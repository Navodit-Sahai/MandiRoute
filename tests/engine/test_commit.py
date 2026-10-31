"""B6 golden tests - commitment points (PLAN 7.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.contracts import CandidateInputs, DispatchRequest, LatLon, PlanOption
from app.contracts.inputs import Costs, RouteOption, Waypoint
from app.engine.commit import compute_commit_points, last_feasible_waypoint

IST = timezone(timedelta(hours=5, minutes=30))
FIXTURE_DEPART = datetime(2026, 10, 31, 2, 0, tzinfo=IST)
FIXTURE_CFG = Settings()


def FIXTURE_request(closing: dict[str, str] | None = None) -> DispatchRequest:
    costs = Costs(transport_mode="per_km_per_trip", transport_rate=30.0)
    return DispatchRequest(quantity_qtl=50, origin=LatLon(lat=13.1, lon=78.1), origin_label="FIXTURE_farm",
                           origin_state="Karnataka", vehicle="covered", loading_time=FIXTURE_DEPART,
                           preferred_departure=FIXTURE_DEPART, costs=costs,
                           mandi_latest_arrival=closing or {})


def FIXTURE_route() -> RouteOption:
    # 4 waypoints, 1 h apart along A; alternative B gets further away as the truck goes on.
    wps = [Waypoint(idx=i, point=LatLon(lat=13.0, lon=78.0 + i / 10), elapsed_nominal_s=i * 3600.0,
                    to_dest_s={"FIXTURE_mB": 3600.0 * (1 + i)}) for i in range(4)]
    return RouteOption(route_id="FIXTURE_rA", distance_km=200, duration_h_nominal=4.0, waypoints=wps)


def FIXTURE_option(mandi_id: str) -> PlanOption:
    return PlanOption(option_id=f"FIXTURE_{mandi_id}", mandi_id=mandi_id, mandi_name=mandi_id, route_id="r",
                      depart_iso=FIXTURE_DEPART.isoformat(), distance_km=100, duration_h_nominal=2.0,
                      rain_exposure_mm=0.0, scenario_results=[], score=0.0, mean_net=0.0, evidence_ids=[])


def FIXTURE_alt_cand(closing: str | None) -> CandidateInputs:
    return CandidateInputs(mandi_id="FIXTURE_mB", mandi_name="FIXTURE B", location=LatLon(lat=13.0, lon=78.5),
                           location_precision="market", latest_arrival_local=closing)


def _commit(closing: str | None, req_closing: dict[str, str] | None = None):
    return compute_commit_points(FIXTURE_request(req_closing), FIXTURE_option("FIXTURE_mA"), FIXTURE_route(),
                                 FIXTURE_option("FIXTURE_mB"), FIXTURE_alt_cand(closing), FIXTURE_CFG)


def test_unknown_closing_not_computed():
    info = _commit(None)
    assert info.computed is False
    assert info.last_feasible_waypoint == {}
    assert "approximate" in info.note


def test_closing_from_request_is_used():
    assert _commit(None, {"FIXTURE_mB": "09:00"}).computed is True


def test_waypoint_zero_vs_last():
    # nominal eta from wp i = 02:00 + 2i+1 h -> wp0 03:00 ... wp3 09:00
    assert _commit("09:00").last_feasible_waypoint["none"] == 3     # every waypoint works
    assert _commit("03:00").last_feasible_waypoint["none"] == 0     # only the start works
    assert _commit("02:30").last_feasible_waypoint["none"] is None  # never feasible


def test_monotone_in_delay():
    route = FIXTURE_route()
    for closing_h in range(3, 14):
        deadline = FIXTURE_DEPART.replace(hour=closing_h)
        prev = 10**6
        for step in range(0, 21):
            lf = last_feasible_waypoint(FIXTURE_DEPART, route, "FIXTURE_mB", deadline, step * 0.05)
            cur = -1 if lf is None else lf
            assert cur <= prev
            prev = cur


def test_scenarios_reported_and_non_increasing():
    info = _commit("06:00")
    assert set(info.last_feasible_waypoint) == {"none", "moderate", "severe"}
    vals = [-1 if v is None else v for v in (info.last_feasible_waypoint[k] for k in ("none", "moderate", "severe"))]
    assert vals == sorted(vals, reverse=True)


def test_note_always_approximate():
    for closing in (None, "02:30", "06:00", "23:00"):
        assert "approximate" in _commit(closing).note
