"""Geocoding with cache + manual verified coordinate table (BLUEPRINT A4).

Order for a mandi:  manual verified CSV / user mandi_settings  ->  geocode_cache
                    -> Nominatim -> Open-Meteo geocoding -> district centroid -> unknown.
Rules
  * NEVER invent coordinates; unresolved => precision "unknown" with no lat/lon.
  * Ambiguity: a hit is discarded unless its state matches AND (for market-level) its district matches;
    results outside the India bounding box are discarded.
  * Nominatim policy (NN-13): <= 1 req/s, identifying User-Agent WITH contact (MG_CONTACT_EMAIL; if unset
    Nominatim is skipped, not called anonymously), everything cached.
  * Cache: "market" precision never expires; district-centroid / unknown results stored under a MARKET key
    expire after 7 days so a better market-level hit can be found later. Network errors are never cached.
"""
from __future__ import annotations

import csv
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.config import env
from app.data import http
from app.data.geo import REF_DIR, bracket_hint, canonical_state, clean_market_name, in_india, mandi_id, norm, same_state, state_in_text
from app.db.db import get_conn, get_mandi_setting

log = logging.getLogger("mandiguard.geocode")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NEGATIVE_TTL_S = 7 * 24 * 3600
GEOCODE_NET_BUDGET = 40         # max network-resolved lookups in one shortlist() call (1 req/s policy)
Precision = Literal["market", "district_centroid", "unknown"]


class GeoResult(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    precision: Precision = "unknown"
    display_name: str = ""
    source: str = ""
    cached: bool = False
    had_error: bool = False     # a network/HTTP error occurred while resolving (do not treat as "not found")

    @property
    def resolved(self) -> bool:
        return self.lat is not None and self.lon is not None and self.precision != "unknown"


UNKNOWN = GeoResult(precision="unknown", source="none")


def _word_in(needle: str, haystack_norm: str) -> bool:
    n = norm(needle)
    return bool(n) and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", haystack_norm) is not None


# ---------------- manual verified table ----------------
def _coords_csv_path() -> Path:
    return Path(env("MG_COORDS_CSV") or REF_DIR / "mandi_coords.csv")


@lru_cache(maxsize=4)
def _read_manual_csv(path_str: str, mtime: float) -> dict[str, GeoResult]:
    out: dict[str, GeoResult] = {}
    p = Path(path_str)
    if not p.is_file():
        return out
    with p.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            if str(row.get("verified", "")).strip().lower() not in ("1", "true", "yes", "y"):
                continue                                     # verified rows ONLY (BLUEPRINT A4)
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                log.warning("mandi_coords.csv line %d: bad lat/lon -> ignored", i)
                continue
            if not in_india(lat, lon):
                log.warning("mandi_coords.csv line %d: (%s,%s) outside India (swapped lat/lon?) -> ignored", i, lat, lon)
                continue
            mid = (row.get("mandi_id") or "").strip() or mandi_id(row.get("state", ""), row.get("district", ""), row.get("market", ""))
            prec: Precision = "district_centroid" if (row.get("precision") or "").strip() == "district_centroid" else "market"
            out[mid] = GeoResult(lat=lat, lon=lon, precision=prec,
                                 display_name=f"{row.get('market','')}, {row.get('district','')}, {row.get('state','')}",
                                 source="manual_csv")
    return out


def _manual_lookup(mid: str) -> GeoResult | None:
    p = _coords_csv_path()
    mtime = p.stat().st_mtime if p.is_file() else 0.0
    r = _read_manual_csv(str(p), mtime).get(mid)
    return r.model_copy(update={"cached": True}) if r else None


def _settings_lookup(mid: str) -> GeoResult | None:
    s = get_mandi_setting(mid)
    if s and s.get("lat") is not None and s.get("lon") is not None and in_india(s["lat"], s["lon"]):
        prec: Precision = "district_centroid" if s.get("precision") == "district_centroid" else "market"
        return GeoResult(lat=s["lat"], lon=s["lon"], precision=prec, display_name=s.get("name") or mid,
                         source="mandi_settings", cached=True)
    return None


# ---------------- geocode_cache ----------------
def _key_m(market: str, district: str, state: str) -> str:
    return f"m|{norm(canonical_state(state))}|{norm(district)}|{norm(clean_market_name(market))}"


def _key_d(district: str, state: str) -> str:
    return f"d|{norm(canonical_state(state))}|{norm(district)}"


def _cache_get(key: str) -> GeoResult | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM geocode_cache WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    prec = r["precision"]
    permanent = prec == "market" or (prec == "district_centroid" and key.startswith("d|"))
    if not permanent:
        try:
            from datetime import datetime
            age = (http.utcnow() - datetime.fromisoformat(r["fetched_at"])).total_seconds()
        except (ValueError, TypeError):
            return None
        if age > NEGATIVE_TTL_S:
            return None
    return GeoResult(lat=r["lat"], lon=r["lon"], precision=prec,  # type: ignore[arg-type]
                     display_name=r["display_name"] or "",
                     source=r["source"] or "cache", cached=True)


def _cache_put(key: str, g: GeoResult) -> None:
    if g.had_error and not g.resolved:
        return                                           # never cache outages as "not found"
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO geocode_cache(key,lat,lon,precision,display_name,source,fetched_at)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (key, g.lat, g.lon, g.precision, g.display_name, g.source, http.iso_utc()))


# ---------------- providers ----------------
def _nominatim(query: str, state: str, district: str, level: str) -> tuple[GeoResult | None, bool]:
    """Returns (valid hit | None, had_error)."""
    contact = env("MG_CONTACT_EMAIL")
    if not contact:
        http.add_warning("Nominatim skipped: set MG_CONTACT_EMAIL (policy requires an identifying User-Agent)")
        return None, False
    res = http.request_json(
        "nominatim", "GET", NOMINATIM_URL,
        {"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1, "countrycodes": "in"},
        rate_limiter=http.get_limiter("nominatim"),
        headers={"User-Agent": f"MandiGuard/0.1 ({contact})", "Accept-Language": "en"})
    if not res.ok:
        return None, True
    if not isinstance(res.data, list):
        return None, True
    for it in res.data:
        g = _validate_nominatim(it, state, district, level)
        if g:
            return g, False
    return None, False


def _validate_nominatim(it: Any, state: str, district: str, level: str) -> GeoResult | None:
    try:
        lat, lon = float(it["lat"]), float(it["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not in_india(lat, lon):
        return None
    display = str(it.get("display_name", ""))
    dn = norm(display)
    addr = it.get("address") or {}
    st = addr.get("state")
    if st:
        if not same_state(st, state):
            return None                                   # foreign/other-state homonym -> discard
    elif not state_in_text(state, display):
        return None
    if not _word_in(district, dn) and not any(norm(addr.get(k)) == norm(district)
                                              for k in ("state_district", "county", "district", "city_district")):
        return None                                       # district must agree (market AND district level)
    return GeoResult(lat=lat, lon=lon, precision="market" if level == "market" else "district_centroid",
                     display_name=display, source="nominatim")


def _open_meteo_geo(name: str, state: str, district: str, level: str) -> tuple[GeoResult | None, bool]:
    res = http.request_json(
        "open_meteo_geocoding", "GET", OPEN_METEO_GEOCODE_URL,
        {"name": name, "count": 10, "language": "en", "format": "json", "countryCode": "IN"},
        rate_limiter=http.get_limiter("open_meteo"))
    if not res.ok or not isinstance(res.data, dict):
        return None, True
    for it in res.data.get("results") or []:
        try:
            lat, lon = float(it["latitude"]), float(it["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_india(lat, lon) or (it.get("country_code") or "IN").upper() != "IN":
            continue
        if not same_state(it.get("admin1"), state):
            continue
        adm2, nm = norm(it.get("admin2")), norm(it.get("name"))
        d = norm(district)
        if level == "market":
            if not (adm2 and (adm2 == d or _word_in(district, adm2) or _word_in(it.get("admin2") or "", d))):
                continue
            nq = norm(name)                                   # the returned PLACE must resemble the market asked for
            if not (nm == nq or _word_in(name, nm) or _word_in(it.get("name") or "", nq)):
                continue
        elif d not in (adm2, nm):
            continue
        disp = ", ".join(x for x in (it.get("name"), it.get("admin2"), it.get("admin1"), "India") if x)
        return GeoResult(lat=lat, lon=lon, precision="market" if level == "market" else "district_centroid",
                         display_name=disp, source="open_meteo_geocoding"), False
    return None, False


# ---------------- public API ----------------
def lookup_local(market: str, district: str, state: str, use_cache: bool = True) -> GeoResult | None:
    """Free lookup (no network): manual CSV -> mandi_settings -> cache. None if nothing local."""
    mid = mandi_id(state, district, market)
    return _manual_lookup(mid) or _settings_lookup(mid) or (_cache_get(_key_m(market, district, state)) if use_cache else None)


def geocode_district(district: str, state: str, allow_network: bool = True) -> GeoResult:
    key = _key_d(district, state)
    hit = _cache_get(key)
    if hit:
        return hit
    if not allow_network:
        return UNKNOWN.model_copy(update={"source": "local-miss"})
    had_err = False
    g, e1 = _nominatim(f"{district} district, {canonical_state(state)}, India", state, district, "district")
    had_err |= e1
    if g is None:
        g, e2 = _open_meteo_geo(district, state, district, "district")
        had_err |= e2
    if g is None:
        out = UNKNOWN.model_copy(update={"source": "not-found", "had_error": had_err})
    else:
        out = g
    _cache_put(key, out)
    return out


def geocode_mandi(market: str, district: str, state: str, allow_network: bool = True,
                  refresh: bool = False) -> GeoResult:
    """BLUEPRINT A4 signature (+ optional allow_network / refresh). Never raises, never invents coordinates.

    Name variants tried in order: the BRACKET hint (Agmarknet puts the real yard there: 'Pune(Moshi)' -> Moshi),
    then the cleaned market name. If only the cleaned name matched and it EQUALS the district name
    ('Pune' in Pune) the hit is the city/district centre, so precision is downgraded to district_centroid.
    refresh=True ignores the geocode cache (manual verified rows still win)."""
    local = lookup_local(market, district, state, use_cache=not refresh)
    if local is not None:
        return local
    if not allow_network:
        return UNKNOWN.model_copy(update={"source": "local-miss"})
    clean = clean_market_name(market)
    hint = bracket_hint(market)
    names = [n for n in (hint, clean) if n]
    had_err = False
    g: GeoResult | None = None
    hit_name = ""
    for n in names:
        g, e = _nominatim(f"{n}, {district}, {canonical_state(state)}, India", state, district, "market")
        had_err |= e
        if g is not None:
            hit_name = n
            break
    if g is None:
        for n in names:
            g, e = _open_meteo_geo(n, state, district, "market")
            had_err |= e
            if g is not None:
                hit_name = n
                break
    if g is not None and hit_name == clean and norm(clean) == norm(district):
        g = g.model_copy(update={"precision": "district_centroid",
                                 "display_name": g.display_name + " [market name equals district: city/district centre, not the yard]"})
    if g is None:
        g = geocode_district(district, state, allow_network=True)
        had_err |= g.had_error
        if g.resolved:
            g = g.model_copy(update={"cached": False})
    if g.resolved is False:
        g = UNKNOWN.model_copy(update={"source": "not-found", "had_error": had_err})
    _cache_put(_key_m(market, district, state), g)
    return g


class DefaultGeocoder:
    """Object form used by mandi.shortlist (tests can inject a fake with the same three methods)."""
    def lookup_local(self, market: str, district: str, state: str) -> GeoResult | None:
        return lookup_local(market, district, state)

    def geocode_district(self, district: str, state: str, allow_network: bool = True) -> GeoResult:
        return geocode_district(district, state, allow_network)

    def geocode_mandi(self, market: str, district: str, state: str, allow_network: bool = True) -> GeoResult:
        return geocode_mandi(market, district, state, allow_network)
