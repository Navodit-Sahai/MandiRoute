"""Mandi price ingestion & shortlist (BLUEPRINT A3).

Source: data.gov.in Agmarknet daily price resource. Daily records -- NEVER called real-time (NN-3).
Price band = min/modal/max of ONE observed record, labelled "reported", NOT a forecast (NN-4).

UNKNOWN until gate A1 runs live (this code defends against each):
  * exact server-side filter key for state ("state.keyword" vs "state") and state spelling -> tried in turn,
    and every record is re-checked client-side (commodity + state), so an ignored filter cannot leak data;
  * record field spelling (older dumps use Min_x0020_Price etc.) -> alias map;
  * price unit (Rs/quintal) -> unit_verified=False unless MG_PRICE_UNIT_VERIFIED=1 (NN-15).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional, Protocol

from pydantic import BaseModel

from app.config import env, get_settings, price_unit_verified
from app.contracts import Kind, LatLon, PriceBand, SourceStatus
from app.data import http
from app.data.geo import canonical_state, haversine_km, mandi_id, norm, same_state, today_ist
from app.db.db import get_conn

if TYPE_CHECKING:
    from app.data.geocode import GeoResult

log = logging.getLogger("mandiguard.mandi")

SOURCE = "datagov_agmarknet"
RESOURCE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
PAGE_LIMIT = 500
MAX_PAGES = 4
_STATE_FILTER_STYLES = ("state.keyword", "state")
# API spellings that differ from our canonical names (UNVERIFIED; tried in order until records appear)
_STATE_SPELLINGS = {
    "Chhattisgarh": ["Chhattisgarh", "Chattisgarh"],
    "Delhi": ["NCT of Delhi", "Delhi"],
    "Uttarakhand": ["Uttarakhand", "Uttrakhand"],
    "Puducherry": ["Puducherry", "Pondicherry"],
}
_FIELD_ALIASES = {"min_x0020_price": "min_price", "max_x0020_price": "max_price",
                  "modal_x0020_price": "modal_price", "arrival_date_": "arrival_date"}
_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%y")
_sticky_filter_style: str | None = None


# ---------------- models ----------------
class MandiRecord(BaseModel):
    mandi_id: str
    state: str
    district: str
    market: str
    commodity: str
    variety: Optional[str] = None
    grade: Optional[str] = None
    arrival_date: Optional[date] = None
    min_price: Optional[float] = None
    modal_price: Optional[float] = None
    max_price: Optional[float] = None
    suspect: bool = False
    suspect_reason: Optional[str] = None
    served_stale: bool = False        # True: came from an EXPIRED cache copy because the live fetch failed
    fetched_at: str = ""
    raw: dict[str, Any] = {}


class MandiFetch(BaseModel):
    records: list[MandiRecord]
    status: SourceStatus
    warnings: list[str] = []


class _GeocoderLike(Protocol):
    def lookup_local(self, market: str, district: str, state: str) -> "GeoResult | None": ...
    def geocode_district(self, district: str, state: str, allow_network: bool = True) -> "GeoResult": ...


# ---------------- normalisation ----------------
def _key(k: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_")
    return _FIELD_ALIASES.get(k, k)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s.upper() in ("NA", "N/A", "NULL", "NAN", "-"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def parse_date(v: Any) -> date | None:
    s = str(v or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_record(raw: dict[str, Any], fetched_at: str, today: date) -> MandiRecord | None:
    """Lowercase keys, parse date/prices, flag suspect rows. None if identity fields are missing."""
    r = {_key(k): v for k, v in raw.items()}
    state, district, market, commodity = (str(r.get(f) or "").strip() for f in ("state", "district", "market", "commodity"))
    if not (state and district and market and commodity):
        return None
    d = parse_date(r.get("arrival_date"))
    lo, mo, hi = _to_float(r.get("min_price")), _to_float(r.get("modal_price")), _to_float(r.get("max_price"))
    reason = None
    if d is None:
        reason = "arrival_date missing/unparseable"
    elif d > today:
        reason = f"arrival_date {d} is in the future"
    elif lo is None or mo is None or hi is None:
        reason = "a price is missing/non-numeric"
    elif min(lo, mo, hi) <= 0:
        reason = "price <= 0"
    elif not (lo <= mo <= hi):
        reason = "violates min <= modal <= max"
    variety = str(r.get("variety") or "").strip() or None
    grade = str(r.get("grade") or "").strip() or None
    return MandiRecord(
        mandi_id=mandi_id(state, district, market), state=state, district=district, market=market,
        commodity=commodity, variety=variety, grade=grade, arrival_date=d, min_price=lo, modal_price=mo,
        max_price=hi, suspect=reason is not None, suspect_reason=reason, fetched_at=fetched_at, raw=raw)


def age_days(rec: MandiRecord, now: datetime) -> int | None:
    return None if rec.arrival_date is None else (today_ist(now) - rec.arrival_date).days


# ---------------- fetching ----------------
def _filter_styles() -> list[str]:
    first = env("DATAGOV_STATE_FILTER")
    order = [first] if first else []
    if _sticky_filter_style:
        order.insert(0, _sticky_filter_style)
    order += list(_STATE_FILTER_STYLES)
    seen: list[str] = []
    for s in order:
        if s and s not in seen:
            seen.append(s)
    return seen


def _check_shape(data: Any) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        keys = sorted(data.keys())[:6] if isinstance(data, dict) else type(data).__name__
        return f"unexpected response shape (no 'records' list; got {keys})"
    return None


def _fetch_pages(commodity: str, state_value: str, style: str, key: str, force: bool) -> tuple[list[dict], http.HttpResult | None, str | None]:
    """Follow pagination using the rows actually returned (the API may cap page size below PAGE_LIMIT)."""
    s = get_settings()
    out: list[dict] = []
    last: http.HttpResult | None = None
    offset = 0
    for _ in range(MAX_PAGES):
        params = {"api-key": key, "format": "json", "limit": PAGE_LIMIT, "offset": offset,
                  f"filters[{style}]": state_value, "filters[commodity]": commodity}
        res = http.request_json(
            SOURCE, "GET", RESOURCE_URL, params, ttl=s.cache_ttl_s["mandi"],
            rate_limiter=http.get_limiter("datagov"),
            cache_key=f"datagov|{style}|{state_value}|{commodity}|{offset}|{PAGE_LIMIT}",
            max_stale_s=s.price_abstain_days * 86400, force=force, check=_check_shape)
        last = res
        if not res.ok:
            return out, res, res.error
        recs = [x for x in res.data["records"] if isinstance(x, dict)]
        out.extend(recs)
        offset += len(recs)
        try:
            total = int(res.data.get("total"))
        except (TypeError, ValueError):
            total = None
        if not recs or (total is not None and offset >= total) or (total is None and len(recs) < PAGE_LIMIT):
            return out, res, None
    http.add_warning(f"{state_value}: stopped after {MAX_PAGES} pages ({len(out)} rows) - list may be truncated")
    return out, last, None


def _persist(records: list[MandiRecord]) -> None:
    unit_ok = price_unit_verified()
    with get_conn() as c:
        c.executemany(
            "INSERT INTO price_records(fetched_at,state,district,market,commodity,variety,grade,arrival_date,"
            "min_price,max_price,modal_price,unit,unit_verified,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.fetched_at, r.state, r.district, r.market, r.commodity, r.variety, r.grade,
              r.arrival_date.isoformat() if r.arrival_date else None, r.min_price, r.max_price, r.modal_price,
              "INR/quintal" if unit_ok else "UNVERIFIED", int(unit_ok), _json(r.raw)) for r in records])


def _json(o: Any) -> str:
    import json
    return json.dumps(o, ensure_ascii=False, default=str)


def fetch_mandi_records(commodity: str, states: list[str], key: str, now: datetime, *, force: bool = False) -> MandiFetch:
    """Live fetch + normalise. Failure -> empty records + SourceStatus(ok=False). Never raises on HTTP errors.

    Records are persisted to `price_records` on every NON-cached fetch (monitoring history)."""
    global _sticky_filter_style
    today = today_ist(now)
    warnings: list[str] = []
    records: list[MandiRecord] = []
    failed: list[str] = []
    http_codes: list[int] = []
    latencies: list[int] = []
    fetched: list[str] = []
    n_dropped_commodity = n_dropped_state = n_unidentified = 0

    for st in states:
        canon = canonical_state(st)
        raw_rows: list[dict] = []
        res: http.HttpResult | None = None
        err: str | None = None
        attempts = 0
        for value in _STATE_SPELLINGS.get(canon, [canon]):
            for style in _filter_styles():
                attempts += 1
                raw_rows, res, err = _fetch_pages(commodity, value, style, key, force)
                if err or raw_rows:
                    if raw_rows and not err and style != _sticky_filter_style:
                        _sticky_filter_style = style
                    break
                if attempts >= 4:
                    break
            if err or raw_rows or attempts >= 4:
                break
        if res is not None:
            if res.http is not None:
                http_codes.append(res.http)
            if res.latency_ms is not None:
                latencies.append(res.latency_ms)
            if res.fetched_at:
                fetched.append(res.fetched_at)
        if err:
            failed.append(f"{canon}: {err}")
            continue
        if res is not None and res.stale:
            warnings.append(f"{canon}: live fetch failed, using CACHED data from {res.fetched_at}")
        fetched_at = res.fetched_at if res else http.iso_utc()
        batch: list[MandiRecord] = []
        for raw in raw_rows:
            rec = normalize_record(raw, fetched_at, today)
            if rec is not None and res is not None and res.stale:
                rec.served_stale = True
            if rec is None:
                n_unidentified += 1
            elif norm(rec.commodity) != norm(commodity):
                n_dropped_commodity += 1
            elif not same_state(rec.state, canon):
                n_dropped_state += 1
            else:
                batch.append(rec)
        records.extend(batch)
        if batch and res is not None and not res.from_cache:
            _persist(batch)

    if n_unidentified:
        warnings.append(f"{n_unidentified} rows lacked state/district/market/commodity and were skipped")
    if n_dropped_commodity or n_dropped_state:
        warnings.append(f"server-side filter did not fully apply: dropped {n_dropped_commodity} other-commodity and "
                        f"{n_dropped_state} other-state rows client-side")
    n_suspect = sum(r.suspect for r in records)
    if n_suspect:
        warnings.append(f"{n_suspect} suspect price records excluded (e.g. min<=modal<=max violated)")
    for f in failed:
        warnings.append(f"mandi price fetch failed - {f}")

    dated = [r.arrival_date for r in records if r.arrival_date and not r.suspect]
    status = SourceStatus(
        name=SOURCE, ok=not failed, http=(http_codes[-1] if http_codes else None),
        latency_ms=(max(latencies) if latencies else None),
        as_of=(max(dated).isoformat() if dated else None), fetched_at=(max(fetched) if fetched else None),
        kind=Kind.OBSERVED,
        note=("; ".join(failed) if failed else f"{len(records)} records (daily records, not real-time)"))
    return MandiFetch(records=records, status=status, warnings=warnings)


# ---------------- selection rule / grouping ----------------
def _vg(rec: MandiRecord) -> tuple[str, str]:
    return (norm(rec.variety), norm(rec.grade))


def select_variety_grade(records: list[MandiRecord]) -> tuple[str | None, str | None]:
    """(Strict matching - NOT used by the assembler since D-A13; kept for tests/optional use.) Pair (variety, grade) present in the MOST distinct mandis; tie -> alphabetical. Deterministic.
    Only non-suspect records count. Returns the original spelling (smallest spelling for that pair)."""
    pairs: dict[tuple[str, str], set[str]] = {}
    spelling: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for r in sorted((x for x in records if not x.suspect), key=lambda x: (x.mandi_id, x.variety or "", x.grade or "")):
        k = _vg(r)
        pairs.setdefault(k, set()).add(r.mandi_id)
        cur = spelling.get(k)
        cand = (r.variety, r.grade)
        if cur is None or (cand[0] or "", cand[1] or "") < (cur[0] or "", cur[1] or ""):
            spelling[k] = cand
    if not pairs:
        return None, None
    best = sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0][0]
    return spelling[best]


def pair_support(records: list[MandiRecord]) -> list[tuple[str | None, str | None, int]]:
    """For the UI/warnings: every (variety, grade) with its mandi count, best first."""
    out: dict[tuple[str, str], tuple[str | None, str | None, set[str]]] = {}
    for r in records:
        if r.suspect:
            continue
        k = _vg(r)
        out.setdefault(k, (r.variety, r.grade, set()))[2].add(r.mandi_id)
    return [(v, g, len(s)) for _, (v, g, s) in sorted(out.items(), key=lambda kv: (-len(kv[1][2]), kv[0]))]


def select_grade(records: list[MandiRecord]) -> str | None:
    """D-A13: compare on GRADE only. Grade present in the MOST distinct mandis; tie -> alphabetical.
    (Variety labels such as Local/Other/Deshi are used inconsistently across mandis, so variety is NOT matched.)"""
    sup = grade_support(records)
    return sup[0][0] if sup else None


def grade_support(records: list[MandiRecord]) -> list[tuple[str | None, int]]:
    by: dict[str, tuple[str | None, set[str]]] = {}
    for r in sorted((x for x in records if not x.suspect), key=lambda x: (x.mandi_id, x.grade or "")):
        k = norm(r.grade)
        cur = by.get(k)
        if cur is None:
            by[k] = (r.grade, {r.mandi_id})
        else:
            cur[1].add(r.mandi_id)
            if (r.grade or "") < (cur[0] or ""):
                by[k] = (r.grade, cur[1])
    return [(g, len(m)) for _, (g, m) in sorted(by.items(), key=lambda kv: (-len(kv[1][1]), kv[0]))]


def group_by_mandi(records: list[MandiRecord], variety: str | None, grade: str | None) -> dict[str, list[MandiRecord]]:
    """None = wildcard (match any). Pass variety=None to ignore variety (default policy, D-A13)."""
    wv = None if variety is None else norm(variety)
    wg = None if grade is None else norm(grade)
    g: dict[str, list[MandiRecord]] = {}
    for r in records:
        if r.suspect or (wv is not None and norm(r.variety) != wv) or (wg is not None and norm(r.grade) != wg):
            continue
        g.setdefault(r.mandi_id, []).append(r)
    return g


# ---------------- price band ----------------
def build_price_band(record_group: list[MandiRecord], now: datetime) -> PriceBand | None:
    """low=min, typical=modal, high=max of ONE record (the newest). Creates 3 observed Evidence rows."""
    good = [(r.arrival_date, r) for r in record_group if not r.suspect and r.arrival_date is not None]
    if not good:
        return None
    newest = max(d for d, _ in good)
    same_day = sorted((r for d, r in good if d == newest),
                      key=lambda r: (r.min_price or 0.0, r.modal_price or 0.0, r.max_price or 0.0))
    rec = same_day[0]
    if rec.min_price is None or rec.modal_price is None or rec.max_price is None:      # cannot happen (non-suspect)
        return None
    if len(same_day) > 1:
        http.add_warning(f"{rec.market}: {len(same_day)} records on {newest} for the same variety/grade; "
                         f"used the one with the lowest min price (deterministic)")
    s = get_settings()
    age = age_days(rec, now) or 0
    stale = age > s.price_degrade_days or rec.served_stale
    verified = price_unit_verified()
    unit = "INR/quintal" if verified else "INR/quintal (UNIT UNVERIFIED)"
    note = (f"{rec.market}, {rec.district}, {rec.state}; variety={rec.variety}; grade={rec.grade}; "
            f"record age {age} d; observed daily record, NOT a forecast")
    ev_ids = []
    for label, val in (("Low", rec.min_price), ("Typical (modal)", rec.modal_price), ("High", rec.max_price)):
        ev = http.make_evidence(Kind.OBSERVED, f"{label} price reported (record date: {newest.isoformat()})", val, unit,
                                SOURCE, newest.isoformat(), rec.fetched_at or http.iso_utc(), note, stale)
        ev_ids.append(ev.id)
    return PriceBand(low=rec.min_price, typical=rec.modal_price, high=rec.max_price,
                     record_date=newest.isoformat(), variety=rec.variety, grade=rec.grade,
                     unit_verified=verified, evidence_ids=ev_ids)


# ---------------- shortlist ----------------
def shortlist(records: list[MandiRecord], origin: LatLon, max_km: float, k: int, geocoder: _GeocoderLike,
              net_budget: int | None = None) -> list[str]:
    """Straight-line (haversine) prefilter -> top-K mandi_ids, nearest first (ties by id).

    Location source per mandi: market-level (manual CSV / cache), else DISTRICT centroid (network, budgeted
    to respect the 1 req/s Nominatim policy; results are cached forever so coverage grows run by run).
    Mandis whose location cannot be resolved are skipped with a warning (never guessed)."""
    from app.data.geocode import GEOCODE_NET_BUDGET
    budget = GEOCODE_NET_BUDGET if net_budget is None else net_budget
    reps: dict[str, MandiRecord] = {}
    for r in sorted((x for x in records if not x.suspect), key=lambda x: x.mandi_id):
        reps.setdefault(r.mandi_id, r)
    located: list[tuple[float, str]] = []
    unresolved = 0
    dist_cache: dict[tuple[str, str], "GeoResult"] = {}
    for mid, r in sorted(reps.items()):
        g = geocoder.lookup_local(r.market, r.district, r.state)
        if g is None or not g.resolved:
            dk = (norm(r.district), norm(canonical_state(r.state)))
            if dk not in dist_cache:
                allow = budget > 0
                dg = geocoder.geocode_district(r.district, r.state, allow_network=allow)
                if allow and not dg.cached:
                    budget -= 1
                dist_cache[dk] = dg
            g = dist_cache[dk]
        if g is None or not g.resolved:
            unresolved += 1
            continue
        d = haversine_km(origin.lat, origin.lon, g.lat, g.lon)           # type: ignore[arg-type]
        if d <= max_km:
            located.append((d, mid))
    if unresolved:
        http.add_warning(f"{unresolved} mandis could not be located (geocoding budget/lookup) and were skipped; "
                         f"add verified rows to data/reference/mandi_coords.csv (scripts/build_coords.py)")
    located.sort()
    return [mid for _, mid in located[:max(0, k)]]
