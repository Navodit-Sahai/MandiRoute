"""Source-health strip (BLUEPRINT A7 `source_health`).

`source_health(probe=True)`  -> one tiny cached request per source (TTL 5 min) -> live status.
`source_health(probe=False)` -> passive: last call per source from `source_calls` (no network).
Probe coordinates below are used ONLY to test reachability; they never feed a plan.
Telegram/LLM health belongs to Stream C (integrations).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from app.config import datagov_key, env
from app.contracts import Kind, SourceStatus
from app.data import http
from app.db.db import get_conn

log = logging.getLogger("mandiguard.health")

SOURCE_KINDS: dict[str, Kind] = {
    "datagov_agmarknet": Kind.OBSERVED,
    "open_meteo_forecast": Kind.FORECAST,
    "open_meteo_flood": Kind.PROXY,
    "osrm": Kind.COMPUTED,
    "osrm_fallback": Kind.COMPUTED,
    "nominatim": Kind.COMPUTED,
    "open_meteo_geocoding": Kind.COMPUTED,
}
PROBE_TTL_S = 300.0
_PROBE_LAT, _PROBE_LON = 28.61, 77.21       # reachability probe only


def summarize_calls(calls: list[dict[str, Any]], skip: set[str] | None = None) -> list[SourceStatus]:
    """Aggregate per-source call records (from http.collect) into SourceStatus rows."""
    by: dict[str, list[dict[str, Any]]] = {}
    for c in calls:
        if skip and c["source"] in skip:
            continue
        by.setdefault(c["source"], []).append(c)
    out: list[SourceStatus] = []
    for name in sorted(by):
        cs = by[name]
        ok = all(c["ok"] for c in cs)
        errs = sorted({c["error"] for c in cs if c.get("error")})
        lat = [c["latency_ms"] for c in cs if c.get("latency_ms")]
        notes = []
        if errs:
            notes.append("; ".join(errs)[:300])
        if any(c.get("stale") for c in cs):
            notes.append("served from EXPIRED cache")
        n_cache = sum(1 for c in cs if c.get("from_cache"))
        notes.append(f"{len(cs)} call(s), {n_cache} from cache")
        out.append(SourceStatus(
            name=name, ok=ok, http=next((c["http"] for c in reversed(cs) if c.get("http")), None),
            latency_ms=max(lat) if lat else None, as_of=None,
            fetched_at=max((c["fetched_at"] for c in cs if c.get("fetched_at")), default=None),
            kind=SOURCE_KINDS.get(name, Kind.COMPUTED), note=" | ".join(notes)))
    return out


def _newest_tomato_record_date() -> str | None:
    try:
        with get_conn() as c:
            r = c.execute("SELECT MAX(arrival_date) AS d FROM price_records WHERE lower(commodity)='tomato'").fetchone()
        return r["d"] if r and r["d"] else None
    except Exception as e:           # health must never crash the UI
        log.warning("health: price_records query failed: %s", e)
        return None


def _probe(name: str, url: str, params: dict[str, Any], limiter: str | None, check: Callable[[Any], str | None],
           headers: dict[str, str] | None = None, cache_table: str = "http_cache") -> SourceStatus:
    res = http.request_json(name, "GET", url, params, ttl=PROBE_TTL_S, cache_key=f"health|{name}", headers=headers,
                            rate_limiter=http.get_limiter(limiter) if limiter else None, check=check,
                            cache_table=cache_table, retries=0, timeout=10.0)
    return SourceStatus(name=name, ok=res.ok, http=res.http, latency_ms=res.latency_ms,
                        fetched_at=res.fetched_at, kind=SOURCE_KINDS[name],
                        note=(None if res.ok else res.error) or ("cached probe" if res.from_cache else None))


def _passive() -> list[SourceStatus]:
    out: list[SourceStatus] = []
    with get_conn() as c:
        for name, kind in SOURCE_KINDS.items():
            r = c.execute("SELECT ts,http,latency_ms,ok,note FROM source_calls WHERE source=? ORDER BY id DESC LIMIT 1",
                          (name,)).fetchone()
            if r is None:
                out.append(SourceStatus(name=name, ok=False, kind=kind, note="no calls yet"))
            else:
                out.append(SourceStatus(name=name, ok=bool(r["ok"]), http=r["http"], latency_ms=r["latency_ms"],
                                        fetched_at=r["ts"], kind=kind, note=r["note"]))
    return out


def source_health(probe: bool = True) -> list[SourceStatus]:
    rows = _probe_all() if probe else _passive()
    newest = _newest_tomato_record_date()
    for s in rows:
        if s.name == "datagov_agmarknet":
            s.as_of = s.as_of or newest
            s.note = ((s.note + " | ") if s.note else "") + "daily records, NOT real-time; as_of = newest tomato record seen"
    return rows


def _probe_all() -> list[SourceStatus]:
    from app.data import geocode, mandi, routing, weather
    ok_obj = lambda d: None if isinstance(d, (dict, list)) else "unexpected body"      # noqa: E731
    out: list[SourceStatus] = []

    key = datagov_key()
    if not key:
        out.append(SourceStatus(name="datagov_agmarknet", ok=False, kind=Kind.OBSERVED, note="DATAGOV_KEY not set"))
    else:
        def chk(d: Any) -> str | None:
            return None if isinstance(d, dict) and isinstance(d.get("records"), list) else "no 'records' list in response"
        s = _probe("datagov_agmarknet", mandi.RESOURCE_URL,
                   {"api-key": key, "format": "json", "limit": 1, "filters[commodity]": "Tomato"}, "datagov", chk)
        out.append(s)
    out.append(_probe("open_meteo_forecast", weather.FORECAST_URL,
                      {"latitude": _PROBE_LAT, "longitude": _PROBE_LON, "hourly": "temperature_2m", "forecast_days": 1},
                      "open_meteo", lambda d: None if isinstance(d, dict) and "hourly" in d else "no hourly block",
                      cache_table="weather_cache"))
    out.append(_probe("open_meteo_flood", weather.FLOOD_URL,
                      {"latitude": _PROBE_LAT, "longitude": _PROBE_LON, "daily": "river_discharge", "forecast_days": 1},
                      "open_meteo", lambda d: None if isinstance(d, dict) and "daily" in d else "no daily block",
                      cache_table="weather_cache"))
    out.append(_probe("osrm", f"{routing.OSRM_PRIMARY[1]}/nearest/v1/driving/{_PROBE_LON},{_PROBE_LAT}", {}, "osrm",
                      routing._osrm_check, cache_table="route_cache"))
    out.append(_probe("open_meteo_geocoding", geocode.OPEN_METEO_GEOCODE_URL, {"name": "Delhi", "count": 1}, "open_meteo", ok_obj))
    contact = env("MG_CONTACT_EMAIL")
    if not contact:
        out.append(SourceStatus(name="nominatim", ok=False, kind=Kind.COMPUTED,
                                note="MG_CONTACT_EMAIL not set - Nominatim disabled (policy: identifying User-Agent)"))
    else:
        out.append(_probe("nominatim", "https://nominatim.openstreetmap.org/status.php", {"format": "json"}, "nominatim",
                          ok_obj, headers={"User-Agent": f"MandiGuard/0.1 ({contact})"}))
    return out
