"""
B5 golden tests - Plan builder A/B/C (BLUEPRINT B5, PLAN 7.4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.contracts import CandidateInputs, DispatchRequest, Evidence, Kind, LatLon, PriceBand, RouteOption, Waypoint, WeatherSample
from app.contracts.inputs import Costs
from app.engine.plans import build_plan_set

IST = timezone(timedelta(hours=5, minutes=30))
FIXTURE_NOW = datetime(2026, 10, 31, 5, 0, tzinfo=IST)
FIXTURE_CFG = Settings()


def FIXTURE_costs(**overrides) -> Costs:
    values = dict(
        transport_mode="per_km_per_trip",
        transport_rate=20.0,
        commission_pct=5.0,
        market_fee_pct=2.0,
        hamali_per_qtl=5.0,
        loading_per_qtl=5.0,
        hold_cost_per_hour=100.0,
    )
    values.update(overrides)
    return Costs(**values)


def FIXTURE_request(**overrides) -> DispatchRequest:
    values = dict(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=13.0, lon=77.5),
        origin_label="FIXTURE_farm",
        origin_state="Karnataka",
        vehicle="covered",
        loading_time=FIXTURE_NOW,
        preferred_departure=FIXTURE_NOW,
        max_distance_km=400.0,
        costs=FIXTURE_costs(),
        mandi_latest_arrival={},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )
    values.update(overrides)
    return DispatchRequest(**values)


def FIXTURE_route(
    route_id: str = "FIXTURE_m1~r0",
    distance_km: float = 100.0,
    duration_h_nominal: float = 2.0,
) -> RouteOption:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=13.0, lon=77.5), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=13.5, lon=78.0), elapsed_nominal_s=duration_h_nominal * 3600.0),
    ]
    return RouteOption(
        route_id=route_id,
        distance_km=distance_km,
        duration_h_nominal=duration_h_nominal,
        waypoints=wps,
        weather=[],
        geometry_ok=True,
        evidence_ids=["ev_route"],
    )


def FIXTURE_cand(
    mandi_id: str,
    mandi_name: str,
    low: float,
    typical: float,
    high: float,
    distance_km: float = 100.0,
    duration_h: float = 2.0,
    closing: str | None = "14:00",
    priced: bool = True,
) -> CandidateInputs:
    price = None
    if priced:
        price = PriceBand(
            low=low,
            typical=typical,
            high=high,
            record_date="2026-10-31",
            unit_verified=True,
            evidence_ids=[f"ev_p_{mandi_id}"],
        )
    route = FIXTURE_route(route_id=f"{mandi_id}~r0", distance_km=distance_km, duration_h_nominal=duration_h)
    return CandidateInputs(
        mandi_id=mandi_id,
        mandi_name=mandi_name,
        location=LatLon(lat=13.1, lon=78.1),
        location_precision="market",
        price=price,
        routes=[route],
        latest_arrival_local=closing,
    )


# -----------------------------------------------------------------------------
# Golden Test 1: option_id format check
# -----------------------------------------------------------------------------
def test_option_id_format():
    """option_id MUST be f'{mandi_id}::{route_id}::{depart_iso}' for data layer parsing."""
    req = FIXTURE_request()
    cands = [FIXTURE_cand("m1", "Mandi 1", 1200, 1500, 1800)]
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p001")

    assert len(ps.plans) > 0
    opt_a = ps.plans[0].option
    expected_id = f"m1::m1~r0::{opt_a.depart_iso}"
    assert opt_a.option_id == expected_id


# -----------------------------------------------------------------------------
# Golden Test 2: cvar vs mean can choose different Plan A
# -----------------------------------------------------------------------------
def test_cvar_vs_mean_can_choose_different_plan_a():
    """
    Candidate 1 has a very high high-price scenario but terrible low-price scenario (high variance).
    Candidate 2 has consistent, stable prices across low, typical, high.
    - Under risk_mode='mean', Candidate 1 wins Plan A.
    - Under risk_mode='cvar25', Candidate 2 wins Plan A.
    """
    # Cand 1: low=500, typical=1800, high=3000 -> Mean is high, but worst scenarios are very low
    # Cand 2: low=1400, typical=1600, high=1800 -> Mean is moderate, worst scenarios are high
    c1 = FIXTURE_cand("m1", "Volatile Mandi", low=500, typical=1800, high=3000)
    c2 = FIXTURE_cand("m2", "Steady Mandi", low=1400, typical=1600, high=1800)
    cands = [c1, c2]

    req_mean = FIXTURE_request(risk_mode="mean")
    ps_mean = build_plan_set(req_mean, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_mean")
    plan_a_mean = ps_mean.plans[0]
    assert plan_a_mean.option.mandi_id == "m1"

    req_cvar = FIXTURE_request(risk_mode="cvar25")
    ps_cvar = build_plan_set(req_cvar, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_cvar")
    plan_a_cvar = ps_cvar.plans[0]
    assert plan_a_cvar.option.mandi_id == "m2"


# -----------------------------------------------------------------------------
# Golden Test 3: Plan B is selected by mean Net over Fail(A) & differs from 2nd best by score
# -----------------------------------------------------------------------------
def test_plan_b_hedge_differs_from_second_best_by_score():
    """
    Plan A is chosen.
    Candidate 2 has the second highest overall score, but in the scenarios where Plan A fails,
    Candidate 2 also performs poorly.
    Candidate 3 has lower overall score than Candidate 2, but excels in Plan A's failure scenarios.
    Rule: Plan B must be Candidate 3, NOT Candidate 2.
    """
    # Let origin departure be 05:00.
    # A (m1): Closes at 08:00. Duration 2h -> arrives 07:00 (none delay), but misses window under severe delay!
    # c2 (m2): Closes at 08:00 as well, so it ALSO misses window under severe delay! Overall score high.
    # c3 (m3): Closes late (18:00) or nearby. It NEVER misses window under severe delay!
    # In Fail(A) (the missed window scenario), c3 heavily beats c2!
    c1 = FIXTURE_cand("m1", "Mandi 1", low=1500, typical=1800, high=2200, duration_h=2.0, closing="07:30")
    c2 = FIXTURE_cand("m2", "Mandi 2", low=1450, typical=1750, high=2150, duration_h=2.0, closing="07:30")
    c3 = FIXTURE_cand("m3", "Mandi 3", low=1350, typical=1600, high=1900, duration_h=1.0, closing="18:00")
    cands = [c1, c2, c3]

    req = FIXTURE_request(risk_mode="mean", costs=FIXTURE_costs(hold_cost_per_hour=800.0))
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_hedge")

    plan_a = [p for p in ps.plans if p.label == "A"][0]
    plan_b = [p for p in ps.plans if p.label == "B"][0]

    assert plan_a.option.mandi_id == "m1"
    # c3 must be chosen for Plan B because it protects against A's failure
    assert plan_b.option.mandi_id == "m3"


# -----------------------------------------------------------------------------
# Golden Test 4: empty Fail(A) gives B=standby with the right note
# -----------------------------------------------------------------------------
def test_empty_fail_a_gives_standby_with_correct_note():
    """When Fail(A) is empty, B is the best remaining by score with exact note."""
    # c1 is dominant in every single scenario (price high, no closing miss)
    c1 = FIXTURE_cand("m1", "Mandi 1", low=2500, typical=3000, high=3500, closing="23:00")
    c2 = FIXTURE_cand("m2", "Mandi 2", low=1500, typical=1800, high=2000, closing="23:00")
    c3 = FIXTURE_cand("m3", "Mandi 3", low=1000, typical=1200, high=1400, closing="23:00")
    cands = [c1, c2, c3]

    req = FIXTURE_request()
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_standby")

    plan_b = [p for p in ps.plans if p.label == "B"][0]
    assert plan_b.option.mandi_id == "m2"
    assert plan_b.role_note == "A is robust in all tested scenarios; B is the standby"


# -----------------------------------------------------------------------------
# Golden Test 5: no priced option gives ABSTAIN with plans=[]
# -----------------------------------------------------------------------------
def test_no_priced_option_gives_abstain_with_empty_plans():
    """When no candidate has a price band, return PlanSet with plans=[] and grade=ABSTAIN."""
    c1 = FIXTURE_cand("m1", "Mandi 1", 0, 0, 0, priced=False)
    c2 = FIXTURE_cand("m2", "Mandi 2", 0, 0, 0, priced=False)
    cands = [c1, c2]

    req = FIXTURE_request()
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_abstain")

    assert ps.coverage.grade == "ABSTAIN"
    assert ps.plans == []
    assert ps.robustness_a_best_in == 0
    assert ps.robustness_of == 9


# -----------------------------------------------------------------------------
# Golden Test 6: deterministic tie-break across all plan roles
# -----------------------------------------------------------------------------
def test_deterministic_tie_break():
    """When options have identical scores and scenarios, tie-break order is deterministic."""
    c1 = FIXTURE_cand("m1", "Mandi 1", low=1500, typical=1800, high=2000, distance_km=100.0, duration_h=2.0)
    c2 = FIXTURE_cand("m2", "Mandi 2", low=1500, typical=1800, high=2000, distance_km=100.0, duration_h=2.0)
    cands = [c2, c1]  # pass c2 first

    req = FIXTURE_request()
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_tie")

    # Both have identical score, mean_net, and duration.
    # option_id ascending tie-break means m1 wins over m2
    assert ps.plans[0].label == "A"
    assert ps.plans[0].option.mandi_id == "m1"
    assert ps.plans[1].label == "B"
    assert ps.plans[1].option.mandi_id == "m2"


# -----------------------------------------------------------------------------
# Reviewer additions: C rain, B6/B7 integration, robustness tie-break, missing weather
# -----------------------------------------------------------------------------
def FIXTURE_with_weather(cand: CandidateInputs) -> CandidateInputs:
    route = cand.routes[0]
    wx = [WeatherSample(waypoint_idx=w.idx, time_iso="2026-10-31T06:00", temp_c=25.0, precip_mm=2.0,
                        evidence_id=f"ev_wx_{w.idx}") for w in route.waypoints]
    wps = [w.model_copy(update={"to_dest_s": {"m2": 3600.0}}) for w in route.waypoints]
    return cand.model_copy(update={"routes": [route.model_copy(update={"weather": wx, "waypoints": wps})]})


def test_plan_c_rain_is_none_never_reused_from_a():
    c1 = FIXTURE_with_weather(FIXTURE_cand("m1", "Mandi 1", low=2500, typical=3000, high=3500, closing="23:00"))
    c2 = FIXTURE_cand("m2", "Mandi 2", low=1500, typical=1800, high=2000, closing="23:00")
    ps = build_plan_set(FIXTURE_request(), [c1, c2], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_rain")
    by = {p.label: p for p in ps.plans}
    assert by["A"].option.rain_exposure_mm == 4.0
    assert by["C"].option.rain_exposure_mm is None


def test_rain_none_when_route_weather_missing():
    c1 = FIXTURE_cand("m1", "Mandi 1", low=2500, typical=3000, high=3500)
    ps = build_plan_set(FIXTURE_request(), [c1], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_nowx")
    assert ps.plans[0].option.rain_exposure_mm is None


def test_commit_flip_and_conditions_filled_from_engine():
    # A (m1) closes 08:30; nominal arrival 07:00 -> late with delay; B (m2) slightly cheaper, closes late.
    c1 = FIXTURE_with_weather(FIXTURE_cand("m1", "Mandi 1", low=2400, typical=2600, high=2800, closing="08:30"))
    c2 = FIXTURE_cand("m2", "Mandi 2", low=2380, typical=2580, high=2780, closing="23:00")
    ps = build_plan_set(FIXTURE_request(), [c1, c2], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_int")
    a = ps.plans[0]
    assert a.label == "A" and a.option.mandi_id == "m1"
    assert a.commit.computed is True and "approximate" in a.commit.note
    assert set(a.commit.last_feasible_waypoint) == {"none", "moderate", "severe"}
    assert a.flip.note and "approximate" in a.flip.note
    kinds = {c.kind: c for c in a.reconsider_if}
    assert "ripening_pct" not in kinds
    assert kinds["window_miss_scenarios"].threshold == FIXTURE_CFG.window_miss_trigger
    assert a.flip.delay_flip_pct is not None
    assert kinds["eta_slip_h"].threshold == round(a.flip.delay_flip_pct / 100 * a.option.duration_h_nominal, 2)
    if "price_drop_pct" in kinds:
        expected = (2600 - a.flip.price_flip_per_qtl) / 2600 * 100
        assert abs(kinds["price_drop_pct"].threshold - expected) < 0.1


def test_commit_not_computed_when_b_closing_unknown():
    c1 = FIXTURE_cand("m1", "Mandi 1", low=2500, typical=3000, high=3500)
    c2 = FIXTURE_cand("m2", "Mandi 2", low=1500, typical=1800, high=2000, closing=None)
    a = build_plan_set(FIXTURE_request(), [c1, c2], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_nc").plans[0]
    assert a.commit.computed is False and "approximate" in a.commit.note


def test_robustness_tie_goes_to_a_by_tie_break():
    c1 = FIXTURE_cand("m1", "Mandi 1", low=2000, typical=2500, high=3000)
    c2 = FIXTURE_cand("m2", "Mandi 2", low=2000, typical=2500, high=3000)
    ps = build_plan_set(FIXTURE_request(), [c2, c1], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p_tie")
    assert ps.plans[0].option.mandi_id == "m1"   # option_id ascending
    assert ps.robustness_a_best_in == 9
