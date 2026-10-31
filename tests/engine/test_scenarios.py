"""
B4 golden tests - Option evaluator & scenario grid (BLUEPRINT B4, PLAN 7.2, 7.3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.contracts import CandidateInputs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint, WeatherSample
from app.contracts.inputs import Costs
from app.engine.scenarios import evaluate_option, option_sort_key, sort_options
from app.engine.units import lowest_k_count

IST = timezone(timedelta(hours=5, minutes=30))
FIXTURE_DEPART = datetime(2026, 10, 31, 5, 0, tzinfo=IST)
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
        loading_time=FIXTURE_DEPART,
        preferred_departure=FIXTURE_DEPART,
        max_distance_km=400.0,
        costs=FIXTURE_costs(),
        mandi_latest_arrival={},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )
    values.update(overrides)
    return DispatchRequest(**values)


def FIXTURE_price_band(low: float = 1200.0, typical: float = 1500.0, high: float = 1800.0) -> PriceBand:
    return PriceBand(
        low=low,
        typical=typical,
        high=high,
        record_date="2026-10-31",
        evidence_ids=["ev_price_low", "ev_price_typ", "ev_price_high"],
    )


def FIXTURE_route(
    route_id: str = "FIXTURE_m1~r0",
    distance_km: float = 100.0,
    duration_h_nominal: float = 2.0,
    rain_mm: float | None = 2.5,
) -> RouteOption:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=13.0, lon=77.5), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=13.5, lon=78.0), elapsed_nominal_s=duration_h_nominal * 3600.0),
    ]
    weather = []
    if rain_mm is not None:
        weather = [
            WeatherSample(
                waypoint_idx=0,
                time_iso="2026-10-31T05:00:00+05:30",
                temp_c=22.0,
                precip_mm=rain_mm,
                evidence_id="ev_wx_0",
            ),
            WeatherSample(
                waypoint_idx=1,
                time_iso="2026-10-31T07:00:00+05:30",
                temp_c=24.0,
                precip_mm=rain_mm,
                evidence_id="ev_wx_1",
            ),
        ]
    return RouteOption(
        route_id=route_id,
        distance_km=distance_km,
        duration_h_nominal=duration_h_nominal,
        waypoints=wps,
        weather=weather,
        evidence_ids=["ev_route_dist", "ev_route_dur"],
    )


def FIXTURE_candidate(
    mandi_id: str = "FIXTURE_m1",
    mandi_name: str = "FIXTURE Kolar",
    latest_arrival_local: str | None = "10:00",
    price_band: PriceBand | None = None,
) -> CandidateInputs:
    return CandidateInputs(
        mandi_id=mandi_id,
        mandi_name=mandi_name,
        location=LatLon(lat=13.1, lon=78.1),
        location_precision="market",
        price=price_band or FIXTURE_price_band(),
        latest_arrival_local=latest_arrival_local,
    )


# -----------------------------------------------------------------------------
# Golden Test 1: 27 raw environmental scenarios collapse to 9 money scenarios
# -----------------------------------------------------------------------------
def test_27_raw_scenarios_collapse_to_9_money_scenarios():
    """
    Environmental stress space has 3 price levels x 3 delay levels x 3 cargo offsets = 27 raw scenarios.
    Because money calculations depend only on price and delay, evaluate_option produces exactly
    9 ScenarioResult entries (all pairs of price in {low, typical, high} x delay in {none, moderate, severe}).
    """
    req = FIXTURE_request()
    cand = FIXTURE_candidate()
    route = FIXTURE_route()

    opt = evaluate_option(req, cand, route, FIXTURE_DEPART, FIXTURE_CFG)

    assert len(opt.scenario_results) == 9
    combos = {(sr.price_level, sr.delay_level) for sr in opt.scenario_results}
    expected = {
        ("low", "none"), ("low", "moderate"), ("low", "severe"),
        ("typical", "none"), ("typical", "moderate"), ("typical", "severe"),
        ("high", "none"), ("high", "moderate"), ("high", "severe"),
    }
    assert combos == expected


# -----------------------------------------------------------------------------
# Golden Test 2: k(n=9)=3, k(n=18)=5, k(n=4)=1
# -----------------------------------------------------------------------------
def test_lowest_k_math():
    """Verify lowest_k_count formula with q=0.25 on specific sample counts."""
    q = 0.25
    assert lowest_k_count(n=9, q=q) == 3
    assert lowest_k_count(n=18, q=q) == 5
    assert lowest_k_count(n=4, q=q) == 1


# -----------------------------------------------------------------------------
# Golden Test 3: score uses the lowest-k mean (CVaR25) vs mean
# -----------------------------------------------------------------------------
def test_score_uses_lowest_k_mean():
    """
    When risk_mode is 'cvar25', score must equal the average of the 3 lowest net values among 9 scenarios.
    When risk_mode is 'mean', score must equal the average of all 9 net values.
    """
    cand = FIXTURE_candidate(latest_arrival_local="12:00")
    route = FIXTURE_route(distance_km=100.0, duration_h_nominal=2.0)

    # In cvar25 mode:
    req_cvar = FIXTURE_request(risk_mode="cvar25")
    opt_cvar = evaluate_option(req_cvar, cand, route, FIXTURE_DEPART, FIXTURE_CFG)

    all_nets = [sr.net_value for sr in opt_cvar.scenario_results]
    sorted_nets = sorted(all_nets)
    expected_cvar_score = sum(sorted_nets[:3]) / 3.0

    assert pytest.approx(opt_cvar.score, rel=1e-5) == expected_cvar_score
    assert pytest.approx(opt_cvar.mean_net, rel=1e-5) == sum(all_nets) / 9.0

    # In mean mode:
    req_mean = FIXTURE_request(risk_mode="mean")
    opt_mean = evaluate_option(req_mean, cand, route, FIXTURE_DEPART, FIXTURE_CFG)

    assert pytest.approx(opt_mean.score, rel=1e-5) == sum(all_nets) / 9.0
    assert opt_mean.score > opt_cvar.score


# -----------------------------------------------------------------------------
# Golden Test 4: window unknown when closing time is absent
# -----------------------------------------------------------------------------
def test_window_unknown_when_closing_time_is_absent():
    """
    If latest_arrival_local is not given in candidate or request,
    window_status is 'unknown' across all 9 scenarios, and no missed-window penalty is incurred.
    """
    req = FIXTURE_request(mandi_latest_arrival={})
    cand = FIXTURE_candidate(latest_arrival_local=None)
    route = FIXTURE_route(distance_km=100.0, duration_h_nominal=2.0)

    opt = evaluate_option(req, cand, route, FIXTURE_DEPART, FIXTURE_CFG)

    for sr in opt.scenario_results:
        assert sr.window_status == "unknown"


# -----------------------------------------------------------------------------
# Golden Test 5: missed window adds NEXT_DAY_WAIT_H x hold and keeps price level
# -----------------------------------------------------------------------------
def test_missed_window_adds_next_day_wait_and_keeps_price():
    """
    When arrival time is strictly after mandi closing time:
    1. window_status is 'missed'.
    2. Net value is penalized by exactly (cfg.next_day_wait_h * hold_cost_per_hour).
    3. Price per qtl used is unchanged (no price drift modelled).
    """
    # Departure is 05:00 IST, duration is 4.0 h nominal.
    # Arrival nominal is 09:00 IST.
    # Mandi closes at 08:00 IST -> every scenario (even delay=none) arrives after closing time.
    req = FIXTURE_request(costs=FIXTURE_costs(hold_cost_per_hour=150.0))
    cand_early_close = FIXTURE_candidate(latest_arrival_local="08:00")
    cand_late_close = FIXTURE_candidate(latest_arrival_local="14:00")
    route = FIXTURE_route(distance_km=100.0, duration_h_nominal=4.0)

    opt_missed = evaluate_option(req, cand_early_close, route, FIXTURE_DEPART, FIXTURE_CFG)
    opt_met = evaluate_option(req, cand_late_close, route, FIXTURE_DEPART, FIXTURE_CFG)

    expected_hold_penalty = FIXTURE_CFG.next_day_wait_h * 150.0  # 12 h * 150 Rs/h = 1800 Rs

    for sr_missed, sr_met in zip(opt_missed.scenario_results, opt_met.scenario_results):
        assert sr_missed.window_status == "missed"
        assert sr_met.window_status == "met"
        assert pytest.approx(sr_missed.net_value, rel=1e-5) == sr_met.net_value - expected_hold_penalty


# -----------------------------------------------------------------------------
# Golden Test 6: deterministic tie-break
# -----------------------------------------------------------------------------
def test_deterministic_tie_break():
    """
    Tie-break order:
    1. Higher score first
    2. Higher mean_net first
    3. Shorter nominal duration first
    4. option_id ascending
    """
    req = FIXTURE_request()
    cand = FIXTURE_candidate()

    # Create synthetic options to test all branches of the tie-break
    r_fast = FIXTURE_route(route_id="FIXTURE_m1~r0", duration_h_nominal=2.0)
    r_slow = FIXTURE_route(route_id="FIXTURE_m1~r1", duration_h_nominal=3.0)

    opt1 = evaluate_option(req, cand, r_fast, FIXTURE_DEPART, FIXTURE_CFG)
    opt2 = evaluate_option(req, cand, r_slow, FIXTURE_DEPART, FIXTURE_CFG)

    # 1. Higher score wins
    assert option_sort_key(opt1) < option_sort_key(opt2)  # opt1 is better

    # 2. Score tie -> higher mean_net wins
    opt_tie_score_a = opt1.model_copy(update={"score": 50000.0, "mean_net": 55000.0})
    opt_tie_score_b = opt1.model_copy(update={"score": 50000.0, "mean_net": 52000.0})
    sorted_by_mean = sort_options([opt_tie_score_b, opt_tie_score_a])
    assert sorted_by_mean[0] == opt_tie_score_a

    # 3. Score & mean_net tie -> shorter nominal duration wins
    opt_shorter = opt1.model_copy(update={"score": 50000.0, "mean_net": 50000.0, "duration_h_nominal": 2.0})
    opt_longer = opt1.model_copy(update={"score": 50000.0, "mean_net": 50000.0, "duration_h_nominal": 4.0})
    sorted_by_dur = sort_options([opt_longer, opt_shorter])
    assert sorted_by_dur[0] == opt_shorter

    # 4. Score, mean_net & duration tie -> option_id ascending wins
    opt_id_a = opt1.model_copy(update={"score": 50000.0, "mean_net": 50000.0, "duration_h_nominal": 2.0, "option_id": "aaa"})
    opt_id_b = opt1.model_copy(update={"score": 50000.0, "mean_net": 50000.0, "duration_h_nominal": 2.0, "option_id": "bbb"})
    sorted_by_id = sort_options([opt_id_b, opt_id_a])
    assert sorted_by_id[0] == opt_id_a


# -----------------------------------------------------------------------------
# Ripening is OFF check
# -----------------------------------------------------------------------------
def test_ripening_is_none_when_disabled():
    """When cfg.ripening_enabled is False, opt.ripening must be None."""
    req = FIXTURE_request()
    cand = FIXTURE_candidate()
    route = FIXTURE_route()

    assert FIXTURE_CFG.ripening_enabled is False
    opt = evaluate_option(req, cand, route, FIXTURE_DEPART, FIXTURE_CFG)
    assert opt.ripening is None


# -----------------------------------------------------------------------------
# Rain exposure check
# -----------------------------------------------------------------------------
def test_rain_exposure_calculation():
    """Verify rain exposure sums precipitation samples along route."""
    req = FIXTURE_request()
    cand = FIXTURE_candidate()
    route_with_rain = FIXTURE_route(rain_mm=3.5)
    opt_rain = evaluate_option(req, cand, route_with_rain, FIXTURE_DEPART, FIXTURE_CFG)
    assert opt_rain.rain_exposure_mm == 7.0  # 2 samples x 3.5 mm

    route_no_rain = FIXTURE_route(rain_mm=None)
    opt_no_rain = evaluate_option(req, cand, route_no_rain, FIXTURE_DEPART, FIXTURE_CFG)
    assert opt_no_rain.rain_exposure_mm is None

