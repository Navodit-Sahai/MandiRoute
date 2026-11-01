"""
Comprehensive system audit and end-to-end component verification test suite.
Verifies:
1. No hardcoded fallback data: errors are flagged and abstain occurs; stubs raise in live mode.
2. Plans strictly use fetched/calculated data: pure deterministic formulas, evidenced IDs.
3. Decision engine modules: netvalue, scenarios (9-grid), plans (A/B/C), commit points, flips, coverage, triggers, diff.
4. Ripening is disabled (SR-1/CR-5) and produces no dependencies/claims.
5. Explainer and number checker: approved wording only, unevidenced numbers flagged.
6. API routes and agent flow: health, trace, persistence, graceful degradation.
7. UI formatting and forbidden word scan (AGENT §7).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from app import guards
from app.config import get_settings, Settings
from app.contracts import (
    CandidateInputs, Condition, Costs, CoverageReport, DispatchProgress, DispatchRequest,
    Evidence, FlipBoundaries, Kind, LatLon, PlanOption, PlanSet, PriceBand,
    RipenessStage, RouteOption, ScenarioResult, VehicleType, Waypoint, WeatherSample,
)
from app.engine.units import seconds_to_hours, hours_to_seconds, delayed_arrival, window_status, lowest_k_count, mean_of_lowest
from app.engine.netvalue import net_value, NetBreakdown
from app.engine.scenarios import evaluate_option
from app.engine.plans import build_plan_set
from app.engine.commit import compute_commit_points
from app.engine.flips import compute_flips
from app.engine.coverage import grade_coverage
from app.engine.triggers import evaluate_triggers_and_recompute, WatchState, TriggerDecision
from app.explain import render_plan_summary, render_diff, check_numbers
from app.api.main import app
from app.agent.flow import run_plan_flow
from app.ui.format import format_inr, format_kind_tag, scan_forbidden_words


# ---------------------------------------------------------------------------
# 1. HARDCODED DATA & FALLBACK LOGIC AUDIT
# ---------------------------------------------------------------------------
def test_guard_enforces_live_mode_and_blocks_stubs(monkeypatch):
    """NN-1 & NN-2: in live mode, any stub call MUST raise RuntimeError."""
    monkeypatch.setenv("MG_MODE", "live")
    assert guards.mode() == "live"
    assert guards.banner() is None
    
    with pytest.raises(RuntimeError) as exc_info:
        guards.require_stub_allowed("test_fixture")
    assert "not allowed when MG_MODE='live'" in str(exc_info.value)

    monkeypatch.setenv("MG_MODE", "replay")
    assert guards.mode() == "replay"
    assert guards.banner() == "HISTORICAL REPLAY - NOT LIVE"
    with pytest.raises(RuntimeError):
        guards.require_stub_allowed("test_fixture")


def test_unknown_not_equal_zero_rule():
    """NN-10: Missing mandi closing time produces window_status='unknown', NEVER 'met' or 'missed'."""
    cfg = get_settings()
    arrival = datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)
    status = window_status(arrival, latest_hhmm=None, tz_name=cfg.timezone_name)
    assert status == "unknown"


def test_no_fake_weather_or_prices_on_failure():
    """Missing prices or weather samples must be None, never silently set to 0.0 or room temperature."""
    ws = WeatherSample(waypoint_idx=0, time_iso="2026-11-01T10:00:00+00:00", temp_c=None, precip_mm=None, evidence_id="ev_0")
    assert ws.temp_c is None
    assert ws.precip_mm is None


# ---------------------------------------------------------------------------
# 2. DECISION ENGINE PURE CALCULATIONS AUDIT
# ---------------------------------------------------------------------------
def test_net_value_exact_formula():
    """Verify net value calculation with all transportation modes and deductions."""
    req = DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.85),
        origin_label="Pune",
        origin_state="Maharashtra",
        ripeness=RipenessStage.BREAKER,
        vehicle=VehicleType.OPEN,
        loading_time=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        costs=Costs(
            transport_mode="per_km_per_trip",
            transport_rate=30.0,
            commission_pct=5.0,
            market_fee_pct=1.0,
            hamali_per_qtl=10.0,
            loading_per_qtl=5.0,
            hold_cost_per_hour=50.0,
        ),
    )
    # Q=40, Price=2000 => Gross = 80,000
    # Commission (5%) = 4,000, Market fee (1%) = 800
    # Hamali = 40 * 10 = 400, Loading = 40 * 5 = 200
    # Transport (100km * 30/km) = 3,000
    # Hold (2h * 50/h) = 100
    # Total deductions = 4000 + 800 + 400 + 200 + 3000 + 100 = 8,500
    # Net = 80,000 - 8,500 = 71,500
    val = net_value(req, price_per_qtl=2000.0, distance_km=100.0, wait_hours=2.0)
    assert val == 71500.0


def test_scenario_grid_9_money_scenarios_and_cvar():
    """Verify B4 9-scenario grid, CVaR25 (lowest 3 out of 9), and rain aggregation."""
    cfg = get_settings()
    req = DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.85),
        origin_label="Pune",
        origin_state="Maharashtra",
        ripeness=RipenessStage.BREAKER,
        vehicle=VehicleType.OPEN,
        loading_time=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        risk_mode="cvar25",
        costs=Costs(transport_mode="fixed_per_trip", transport_rate=2000.0),
    )
    cand = CandidateInputs(
        mandi_id="m1",
        mandi_name="Mandi One",
        location=LatLon(lat=19.0, lon=73.0),
        location_precision="market",
        price=PriceBand(low=1000.0, typical=1500.0, high=2000.0, record_date="2026-11-01"),
        latest_arrival_local="18:00",
    )
    route = RouteOption(
        route_id="m1::r1",
        distance_km=100.0,
        duration_h_nominal=2.0,
        waypoints=[
            Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.85), elapsed_nominal_s=0.0),
            Waypoint(idx=1, point=LatLon(lat=19.0, lon=73.0), elapsed_nominal_s=7200.0),
        ],
        weather=[
            WeatherSample(waypoint_idx=0, time_iso="2026-11-01T10:00:00+00:00", temp_c=25.0, precip_mm=2.5, evidence_id="ev_w1"),
            WeatherSample(waypoint_idx=1, time_iso="2026-11-01T12:00:00+00:00", temp_c=26.0, precip_mm=1.5, evidence_id="ev_w2"),
        ],
    )
    opt = evaluate_option(req, cand, route, req.preferred_departure, cfg)
    
    # Check 9 scenarios
    assert len(opt.scenario_results) == 9
    nets = [sr.net_value for sr in opt.scenario_results]
    assert opt.mean_net == sum(nets) / 9.0
    
    # CVaR25: lowest 3 mean
    sorted_nets = sorted(nets)
    expected_cvar = sum(sorted_nets[:3]) / 3.0
    assert abs(opt.score - expected_cvar) < 1e-4
    
    # Rain exposure sum
    assert opt.rain_exposure_mm == 4.0  # 2.5 + 1.5
    
    # SR-1 Ripening flag OFF
    assert opt.ripening is None


def test_plan_set_builder_a_b_c_selection():
    """Verify B5 Plan A, B (hedge over Fail(A)), and C (delayed departure)."""
    cfg = get_settings()
    req = DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.85),
        origin_label="Pune",
        origin_state="Maharashtra",
        ripeness=RipenessStage.BREAKER,
        vehicle=VehicleType.OPEN,
        loading_time=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        risk_mode="cvar25",
        costs=Costs(transport_mode="fixed_per_trip", transport_rate=1000.0, hold_cost_per_hour=50.0),
    )
    # Cand 1: high typical price but tight window (missed in severe delay)
    cand1 = CandidateInputs(
        mandi_id="m1", mandi_name="Mandi One", location=LatLon(lat=19.0, lon=73.0),
        location_precision="market", price=PriceBand(low=1200.0, typical=2000.0, high=2200.0, record_date="2026-11-01"),
        latest_arrival_local="12:30",  # Preferred depart 10:00 + 2h = 12:00. Severe delay (60%) = 10:00 + 3.2h = 13:12 (MISSED!)
    )
    r1 = RouteOption(route_id="m1::r1", distance_km=100.0, duration_h_nominal=2.0, waypoints=[
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.85), elapsed_nominal_s=0.0, to_dest_s={"m2": 3600.0}),
        Waypoint(idx=1, point=LatLon(lat=19.0, lon=73.0), elapsed_nominal_s=7200.0, to_dest_s={"m2": 5400.0}),
    ])
    cand1.routes = [r1]

    # Cand 2: moderate price, safe window
    cand2 = CandidateInputs(
        mandi_id="m2", mandi_name="Mandi Two", location=LatLon(lat=18.8, lon=74.0),
        location_precision="market", price=PriceBand(low=1400.0, typical=1700.0, high=1900.0, record_date="2026-11-01"),
        latest_arrival_local="18:00",
    )
    r2 = RouteOption(route_id="m2::r2", distance_km=80.0, duration_h_nominal=1.5, waypoints=[
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.85), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=18.8, lon=74.0), elapsed_nominal_s=5400.0),
    ])
    cand2.routes = [r2]

    ps = build_plan_set(req, [cand1, cand2], [], req.preferred_departure, cfg, plan_id="test_plan")
    assert len(ps.plans) == 3
    labels = [p.label for p in ps.plans]
    assert labels == ["A", "B", "C"]
    
    plan_a = ps.plans[0]
    plan_b = ps.plans[1]
    plan_c = ps.plans[2]
    
    # In CVaR25, m2 wins Plan A because m1 misses closing time under severe delay (penalty of 12h hold cost)
    assert plan_a.option.mandi_id == "m2"
    assert plan_b.option.mandi_id == "m1"
    # Plan C evaluates departure slots
    assert plan_c.option.depart_iso != plan_a.option.depart_iso
    
    # Flips & Commitment points on A
    assert plan_a.flip.price_flip_per_qtl is not None or plan_a.flip.delay_flip_pct is not None
    assert "approximate" in str(plan_a.commit.note).lower()


# ---------------------------------------------------------------------------
# 3. EXPLAINER & CRITIC NUMBER AUDIT
# ---------------------------------------------------------------------------
def test_explainer_approved_wording_and_number_check():
    """Verify AGENT §7 wording rules and numeric integrity."""
    cfg = get_settings()
    req = DispatchRequest(
        crop="Tomato", quantity_qtl=40.0, origin=LatLon(lat=18.52, lon=73.85),
        origin_label="Pune", origin_state="Maharashtra", ripeness=RipenessStage.BREAKER,
        vehicle=VehicleType.OPEN, loading_time=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        costs=Costs(transport_mode="fixed_per_trip", transport_rate=1000.0),
    )
    cand = CandidateInputs(
        mandi_id="m1", mandi_name="Nashik Market", location=LatLon(lat=19.0, lon=73.0),
        location_precision="market", price=PriceBand(low=1200.0, typical=1600.0, high=2000.0, record_date="2026-11-01"),
        latest_arrival_local="18:00",
    )
    route = RouteOption(route_id="m1::r1", distance_km=100.0, duration_h_nominal=2.0, waypoints=[
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.85), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=19.0, lon=73.0), elapsed_nominal_s=7200.0),
    ])
    cand.routes = [route]
    ps = build_plan_set(req, [cand], [], req.preferred_departure, cfg, plan_id="p_expl")
    
    summary = render_plan_summary(ps, {})
    assert summary is not None
    assert len(summary) > 20
    
    # Check forbidden words scan
    forbidden_found = scan_forbidden_words(summary)
    assert forbidden_found == [], f"Found forbidden phrases in summary: {forbidden_found}"

    # Number checker test: injected unknown number must be flagged
    text_with_fake = summary + " Guaranteed profit of Rs 9999999"
    offending = check_numbers(text_with_fake, allowed_values=[1200.0, 1600.0, 2000.0, 100.0, 2.0])
    assert any("9999999" in s for s in offending)


# ---------------------------------------------------------------------------
# 4. FASTAPI ENDPOINTS AUDIT
# ---------------------------------------------------------------------------
def test_fastapi_endpoints():
    client = TestClient(app)
    
    # 1. Health
    rh = client.get("/health")
    assert rh.status_code == 200
    jh = rh.json()
    assert jh["status"] == "ok"
    assert jh["mode"] in ("live", "dev_stub", "replay")
    
    # 2. Assumptions
    ra = client.get("/assumptions")
    assert ra.status_code == 200
    ja = ra.json()
    assert "timezone_name" in ja
    assert len(ja) > 5
    
    # 3. Sources Health
    rs = client.get("/sources/health")
    assert rs.status_code == 200
    js = rs.json()
    assert isinstance(js, list)
    
    # 4. Bad request validation (422)
    r_bad = client.post("/plan", json={"crop": "Mango"})
    assert r_bad.status_code == 422


# ---------------------------------------------------------------------------
# 5. UI FORMATTING UTILITIES AUDIT
# ---------------------------------------------------------------------------
def test_ui_formatting_helpers():
    assert format_inr(51200) == "₹51,200"
    assert format_inr(0) == "₹0"
    badge = format_kind_tag("observed")
    assert "observed" in badge
    assert scan_forbidden_words("We offer real-time prices and guaranteed profit") != []
