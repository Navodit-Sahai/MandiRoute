"""Candidate assembler (BLUEPRINT A7): the ONLY entry point Stream B/C use for live data.

    assemble_candidates(req, now)                       -> CandidateAssembly
    refresh_for_monitoring(req, plan_option_ids, now)   -> CandidateAssembly   (watchdog)
    source_health()                                     -> list[SourceStatus]  (re-exported from health.py)

Degradation rules (AGENT 4.2, NN-1, NN-10) -- never substitute fake data:
  failed price -> candidate dropped (warning) | failed weather -> route.weather = [] (flagged) |
  failed route -> candidate kept with routes=[] (coverage/B decide) | unknown location -> candidate dropped.

CONVENTION (ASSUMPTION, needs Stream B/C agreement): `option_id` / `route_id` strings START WITH the
mandi_id, followed by '::' or '~'  (e.g. 'maharashtra__nashik__x::<route>::<depart>'). refresh_for_monitoring
extracts the mandi from that prefix because the contract does not otherwise expose it to Stream A.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Callable

from pydantic import BaseModel

from app.config import datagov_key, get_settings
from app.contracts import CandidateInputs, DispatchRequest, Evidence, Kind, LatLon, SourceStatus
from app.data import http, mandi, routing, weather
from app.data.geocode import DefaultGeocoder
from app.data.health import SOURCE_KINDS, source_health, summarize_calls   # noqa: F401  (re-export)
from app.data.geo import states_for, today_ist
from app.db.db import get_mandi_setting

log = logging.getLogger("mandiguard.assemble")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Progress = Callable[[str], None]


class CandidateAssembly(BaseModel):
    candidates: list[CandidateInputs]
    evidence: list[Evidence]          # every number that appears in candidates (superset: also dropped routes)
    source_status: list[SourceStatus]
    warnings: list[str]
    selected_variety: str | None
    selected_grade: str | None


def mandi_ids_from_option_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    for x in ids:
        m = re.split(r"::|~", x, maxsplit=1)[0].strip()
        if m and m not in out:
            out.append(m)
    return out


def _closing_time(req: DispatchRequest, mid: str) -> str | None:
    raw = req.mandi_latest_arrival.get(mid)
    if raw is None:
        s = get_mandi_setting(mid)
        raw = s.get("latest_arrival_local") if s else None
    if raw is None:
        return None
    if _HHMM.match(raw.strip()):
        return raw.strip()
    http.add_warning(f"{mid}: closing time {raw!r} is not HH:MM -> treated as UNKNOWN")
    return None


def _empty(col: http.Collector, extra: list[SourceStatus], v: str | None = None, g: str | None = None) -> CandidateAssembly:
    return _finish(col, [], extra, v, g)


def _finish(col: http.Collector, cands: list[CandidateInputs], extra: list[SourceStatus],
            v: str | None, g: str | None) -> CandidateAssembly:
    ev: list[Evidence] = []
    seen: set[str] = set()
    for e in col.evidence:
        if e.id not in seen:
            seen.add(e.id)
            ev.append(e)
    status = extra + summarize_calls(col.calls, skip={s.name for s in extra})
    return CandidateAssembly(candidates=cands, evidence=ev, source_status=status, warnings=list(col.warnings),
                             selected_variety=v, selected_grade=g)


def _note(progress: Progress | None, msg: str) -> None:
    if progress:
        try:
            progress(msg)
        except Exception:        # a broken UI callback must not break assembly
            log.exception("progress callback failed")


def _assemble(req: DispatchRequest, now: datetime, *, only: list[str] | None = None, variety: str | None = None,
              grade: str | None = None, force: bool = False, geocoder: object | None = None,
              progress: Progress | None = None, include_flood: bool = True) -> CandidateAssembly:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cfg = get_settings()
    geo = geocoder or DefaultGeocoder()
    with http.collect() as col:
        key = datagov_key()
        if not key:
            http.add_warning("DATAGOV_KEY is not set - cannot fetch mandi prices (no fallback data is used)")
            return _empty(col, [SourceStatus(name=mandi.SOURCE, ok=False, kind=Kind.OBSERVED, note="DATAGOV_KEY not set")])

        # ---- 1. prices ----
        _note(progress, "Fetching mandi price records")
        mf = mandi.fetch_mandi_records(req.crop, states_for(req.origin_state), key, now, force=force)
        for w in mf.warnings:
            http.add_warning(w)
        extra = [mf.status]
        valid = [r for r in mf.records if not r.suspect]
        fresh: list[mandi.MandiRecord] = []
        for rec in valid:
            age = mandi.age_days(rec, now)
            if age is not None and 0 <= age <= cfg.price_abstain_days:
                fresh.append(rec)
        if len(fresh) < len(valid):
            http.add_warning(f"{len(valid) - len(fresh)} price records older than {cfg.price_abstain_days} days were ignored")
        if not fresh:
            http.add_warning("no usable (recent, non-suspect) price records -> nothing to plan with")
            return _empty(col, extra)
        newest = max(rec.arrival_date for rec in fresh if rec.arrival_date is not None)
        newest_age = (today_ist(now) - newest).days
        if newest_age > cfg.price_degrade_days:
            http.add_warning(f"newest price record is {newest_age} days old (record date {newest}); prices are daily records")

        # ---- 2. which mandis ----
        _note(progress, "Shortlisting mandis")
        present = {rec.mandi_id for rec in fresh}
        if only is not None:
            ids = [m for m in only if m in present]
            for m in only:
                if m not in present:
                    http.add_warning(f"{m}: no recent price record any more - cannot refresh this mandi")
        else:
            ids = mandi.shortlist(fresh, req.origin, req.max_distance_km, cfg.candidate_k, geo)   # type: ignore[arg-type]
        if not ids:
            http.add_warning("no candidate mandis within range with a located, recent price record")
            return _empty(col, extra)

        # ---- 3. grade (D-A13: grade-only matching; variety is shown, not matched) ----
        pool = [rec for rec in fresh if rec.mandi_id in ids]
        if variety is None and grade is None:
            grade = mandi.select_grade(pool)
            sup = mandi.grade_support(pool)
            http.add_warning("grade chosen for comparability: " + f"{grade or '-'}"
                             + " (" + "; ".join(f"{g or '-'}: {n} mandi(s)" for g, n in sup[:4]) + "); "
                             "variety is NOT matched - it is shown per mandi")
        groups = mandi.group_by_mandi(pool, variety, grade)
        names = {rec.mandi_id: f"{rec.market}, {rec.district}" for rec in pool}      # human-readable names for warnings
        used_varieties = sorted({(groups[m][0].variety or "-") for m in groups})
        if len(used_varieties) > 1:
            http.add_warning("varieties differ across candidates (" + ", ".join(used_varieties)
                             + ") - prices are same-grade but not strictly like-for-like")

        # ---- 4. candidates: location + price + closing time ----
        _note(progress, "Locating mandis")
        cands: list[CandidateInputs] = []
        for mid in ids:
            if mid not in groups:
                http.add_warning(f"{names.get(mid, mid)}: dropped - no record for the selected grade")
                continue
            rep = groups[mid][0]
            try:
                g = geo.geocode_mandi(rep.market, rep.district, rep.state)       # type: ignore[attr-defined]
            except Exception:
                log.exception("geocode failed for %s", mid)
                http.add_warning(f"{names.get(mid, mid)}: dropped - internal geocoding error (see logs)")
                continue
            if not g.resolved:
                http.add_warning(f"{rep.market}: dropped - location unknown (not guessed); add a verified row to mandi_coords.csv")
                continue
            if g.precision == "district_centroid":
                http.add_warning(f"{rep.market}: location is the DISTRICT centroid (approximate)")
            price = mandi.build_price_band(groups[mid], now)
            if price is None:
                http.add_warning(f"{names.get(mid, mid)}: dropped - no valid price record")
                continue
            cands.append(CandidateInputs(
                mandi_id=mid, mandi_name=f"{rep.market}, {rep.district}", location=LatLon(lat=g.lat, lon=g.lon),  # type: ignore[arg-type]
                location_precision=g.precision, price=price, routes=[], latest_arrival_local=_closing_time(req, mid)))
        if not cands:
            return _empty(col, extra, variety, grade)

        # ---- 5. routes ----
        _note(progress, "Routing")
        for c in cands:
            try:
                rr = routing.get_routes_ex(req.origin, c.location, True, c.mandi_id)
            except Exception:
                log.exception("routing crashed for %s", c.mandi_id)
                http.add_warning(f"{c.mandi_name}: internal routing error (see logs)")
                continue
            if rr.error:
                http.add_warning(f"{c.mandi_name}: no route ({rr.error})")
            for w in rr.warnings:
                http.add_warning(f"{c.mandi_name}: {w}")
            keep = [rt for rt in rr.routes if rt.distance_km <= req.max_distance_km]
            if len(keep) < len(rr.routes):
                http.add_warning(f"{c.mandi_name}: {len(rr.routes) - len(keep)} route(s) longer than max_distance_km dropped")
            c.routes = keep
        pts = {c.mandi_id: c.location for c in cands}
        for c in cands:
            for rt in c.routes:
                try:
                    err = routing.get_route_table(rt, pts)
                except Exception:
                    log.exception("table crashed for %s", rt.route_id)
                    err = "internal error (see logs)"
                if err:
                    http.add_warning(f"{rt.route_id}: waypoint->mandi times unavailable ({err}); commitment points cannot be computed")

        # ---- 6. weather ----
        _note(progress, "Fetching weather")
        depart = req.preferred_departure
        for c in cands:
            for rt in c.routes:
                try:
                    wr = weather.get_weather_along_ex(rt, depart, now, force=force)
                    rt.weather = wr.samples
                    if wr.error:
                        http.add_warning(f"{rt.route_id}: weather unavailable ({wr.error}) - flagged, not defaulted")
                    if include_flood:
                        fe = weather.get_river_flow_proxy(rt, now, force=force)
                        if fe is not None:
                            rt.evidence_ids.append(fe.id)
                except Exception:
                    log.exception("weather crashed for %s", rt.route_id)
                    http.add_warning(f"{rt.route_id}: internal weather error (see logs)")
            if c.routes:
                dmin = min(rt.duration_h_nominal for rt in c.routes)
                dmax = max(rt.duration_h_nominal for rt in c.routes)
                start = depart + timedelta(hours=dmin)
                end = depart + timedelta(hours=dmax * (1 + cfg.delay_mult.get("severe", 0.0)) + cfg.next_day_wait_h)
                try:
                    c.destination_weather = weather.get_destination_weather(c.location, (start, end), force=force)
                except Exception:
                    log.exception("destination weather crashed for %s", c.mandi_id)
                    http.add_warning(f"{c.mandi_name}: internal destination-weather error (see logs)")
        _note(progress, "Done")
        return _finish(col, cands, extra, variety, grade)


def assemble_candidates(req: DispatchRequest, now: datetime, *, progress: Progress | None = None) -> CandidateAssembly:
    """BLUEPRINT A7. Live data only. Initial call may take ~30-60 s cold (1 req/s OSRM/Nominatim policy)."""
    return _assemble(req, now, progress=progress)


def refresh_for_monitoring(req: DispatchRequest, plan_option_ids: list[str], now: datetime, *,
                           variety: str | None = None, grade: str | None = None,
                           force: bool = False) -> CandidateAssembly:
    """BLUEPRINT A7 (+ optional keyword args, additive). Re-fetches prices/weather for the mandis named in
    `plan_option_ids` (see CONVENTION above). Pass the grade stored with the plan (variety normally None =
    any) so the comparison basis cannot silently change; None means wildcard when the other is given. Cache TTLs define "fresh" (price 60 min, weather 30 min);
    force=True bypasses the price + weather caches (routes are static map data and stay cached)."""
    only = mandi_ids_from_option_ids(plan_option_ids)
    if not only:
        with http.collect() as col:
            http.add_warning("refresh_for_monitoring: no mandi ids could be read from plan_option_ids")
            return _empty(col, [])
    return _assemble(req, now, only=only, variety=variety, grade=grade, force=force)
