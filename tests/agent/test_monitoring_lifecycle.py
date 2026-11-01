"""
End-to-end tests demonstrating continuous real-time monitoring,
dynamic plan revision, hysteresis, cooldowns, and audit trail in MandiGuard LangGraph workflow.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.graph.workflow import execute_monitoring_tick, execute_plan_graph
from app.contracts import (
    CandidateInputs,
    Costs,
    DispatchProgress,
    DispatchRequest,
    Evidence,
    Kind,
    LatLon,
    PriceBand,
    RouteOption,
    Waypoint,
)
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn, init_db
from app.engine.triggers import WatchState


IST = timezone(timedelta(hours=5, minutes=30))
NOW_BASE = datetime(2026, 10, 31, 5, 0, tzinfo=IST)


def FIXTURE_request() -> DispatchRequest:
    costs = Costs(
        transport_mode="per_km_per_trip",
        transport_rate=20.0,
        commission_pct=5.0,
        market_fee_pct=2.0,
        hamali_per_qtl=5.0,
        loading_per_qtl=5.0,
        hold_cost_per_hour=300.0,
    )
    return DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=13.0, lon=77.5),
        origin_label="FIXTURE_farm",
        origin_state="Karnataka",
        vehicle="covered",
        loading_time=NOW_BASE,
        preferred_departure=NOW_BASE,
        max_distance_km=400.0,
        costs=costs,
        mandi_latest_arrival={},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )


def FIXTURE_cand(mid: str, typical: float, closing: str, record_date: str = "2026-10-31") -> CandidateInputs:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=13.0, lon=77.5), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=13.2, lon=77.7), elapsed_nominal_s=3600.0, to_dest_s={"m2": 3600.0}),
        Waypoint(idx=2, point=LatLon(lat=13.4, lon=77.9), elapsed_nominal_s=7200.0),
    ]
    route = RouteOption(
        route_id=f"{mid}~r0",
        distance_km=100.0,
        duration_h_nominal=2.0,
        waypoints=wps,
        weather=[],
        geometry_ok=True,
        evidence_ids=[f"ev_route_{mid}"],
    )
    price = PriceBand(
        low=typical - 100,
        typical=typical,
        high=typical + 100,
        record_date=record_date,
        unit_verified=True,
        evidence_ids=[f"ev_p_{mid}"],
    )
    return CandidateInputs(
        mandi_id=mid,
        mandi_name=f"FIXTURE_{mid}",
        location=LatLon(lat=13.4, lon=77.9),
        location_precision="market",
        price=price,
        routes=[route],
        latest_arrival_local=closing,
    )


def FIXTURE_assembly(cands: list[CandidateInputs] | None = None) -> CandidateAssembly:
    candidates = cands or [
        FIXTURE_cand("m1", 1500.0, "08:30"),  # Closer closing time (prone to slip)
        FIXTURE_cand("m2", 1480.0, "20:00"),  # Later closing time (resilient alternative)
    ]
    evidence = [
        Evidence(
            id=f"ev_p_{c.mandi_id}",
            kind=Kind.OBSERVED,
            label=f"Price at {c.mandi_name}",
            value=c.price.typical if c.price else 0,
            unit="INR/qtl",
            source="test",
            as_of=NOW_BASE.isoformat(),
            fetched_at=NOW_BASE.isoformat(),
            stale=False,
            note="test evidence",
        )
        for c in candidates
    ]
    return CandidateAssembly(
        candidates=candidates,
        evidence=evidence,
        source_status=[],
        warnings=[],
        selected_variety="Other",
        selected_grade="FAQ",
    )


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_complete_monitoring_and_replanning_lifecycle(monkeypatch):
    """
    Demonstrates full end-to-end lifecycle:
    1. Initial Plan Synthesis (v1, Plan A = m1).
    2. Dispatch Approval (Status -> monitoring).
    3. Monitoring Tick 1: No change (Decision -> retain v1).
    4. Monitoring Tick 2: Delay spike (T2 fires, hysteresis count 1/2 -> retain v1).
    5. Monitoring Tick 3: Persisted delay (Hysteresis met -> Dynamic replan to v2, Plan A = m2).
    6. Human Operator Approval: Approves replan (Status -> monitoring).
    7. Monitoring Resumes: Subsequent tick retains revised Plan v2.
    """
    plan_id = "test_lifecycle_p1"
    req = FIXTURE_request()
    assembly = FIXTURE_assembly()

    # Monkeypatch data assembly for deterministic testing
    monkeypatch.setattr("app.agent.graph.tools.assemble_candidates_tool", lambda r, now: assembly)
    monkeypatch.setattr("app.agent.graph.tools.refresh_monitoring_data_tool", lambda r, opt_ids, now, **kw: assembly)

    # -------------------------------------------------------------------------
    # 1. Initial Plan Generation
    # -------------------------------------------------------------------------
    initial_state = execute_plan_graph(req=req, plan_id=plan_id, now=NOW_BASE)
    assert initial_state["status"] == "awaiting_approval"
    assert initial_state["planset"] is not None
    assert initial_state["planset"].version == 1
    assert initial_state["planset"].plans[0].option.mandi_id == "m1"

    # Persist in DB as would occur via API
    now_iso = NOW_BASE.isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, request_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (plan_id, req.model_dump_json(), initial_state["status"], now_iso, now_iso),
        )
        conn.execute(
            "INSERT INTO plan_versions(plan_id, version, planset_json, diff_json, created_at) VALUES (?, 1, ?, NULL, ?)",
            (plan_id, initial_state["planset"].model_dump_json(), now_iso),
        )

    # -------------------------------------------------------------------------
    # 2. Dispatch Approval
    # -------------------------------------------------------------------------
    from app.api.main import dispatch_plan, DispatchActionRequest
    disp_res = dispatch_plan(plan_id, DispatchActionRequest(dispatched_at=now_iso, by="operator"))
    assert disp_res["status"] == "monitoring"

    # Verify watch state was initialized
    with get_conn() as conn:
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (plan_id,)).fetchone()
        assert w_row is not None
        assert w_row["last_progress_json"] is not None

    # -------------------------------------------------------------------------
    # 3. Monitoring Tick 1: Nominal Conditions (No Change)
    # -------------------------------------------------------------------------
    prog_nominal = DispatchProgress(plan_id=plan_id, reported_delay_min=0.0, dispatched_at=now_iso)
    tick1_state = execute_monitoring_tick(plan_id=plan_id, progress=prog_nominal, now=NOW_BASE, assembly=assembly)

    assert tick1_state["status"] == "monitoring"
    assert tick1_state["trigger_decision"] is not None
    assert not tick1_state["trigger_decision"].propose_replan
    assert tick1_state["active_version"] == 1
    assert tick1_state.get("diff") is None

    # -------------------------------------------------------------------------
    # 4. Monitoring Tick 2: Single Delay Spike (+90 min, Hysteresis = 1/2)
    # -------------------------------------------------------------------------
    prog_spike = DispatchProgress(plan_id=plan_id, reported_delay_min=90.0, dispatched_at=now_iso)
    tick2_state = execute_monitoring_tick(plan_id=plan_id, progress=prog_spike, now=NOW_BASE + timedelta(minutes=5), assembly=assembly)

    dec2 = tick2_state["trigger_decision"]
    assert "T2" in dec2.fired
    assert dec2.persisted == []  # Hysteresis not yet satisfied
    assert not dec2.propose_replan
    assert tick2_state["active_version"] == 1
    assert tick2_state.get("diff") is None

    # -------------------------------------------------------------------------
    # 5. Monitoring Tick 3: Persisted Delay (+90 min, Hysteresis Met -> Replan!)
    # -------------------------------------------------------------------------
    tick3_state = execute_monitoring_tick(
        plan_id=plan_id,
        progress=prog_spike,
        now=NOW_BASE + timedelta(minutes=10),
        assembly=assembly,
        watch_state=tick2_state["watch_state"],
    )

    dec3 = tick3_state["trigger_decision"]
    assert "T2" in dec3.persisted
    assert dec3.propose_replan is True
    assert dec3.new_plan_set is not None
    assert dec3.new_plan_set.version == 2

    # Verify recommendation flipped to m2 because m1 closing was missed
    new_opt_a = dec3.new_plan_set.plans[0].option
    assert new_opt_a.mandi_id == "m2"

    # Verify Diff and Explainer
    assert tick3_state["diff"] is not None
    assert tick3_state["diff"].recommendation_changed is True
    assert "T2" in tick3_state["diff"].primary_reason
    assert tick3_state["status"] == "replan_pending"
    assert tick3_state["active_version"] == 2
    assert tick3_state["diff_summary"] is not None
    assert "Replan v1 -> v2" in tick3_state["diff_summary"]

    # Verify SQLite Audit Trail
    with get_conn() as conn:
        p_row = conn.execute("SELECT status FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        assert p_row["status"] == "replan_pending"

        v_rows = conn.execute("SELECT version, diff_json FROM plan_versions WHERE plan_id = ? ORDER BY version ASC", (plan_id,)).fetchall()
        assert len(v_rows) == 2
        assert v_rows[1]["version"] == 2
        assert v_rows[1]["diff_json"] is not None

        # Check agent_trace recorded each agent's execution
        trace_rows = conn.execute("SELECT node, ok FROM agent_trace WHERE plan_id = ? ORDER BY step ASC", (plan_id,)).fetchall()
        trace_nodes = [r["node"] for r in trace_rows]
        assert "change_detection" in trace_nodes
        assert "replanning" in trace_nodes
        assert "replan_explainer" in trace_nodes

    # -------------------------------------------------------------------------
    # 6. Human Operator Approval of Proposed Revision (NN-11)
    # -------------------------------------------------------------------------
    from app.api.main import approve_plan_action, ApprovalRequest
    appr_res = approve_plan_action(plan_id, ApprovalRequest(kind="replan", decision="approve", by="operator"))
    assert appr_res["status"] == "monitoring"

    with get_conn() as conn:
        p_row = conn.execute("SELECT status FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        assert p_row["status"] == "monitoring"

    # -------------------------------------------------------------------------
    # 7. Resumed Monitoring against Revised Plan (v2)
    # -------------------------------------------------------------------------
    # Cooldown expires after cfg.cooldown_min (30m)
    now_resumed = NOW_BASE + timedelta(minutes=45)
    tick4_state = execute_monitoring_tick(
        plan_id=plan_id,
        progress=prog_spike,
        now=now_resumed,
        assembly=assembly,
    )

    # For m2, closing time is 20:00, so +90 min delay does not miss m2
    assert tick4_state["status"] == "monitoring"
    assert not tick4_state["trigger_decision"].propose_replan
    assert tick4_state["active_version"] == 2


def test_missing_unreliable_data_retains_and_abstains(monkeypatch):
    """
    Test Requirement 4: Missing or unreliable data -> mark affected information
    as unknown and abstain from replanning.
    """
    plan_id = "test_missing_data_p2"
    req = FIXTURE_request()

    # Candidate with missing price
    unpriced_cand = CandidateInputs(
        mandi_id="m1",
        mandi_name="FIXTURE_m1",
        location=LatLon(lat=13.4, lon=77.9),
        location_precision="market",
        price=None,  # Missing price!
        routes=[],
    )
    assembly_empty = CandidateAssembly(
        candidates=[unpriced_cand],
        evidence=[],
        source_status=[],
        warnings=["Agmarknet price unavailable"],
        selected_variety=None,
        selected_grade=None,
    )

    # Prepare existing plan in DB
    init_db()
    cands_initial = [FIXTURE_cand("m1", 1500.0, "08:30"), FIXTURE_cand("m2", 1480.0, "20:00")]
    assembly_initial = FIXTURE_assembly(cands_initial)
    monkeypatch.setattr("app.agent.graph.tools.assemble_candidates_tool", lambda r, now: assembly_initial)
    state = execute_plan_graph(req=req, plan_id=plan_id, now=NOW_BASE)

    # Ingest tick with missing/unreliable candidate data
    prog = DispatchProgress(plan_id=plan_id, reported_delay_min=90.0, dispatched_at=NOW_BASE.isoformat())
    tick_state = execute_monitoring_tick(
        plan_id=plan_id,
        progress=prog,
        now=NOW_BASE,
        assembly=assembly_empty,
        watch_state=WatchState(trigger_counts={"T2": 2}),  # Trigger persisted but data missing
    )

    dec = tick_state["trigger_decision"]
    assert dec is not None
    assert dec.propose_replan is False
    assert "fresh data for Plan A missing" in dec.reason or "fresh data missing" in dec.reason
    assert tick_state.get("diff") is None


def test_cooldown_prevents_premature_replanning(monkeypatch):
    """
    Test Requirement 7: Avoid unnecessary replanning using configurable cooldowns.
    """
    plan_id = "test_cooldown_p3"
    req = FIXTURE_request()
    cands = [FIXTURE_cand("m1", 1500.0, "08:30"), FIXTURE_cand("m2", 1480.0, "20:00")]
    assembly = FIXTURE_assembly(cands)
    monkeypatch.setattr("app.agent.graph.tools.assemble_candidates_tool", lambda r, now: assembly)

    execute_plan_graph(req=req, plan_id=plan_id, now=NOW_BASE)

    # Active cooldown until +20 min
    active_cooldown_time = (NOW_BASE + timedelta(minutes=20)).isoformat()
    watch_state = WatchState(
        trigger_counts={"T2": 5},
        cooldown_until=active_cooldown_time,
    )

    prog = DispatchProgress(plan_id=plan_id, reported_delay_min=90.0, dispatched_at=NOW_BASE.isoformat())
    tick_state = execute_monitoring_tick(
        plan_id=plan_id,
        progress=prog,
        now=NOW_BASE + timedelta(minutes=5),  # Within cooldown
        assembly=assembly,
        watch_state=watch_state,
    )

    dec = tick_state["trigger_decision"]
    assert dec is not None
    assert dec.propose_replan is False
    assert "cooldown until" in dec.reason


def test_api_endpoints_monitoring_integration(monkeypatch):
    """
    Test FastAPI monitoring and dynamic revision endpoints end-to-end.
    """
    from fastapi.testclient import TestClient
    from app.api.main import app

    cands = [FIXTURE_cand("m1", 1500.0, "08:30"), FIXTURE_cand("m2", 1480.0, "20:00")]
    assembly = FIXTURE_assembly(cands)
    monkeypatch.setattr("app.agent.graph.tools.assemble_candidates_tool", lambda r, now: assembly)
    monkeypatch.setattr("app.agent.graph.tools.refresh_monitoring_data_tool", lambda r, opt_ids, now, **kw: assembly)

    client = TestClient(app)

    # 1. Create plan
    req = FIXTURE_request()
    resp_create = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert resp_create.status_code == 200
    plan_id = resp_create.json()["plan_id"]
    assert plan_id

    # 2. List plans
    resp_list = client.get("/plans")
    assert resp_list.status_code == 200
    assert any(p["plan_id"] == plan_id for p in resp_list.json())

    # 3. Dispatch truck
    resp_disp = client.post(f"/plans/{plan_id}/dispatch", json={"by": "operator"})
    assert resp_disp.status_code == 200
    assert resp_disp.json()["status"] == "monitoring"

    # 4. Report progress
    resp_prog = client.post(f"/plans/{plan_id}/progress", json={"reported_delay_min": 30.0, "source": "checkin_button"})
    assert resp_prog.status_code == 200
    assert resp_prog.json()["progress"]["reported_delay_min"] == 30.0

    # 5. Execute watch tick
    resp_tick = client.post(f"/plans/{plan_id}/watch/tick")
    assert resp_tick.status_code == 200
    tick_data = resp_tick.json()
    assert tick_data["plan_id"] == plan_id
    assert "status" in tick_data
    assert "decision_reason" in tick_data

    # 6. Check watch status
    resp_watch = client.get(f"/plans/{plan_id}/watch")
    assert resp_watch.status_code == 200
    watch_data = resp_watch.json()
    assert "watch_state" in watch_data
    assert "events" in watch_data
    assert len(watch_data["events"]) >= 1

    # 7. Check plan details
    resp_get = client.get(f"/plans/{plan_id}")
    assert resp_get.status_code == 200
    plan_detail = resp_get.json()
    assert plan_detail["plan_id"] == plan_id
    assert plan_detail["status"] == "monitoring"
    assert plan_detail["watch_state"] is not None
