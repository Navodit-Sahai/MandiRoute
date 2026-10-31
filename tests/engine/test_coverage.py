"""B8 golden tests - coverage grader (PLAN 7.8)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.contracts import CandidateInputs, DispatchRequest, Kind, LatLon
from app.contracts.inputs import Costs, PriceBand, RouteOption, Waypoint, WeatherSample
from app.engine.coverage import grade_coverage

IST = timezone(timedelta(hours=5, minutes=30))
FIXTURE_NOW = datetime(2026, 10, 31, 9, 0, tzinfo=IST)
FIXTURE_CFG = Settings()


def FIXTURE_request(**over) -> DispatchRequest:
    costs = Costs(transport_mode="per_km_per_trip", transport_rate=30.0,
                  commission_pct=6.0, market_fee_pct=1.0, hamali_per_qtl=10.0)
    data = dict(quantity_qtl=50, origin=LatLon(lat=13.1, lon=78.1), origin_label="FIXTURE_farm",
                origin_state="Karnataka", vehicle="covered", loading_time=FIXTURE_NOW,
                preferred_departure=FIXTURE_NOW, costs=costs,
                mandi_latest_arrival={"FIXTURE_m1": "06:00"})
    data.update(over)
    return DispatchRequest(**data)


def FIXTURE_route(precip: float | None = 0.0) -> RouteOption:
    wps = [Waypoint(idx=i, point=LatLon(lat=13.0, lon=78.0 + i / 10), elapsed_nominal_s=i * 3600.0)
           for i in range(2)]
    wx = [WeatherSample(waypoint_idx=i, time_iso="2026-10-31T10:00", temp_c=24.0,
                        precip_mm=precip, evidence_id=f"ev_fx00000{i}") for i in range(2)]
    return RouteOption(route_id="FIXTURE_r1", distance_km=100, duration_h_nominal=2.0,
                       waypoints=wps, weather=wx)


def FIXTURE_cand(age_days: int = 0, precision: str = "market", closing: str | None = "06:00",
                 unit_verified: bool = True, precip: float | None = 0.0,
                 priced: bool = True) -> CandidateInputs:
    price = None
    if priced:
        price = PriceBand(low=800, typical=1000, high=1200, unit_verified=unit_verified,
                          record_date=(FIXTURE_NOW.date() - timedelta(days=age_days)).isoformat())
    return CandidateInputs(mandi_id="FIXTURE_m1", mandi_name="FIXTURE Mandi", location=LatLon(lat=13.0, lon=78.2),
                           location_precision=precision, price=price, routes=[FIXTURE_route(precip)],
                           latest_arrival_local=closing)


def _grade(req=None, cands=None):
    return grade_coverage(req or FIXTURE_request(), cands or [FIXTURE_cand()], [], FIXTURE_NOW, FIXTURE_CFG)


def test_all_ok_actionable():
    rep = _grade()
    assert rep.grade == "ACTIONABLE"
    assert all(i.status == "ok" for i in rep.items)


def test_closing_unknown_with_caveats():
    rep = _grade(req=FIXTURE_request(mandi_latest_arrival={}), cands=[FIXTURE_cand(closing=None)])
    assert rep.grade == "ACTIONABLE_WITH_CAVEATS"
    assert {i.dimension: i.status for i in rep.items}["closing_time"] == "missing"


def test_three_caveats_advisory_only():
    cand = FIXTURE_cand(age_days=FIXTURE_CFG.price_degrade_days + 1, unit_verified=False, precip=None)
    rep = _grade(cands=[cand])
    assert rep.grade == "ADVISORY_ONLY"


def test_stale_price_abstain():
    rep = _grade(cands=[FIXTURE_cand(age_days=FIXTURE_CFG.price_abstain_days + 1)])
    assert rep.grade == "ABSTAIN"
    assert rep.reasons


def test_no_priced_candidate_abstain():
    assert _grade(cands=[FIXTURE_cand(priced=False)]).grade == "ABSTAIN"


def test_district_centroid_counted():
    rep = _grade(cands=[FIXTURE_cand(precision="district_centroid")])
    assert rep.grade == "ACTIONABLE_WITH_CAVEATS"
    assert {i.dimension: i.status for i in rep.items}["location_precision"] == "degraded"


def test_items_complete_with_kind_tags_ripening_omitted():
    rep = _grade()
    dims = {i.dimension for i in rep.items}
    assert dims == {"price_freshness", "route_weather", "closing_time", "deductions",
                    "location_precision", "price_unit"}
    assert all(isinstance(i.kind, Kind) and i.detail for i in rep.items)


def test_ripening_dimension_only_when_enabled():
    cfg = Settings(ripening_enabled=True)
    rep = grade_coverage(FIXTURE_request(), [FIXTURE_cand()], [], FIXTURE_NOW, cfg)
    assert "ripening" in {i.dimension for i in rep.items}
