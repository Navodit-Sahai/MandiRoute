"""
Tests for state-machine flow (C3-lite).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.agent.flow import run_plan_flow
from app.contracts import CandidateInputs, Costs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn


def FIXTURE_request() -> DispatchRequest:
    now_utc = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
    costs = Costs(
        transport_mode="per_km_per_trip",
        transport_rate=20.0,
        commission_pct=5.0,
        market_fee_pct=2.0,
        hamali_per_qtl=5.0,
        loading_per_qtl=5.0,
        hold_cost_per_hour=100.0,
    )
    return DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.86),
        origin_label="FIXTURE_Pune",
        origin_state="Maharashtra",
        vehicle="covered",
        loading_time=now_utc,
        preferred_departure=now_utc,
        max_distance_km=400.0,
        costs=costs,
        mandi_latest_arrival={},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )


def FIXTURE_assembly() -> CandidateAssembly:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.86), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=19.99, lon=73.79), elapsed_nominal_s=7200.0),
    ]
    route = RouteOption(
        route_id="FIXTURE_m1~r0",
        distance_km=150.0,
        duration_h_nominal=2.0,
        waypoints=wps,
        weather=[],
        geometry_ok=True,
        evidence_ids=["ev_fx_route"],
    )
    price = PriceBand(
        low=1200.0,
        typical=1600.0,
        high=2000.0,
        record_date="2026-10-31",
        unit_verified=True,
        evidence_ids=["ev_fx_price"],
    )
    cand = CandidateInputs(
        mandi_id="FIXTURE_m1",
        mandi_name="FIXTURE Nashik",
        location=LatLon(lat=19.99, lon=73.79),
        location_precision="market",
        price=price,
        routes=[route],
        latest_arrival_local="14:00",
    )
    return CandidateAssembly(
        candidates=[cand],
        evidence=[],
        source_status=[],
        warnings=["test warning"],
        selected_variety="Other",
        selected_grade="FAQ",
    )


def test_flow_execution_and_trace(monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_assembly())

    req = FIXTURE_request()
    now = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
    res = run_plan_flow(req, plan_id="test_p1", now=now)

    assert res.plan_id == "test_p1"
    assert res.status == "awaiting_approval"
    assert len(res.planset.plans) >= 1
    assert res.summary is not None

    # Verify agent_trace rows
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM agent_trace WHERE plan_id='test_p1' ORDER BY step ASC").fetchall()
        assert len(rows) <= 12  # Step cap 12
        assert len(rows) >= 5
        steps = [r["node"] for r in rows]
        assert steps == ["intake", "source_gate", "assemble", "decide", "explain", "done"]
        for r in rows:
            assert r["ok"] == 1

