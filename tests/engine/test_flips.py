"""B7 golden tests - flip boundaries (PLAN 7.6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.contracts import CandidateInputs, DispatchRequest, LatLon
from app.contracts.inputs import Costs, PriceBand, RouteOption, Waypoint
from app.engine.commit import closing_deadline, last_feasible_waypoint
from app.engine.flips import compute_flips
from app.engine.netvalue import net_value
from app.engine.scenarios import evaluate_option

IST = timezone(timedelta(hours=5, minutes=30))
FIXTURE_DEPART = datetime(2026, 10, 31, 2, 0, tzinfo=IST)
FIXTURE_CFG = Settings()


def FIXTURE_request(rate: float = 30.0) -> DispatchRequest:
    costs = Costs(transport_mode="per_km_per_trip", transport_rate=rate, commission_pct=6.0,
                  market_fee_pct=1.0, hamali_per_qtl=10.0, hold_cost_per_hour=100.0)
    return DispatchRequest(quantity_qtl=50, origin=LatLon(lat=13.1, lon=78.1), origin_label="FIXTURE_farm",
                           origin_state="Karnataka", vehicle="covered", loading_time=FIXTURE_DEPART,
                           preferred_departure=FIXTURE_DEPART, costs=costs)


def FIXTURE_route(rid: str, km: float, hours: float, to_b_s: float = 3600.0) -> RouteOption:
    wps = [Waypoint(idx=i, point=LatLon(lat=13.0, lon=78.0 + i / 10), elapsed_nominal_s=i * hours * 1800.0,
                    to_dest_s={"FIXTURE_mB": to_b_s * (1 + i)}) for i in range(3)]
    return RouteOption(route_id=rid, distance_km=km, duration_h_nominal=hours, waypoints=wps)


def FIXTURE_cand(mid: str, typical: float, closing: str | None) -> CandidateInputs:
    return CandidateInputs(mandi_id=mid, mandi_name=mid, location=LatLon(lat=13.0, lon=78.5),
                           location_precision="market", latest_arrival_local=closing,
                           price=PriceBand(low=typical * 0.8, typical=typical, high=typical * 1.2,
                                           record_date="2026-10-30", unit_verified=True))


def _setup(rate: float = 30.0, b_typical: float = 1120.0, a_closing: str | None = "08:00",
           b_closing: str | None = "07:00"):
    req = FIXTURE_request(rate)
    cand_a = FIXTURE_cand("FIXTURE_mA", 1200.0, a_closing)
    cand_b = FIXTURE_cand("FIXTURE_mB", b_typical, b_closing)
    route_a = FIXTURE_route("FIXTURE_rA", 200, 4.0)
    route_b = FIXTURE_route("FIXTURE_rB", 100, 2.0)
    plan_a = evaluate_option(req, cand_a, route_a, FIXTURE_DEPART, FIXTURE_CFG)
    plan_b = evaluate_option(req, cand_b, route_b, FIXTURE_DEPART, FIXTURE_CFG)
    flips = compute_flips(req, plan_a, plan_b, cand_a, cand_b, route_a, FIXTURE_CFG)
    return req, plan_b, route_a, flips


def _net_b(plan_b) -> float:
    return next(s.net_value for s in plan_b.scenario_results if s.price_level == "typical" and s.delay_level == "none")


def test_price_flip_matches_brute_force():
    req, plan_b, route_a, flips = _setup()
    target = _net_b(plan_b)
    price = 1.0
    while net_value(req, price, route_a.distance_km, 0.0) <= target:
        price += 0.01
    assert flips.price_flip_per_qtl is not None
    assert abs(flips.price_flip_per_qtl - price) < 0.05


def test_delay_flip_found_and_applied_to_a_only():
    # A closes 08:00, nominal arrival 06:00; at delay 0.55 -> 08:12 (missed) so B wins there.
    _, _, _, flips = _setup()
    assert flips.delay_flip_pct == 55.0


def test_none_when_b_never_wins():
    _, _, _, flips = _setup(b_typical=100.0, a_closing=None)
    assert flips.delay_flip_pct is None
    assert flips.diversion_feasible_at_flip is None


def test_diversion_consistent_with_b6():
    req, plan_b, route_a, flips = _setup()
    deadline = closing_deadline(FIXTURE_DEPART, "07:00", FIXTURE_CFG.timezone_name)
    lf = last_feasible_waypoint(FIXTURE_DEPART, route_a, "FIXTURE_mB", deadline, flips.delay_flip_pct / 100)
    assert flips.diversion_feasible_at_flip is (lf is not None)
    _, _, _, unknown = _setup(b_closing=None)
    assert unknown.diversion_feasible_at_flip is None


def test_flip_moves_with_a_transport_cost():
    _, _, _, cheap = _setup(rate=30.0)
    _, _, _, dear = _setup(rate=40.0)
    assert dear.price_flip_per_qtl > cheap.price_flip_per_qtl


def test_note_approximate():
    for kw in ({}, {"b_typical": 100.0, "a_closing": None}, {"b_closing": None}):
        assert "approximate" in _setup(**kw)[3].note
