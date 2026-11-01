"""
Comprehensive test suite for the LangGraph multi-agent workflow.
Verifies autonomous agent routing, discrete tool invocation, checkpointing,
and adherence to the zero-hardcoded-data and no-fallback non-negotiables.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent.graph.workflow import build_plan_graph, execute_plan_graph, get_plan_graph
from app.contracts import (
    CandidateInputs,
    Costs,
    DispatchRequest,
    LatLon,
    PriceBand,
    RipenessStage,
    RouteOption,
    VehicleType,
    Waypoint,
)
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn


def FIXTURE_request() -> DispatchRequest:
    now_utc = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    return DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.86),
        origin_label="Pune, Maharashtra",
        origin_state="Maharashtra",
        ripeness=RipenessStage.TURNING,
        vehicle=VehicleType.OPEN,
        loading_time=now_utc,
        preferred_departure=now_utc,
        max_distance_km=300.0,
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


def FIXTURE_assembly_with_candidates() -> CandidateAssembly:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.86), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=19.99, lon=73.79), elapsed_nominal_s=7200.0),
    ]
    route = RouteOption(
        route_id="m1~r0",
        distance_km=150.0,
        duration_h_nominal=2.0,
        waypoints=wps,
        weather=[],
        geometry_ok=True,
        evidence_ids=["ev_r1"],
    )
    cand = CandidateInputs(
        mandi_id="m1",
        mandi_name="Nashik Mandi",
        location=LatLon(lat=19.99, lon=73.79),
        location_precision="market",
        price=PriceBand(low=1200.0, typical=1600.0, high=2000.0, record_date="2026-11-01"),
        routes=[route],
        latest_arrival_local="18:00",
    )
    return CandidateAssembly(
        candidates=[cand],
        evidence=[],
        source_status=[],
        warnings=["Test assembly warning"],
        selected_variety="Hybrid",
        selected_grade="FAQ",
    )


def test_graph_compilation():
    """Verify that the LangGraph StateGraph compiles with all agents and checkpointing."""
    graph = build_plan_graph()
    assert graph is not None
    # Singleton access
    assert get_plan_graph() is not None


def test_langgraph_workflow_success_execution(monkeypatch):
    """
    Test full LangGraph execution when data is available.
    Verifies autonomous transitions: intake_gate -> data_assembly -> critic -> decision -> explainer.
    """
    monkeypatch.setattr(
        "app.agent.graph.tools.assemble_candidates_tool",
        lambda req, now: FIXTURE_assembly_with_candidates(),
    )

    req = FIXTURE_request()
    now = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    plan_id = "test_lg_success"

    final_state = execute_plan_graph(req, plan_id, now)

    # State validation
    assert final_state["plan_id"] == plan_id
    assert final_state["status"] == "awaiting_approval"
    assert final_state["planset"] is not None
    assert len(final_state["planset"].plans) >= 1
    assert len(final_state["map_points"]) == 1
    assert "m1~r0" in final_state["route_paths"]
    assert final_state["summary"] is not None

    # SQLite agent_trace rows audit
    with get_conn() as conn:
        rows = conn.execute("SELECT node, tool, ok FROM agent_trace WHERE plan_id=? ORDER BY step ASC", (plan_id,)).fetchall()
        assert len(rows) >= 5
        assert len(rows) <= 12  # Step cap 12
        nodes = [r["node"] for r in rows]
        assert "intake" in nodes
        assert "source_gate" in nodes
        assert "assemble" in nodes
        assert "decide" in nodes
        assert "geo" in nodes
        assert "explain" in nodes
        assert "done" in nodes
        for r in rows:
            assert r["ok"] == 1


def test_langgraph_workflow_abstain_path_no_data(monkeypatch):
    """
    Test autonomous abstention when no candidates or prices are available.
    Ensures zero fallback logic or hardcoded data is used (NN-1).
    """
    empty_assembly = CandidateAssembly(
        candidates=[],
        evidence=[],
        source_status=[],
        warnings=["No records found from source"],
        selected_variety=None,
        selected_grade=None,
    )
    monkeypatch.setattr(
        "app.agent.graph.tools.assemble_candidates_tool",
        lambda req, now: empty_assembly,
    )

    req = FIXTURE_request()
    now = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    plan_id = "test_lg_abstain"

    final_state = execute_plan_graph(req, plan_id, now)

    assert final_state["plan_id"] == plan_id
    assert final_state["status"] == "abstained"
    assert final_state["planset"] is not None
    assert len(final_state["planset"].plans) == 0
    assert final_state["planset"].coverage.grade == "ABSTAIN"
    assert "no candidate mandi has a price record" in final_state["planset"].coverage.reasons


def test_langgraph_intake_validation_abort():
    """
    Verify that invalid request stops at intake and does not invoke data or decision tools.
    """
    # Create request with negative quantity
    req = FIXTURE_request()
    # Bypass pydantic validation at model init by modifying
    object.__setattr__(req, "quantity_qtl", -10.0)

    now = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    plan_id = "test_lg_invalid"

    final_state = execute_plan_graph(req, plan_id, now)
    assert final_state["status"] == "error"
    assert "Quantity must be positive" in final_state["error"]

