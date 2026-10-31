"""
B8 - Evidence coverage grade (PLAN 7.8). Rules only, no percentages.

    ABSTAIN  if no priced candidate, newest price record older than PRICE_ABSTAIN_DAYS,
             or no usable route for any priced candidate.
    Otherwise count caveats (a dimension that is not "ok"):
        0 -> ACTIONABLE | 1-2 -> ACTIONABLE_WITH_CAVEATS | >= 3 -> ADVISORY_ONLY

A dimension is a caveat if ANY priced, routable candidate is affected (the detail names them).
Ripening is only graded while settings.ripening_enabled (SR-1); otherwise it is omitted.
"""
from __future__ import annotations

from datetime import date, datetime

from app.contracts import CandidateInputs, CoverageItem, CoverageReport, DispatchRequest, Evidence, Kind


def _age_days(record_date: str, now: datetime) -> int:
    return (now.date() - date.fromisoformat(record_date[:10])).days


def _usable(cand: CandidateInputs) -> bool:
    return any(r.geometry_ok for r in cand.routes)


def _weather_complete(cand: CandidateInputs) -> bool:
    for route in cand.routes:
        if not route.geometry_ok:
            continue
        covered = {w.waypoint_idx for w in route.weather if w.precip_mm is not None}
        if not route.waypoints or any(wp.idx not in covered for wp in route.waypoints):
            return False
    return True


def _item(dimension: str, kind: Kind, bad: list[str], bad_status: str, ok_detail: str, bad_detail: str) -> CoverageItem:
    if not bad:
        return CoverageItem(dimension=dimension, kind=kind, status="ok", detail=ok_detail)
    return CoverageItem(dimension=dimension, kind=kind, status=bad_status,
                        detail=f"{bad_detail}: {', '.join(bad)}")


def grade_coverage(req: DispatchRequest, cands: list[CandidateInputs], evidence: list[Evidence],
                   now: datetime, cfg) -> CoverageReport:
    priced = [c for c in cands if c.price is not None]
    usable = [c for c in priced if _usable(c)]

    abstain: list[str] = []
    if not priced:
        abstain.append("no candidate mandi has a price record")
    else:
        newest_age = min(_age_days(c.price.record_date, now) for c in priced)
        if newest_age > cfg.price_abstain_days:
            abstain.append(f"newest price record is {newest_age} days old (limit {cfg.price_abstain_days})")
        if not usable:
            abstain.append("no usable route to any priced mandi")
    if abstain:
        return CoverageReport(grade="ABSTAIN", items=[], reasons=abstain)

    stale = [f"{c.mandi_name} ({_age_days(c.price.record_date, now)} d)" for c in usable
             if _age_days(c.price.record_date, now) > cfg.price_degrade_days]
    no_closing = [c.mandi_name for c in usable
                  if req.mandi_latest_arrival.get(c.mandi_id) is None and c.latest_arrival_local is None]
    items = [
        _item("price_freshness", Kind.OBSERVED, stale, "degraded",
              f"price records within {cfg.price_degrade_days} days",
              f"price record older than {cfg.price_degrade_days} days"),
        _item("route_weather", Kind.FORECAST, [c.mandi_name for c in usable if not _weather_complete(c)],
              "degraded", "rain forecast available at every route point", "rain forecast incomplete on route"),
        _item("closing_time", Kind.USER_INPUT, no_closing, "missing",
              "latest arrival time known", "latest arrival time unknown"),
        _item("deductions", Kind.USER_INPUT, [] if req.costs.deductions_known else ["commission/fee/hamali"],
              "missing", "main deductions given", "deductions unknown"),
        _item("location_precision", Kind.COMPUTED,
              [c.mandi_name for c in usable if c.location_precision != "market"], "degraded",
              "mandi located at market level", "location is a district centroid or unknown"),
        _item("price_unit", Kind.OBSERVED, [c.mandi_name for c in usable if not c.price.unit_verified],
              "degraded", "price unit verified (Rs/quintal)", "price unit not verified"),
    ]
    if cfg.ripening_enabled:
        items.append(_item("ripening", Kind.ASSUMPTION, [] if req.ripeness is not None else ["ripeness stage"],
                           "missing", "ripeness stage given", "ripening not assessable"))

    caveats = [i.detail for i in items if i.status != "ok"]
    if not caveats:
        grade = "ACTIONABLE"
    elif len(caveats) <= 2:
        grade = "ACTIONABLE_WITH_CAVEATS"
    else:
        grade = "ADVISORY_ONLY"
    return CoverageReport(grade=grade, items=items, reasons=caveats)
