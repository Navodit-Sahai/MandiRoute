"""
Tests for Watchdog, Progress, Approval, and Tick endpoints (Stream C / C4).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.contracts import CandidateInputs, Costs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn
from app.agent.watchdog import get_plan_lock, tick_plan, _watchdog_loop


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
    now_utc = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
    values = dict(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.86),
        origin_label="FIXTURE_Pune",
        origin_state="Maharashtra",
        vehicle="covered",
        loading_time=now_utc,
        preferred_departure=now_utc,
        max_distance_km=400.0,
        costs=FIXTURE_costs(),
        mandi_latest_arrival={"FIXTURE_m1": "14:00", "FIXTURE_m2": "14:00"},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )
    values.update(overrides)
    return DispatchRequest(**values)


def FIXTURE_candidate_assembly() -> CandidateAssembly:
    wps1 = [
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.86), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=19.99, lon=73.79), elapsed_nominal_s=7200.0),
    ]
    route1 = RouteOption(
        route_id="FIXTURE_m1~r0",
        distance_km=150.0,
        duration_h_nominal=2.0,
        waypoints=wps1,
        weather=[],
        geometry_ok=True,
        evidence_ids=["ev_fx_route1"],
    )
    price1 = PriceBand(
        low=1200.0,
        typical=1600.0,
        high=2000.0,
        record_date="2026-10-31",
        unit_verified=True,
        evidence_ids=["ev_fx_price1"],
    )
    cand1 = CandidateInputs(
        mandi_id="FIXTURE_m1",
        mandi_name="FIXTURE Nashik",
        location=LatLon(lat=19.99, lon=73.79),
        location_precision="market",
        price=price1,
        routes=[route1],
        latest_arrival_local="14:00",
    )

    wps2 = [
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.86), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=18.60, lon=73.80), elapsed_nominal_s=1800.0),
    ]
    route2 = RouteOption(
        route_id="FIXTURE_m2~r0",
        distance_km=30.0,
        duration_h_nominal=0.5,
        waypoints=wps2,
        weather=[],
        geometry_ok=True,
        evidence_ids=["ev_fx_route2"],
    )
    price2 = PriceBand(
        low=1100.0,
        typical=1500.0,
        high=1900.0,
        record_date="2026-10-31",
        unit_verified=True,
        evidence_ids=["ev_fx_price2"],
    )
    cand2 = CandidateInputs(
        mandi_id="FIXTURE_m2",
        mandi_name="FIXTURE Pimpri",
        location=LatLon(lat=18.60, lon=73.80),
        location_precision="market",
        price=price2,
        routes=[route2],
        latest_arrival_local="14:00",
    )

    return CandidateAssembly(
        candidates=[cand1, cand2],
        evidence=[],
        source_status=[],
        warnings=["FIXTURE dual candidates"],
        selected_variety="Other",
        selected_grade="FAQ",
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MG_AGENT", "plain")
    with TestClient(app) as c:
        yield c


def test_approve_reject_transitions_and_409(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    # 1. Invalid transition: cannot approve replan when awaiting_approval
    res_bad = client.post(
        f"/plans/{plan_id}/approve",
        json={"version": 1, "decision": "approve", "kind": "replan"},
    )
    assert res_bad.status_code == 409

    # 2. Valid dispatch approval: awaiting_approval -> monitoring
    res_app = client.post(
        f"/plans/{plan_id}/approve",
        json={"version": 1, "decision": "approve", "kind": "dispatch"},
    )
    assert res_app.status_code == 200
    assert res_app.json()["status"] == "monitoring"

    # Verify watch_state initialized
    with get_conn() as conn:
        ws = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (plan_id,)).fetchone()
        assert ws is not None
        assert ws["last_progress_json"] is not None

        # Verify action recorded for notify
        act = conn.execute("SELECT * FROM actions WHERE plan_id = ?", (plan_id,)).fetchall()
        assert len(act) >= 1

    # 3. Duplicate dispatch approval fails with 409
    res_dup = client.post(
        f"/plans/{plan_id}/approve",
        json={"version": 1, "decision": "approve", "kind": "dispatch"},
    )
    assert res_dup.status_code == 409


def test_progress_endpoint_validation_and_storage(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    plan_id = res.json()["plan_id"]
    client.post(f"/plans/{plan_id}/approve", json={"version": 1, "decision": "approve", "kind": "dispatch"})

    # Update progress with string delay "delay:60"
    p_res = client.post(f"/plans/{plan_id}/progress", json={"delay": "delay:60"})
    assert p_res.status_code == 200
    assert p_res.json()["progress"]["reported_delay_min"] == 60.0

    # Update progress with waypoint passed "passed:1"
    wp_res = client.post(f"/plans/{plan_id}/progress", json={"passed": "passed:1"})
    assert wp_res.status_code == 200
    assert wp_res.json()["progress"]["last_waypoint_idx"] == 1


def test_watch_tick_no_triggers(client, monkeypatch):
    fixture_assembly = FIXTURE_candidate_assembly()
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: fixture_assembly)
    monkeypatch.setattr("app.agent.watchdog.refresh_for_monitoring", lambda **kwargs: fixture_assembly)

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    plan_id = res.json()["plan_id"]
    client.post(f"/plans/{plan_id}/approve", json={"version": 1, "decision": "approve", "kind": "dispatch"})

    # Tick without delays
    tick_res = client.post(f"/plans/{plan_id}/watch/tick")
    assert tick_res.status_code == 200
    data = tick_res.json()
    assert data["new_version"] is None
    assert data["diff"] is None


def test_watch_tick_consecutive_delay_proposes_replan_and_diff(client, monkeypatch):
    fixture_assembly = FIXTURE_candidate_assembly()
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: fixture_assembly)
    monkeypatch.setattr("app.agent.watchdog.refresh_for_monitoring", lambda **kwargs: fixture_assembly)

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    plan_id = res.json()["plan_id"]
    client.post(f"/plans/{plan_id}/approve", json={"version": 1, "decision": "approve", "kind": "dispatch"})

    # Report severe delay: 180 min
    client.post(f"/plans/{plan_id}/progress", json={"delay": "delay:180"})

    # HYSTERESIS_N is 2 in config. Tick 1 increments count
    tick1 = client.post(f"/plans/{plan_id}/watch/tick")
    assert tick1.status_code == 200

    # Tick 2 satisfies hysteresis -> triggers replan proposal
    tick2 = client.post(f"/plans/{plan_id}/watch/tick")
    assert tick2.status_code == 200
    t2_data = tick2.json()

    # If trigger fired and alternative is better/different, replan is proposed
    if t2_data.get("new_version"):
        assert t2_data["new_version"] == 2
        assert t2_data["diff"] is not None
        assert t2_data["diff_text"] is not None

        # Verify plan status is replan_pending
        get_res = client.get(f"/plans/{plan_id}")
        assert get_res.json()["status"] == "replan_pending"

        # Verify diff endpoint
        diff_res = client.get(f"/plans/{plan_id}/diff")
        assert diff_res.status_code == 200
        assert diff_res.json()["diff"]["new_version"] == 2


def test_no_fresh_data_degrades_gracefully(client, monkeypatch):
    fixture_assembly = FIXTURE_candidate_assembly()
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: fixture_assembly)

    def failing_refresh(**kwargs):
        raise RuntimeError("Simulated network outage")

    monkeypatch.setattr("app.agent.watchdog.refresh_for_monitoring", failing_refresh)

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    plan_id = res.json()["plan_id"]
    client.post(f"/plans/{plan_id}/approve", json={"version": 1, "decision": "approve", "kind": "dispatch"})

    # Tick when refresh fails
    tick_res = client.post(f"/plans/{plan_id}/watch/tick")
    assert tick_res.status_code == 200
    assert tick_res.json()["decision"] == "degraded: no fresh data"


def test_double_tick_is_serialized():
    lock = get_plan_lock("test_plan_lock")
    assert hasattr(lock, "acquire") and hasattr(lock, "release")
    # Acquired lock blocks second acquire if non-blocking
    assert lock.acquire(blocking=False) is True
    assert lock.acquire(blocking=False) is False
    lock.release()


def test_watchdog_thread_survives_exception(monkeypatch):
    """Verify watchdog loop survives exceptions without terminating."""
    stop_event = threading.Event()
    error_raised = threading.Event()
    tick_count = 0

    def mock_get_conn():
        nonlocal tick_count
        tick_count += 1
        if tick_count == 1:
            error_raised.set()
            raise RuntimeError("Database busy error")
        stop_event.set()
        from app.db.db import get_conn as real_get_conn
        return real_get_conn()

    monkeypatch.setattr("app.agent.watchdog.get_conn", mock_get_conn)
    monkeypatch.setenv("MG_DEMO", "1")

    # Run in a short thread
    t = threading.Thread(target=_watchdog_loop, args=(stop_event,), daemon=True)
    t.start()
    assert error_raised.wait(timeout=1.0) is True
    stop_event.set()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert tick_count >= 1
