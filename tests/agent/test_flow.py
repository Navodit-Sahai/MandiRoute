"""
Tests for state-machine flow (C3-lite).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.flow import run_plan_flow
from app.contracts import (
    CandidateInputs,
    Costs,
    DispatchRequest,
    LatLon,
    PriceBand,
    RouteOption,
    Waypoint,
)
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn

NOW = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)


def FIXTURE_request(crop: str = "Tomato") -> DispatchRequest:
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
        crop=crop,
        quantity_qtl=40.0,
        origin=LatLon(lat=18.52, lon=73.86),
        origin_label="FIXTURE_Pune",
        origin_state="Maharashtra",
        vehicle="covered",
        loading_time=NOW,
        preferred_departure=NOW,
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


@pytest.fixture
def fixture_assembler(monkeypatch):
    """flow.py resolves its assembler via app.api.deps.get_candidate_assembler()."""
    monkeypatch.setattr(
        "app.api.deps.get_candidate_assembler",
        lambda: (lambda req, now: FIXTURE_assembly()),
    )


def test_flow_execution_and_trace(fixture_assembler):
    res = run_plan_flow(FIXTURE_request(), plan_id="test_p1", now=NOW)

    assert res.plan_id == "test_p1"
    assert res.status == "awaiting_approval"
    assert len(res.planset.plans) >= 1
    assert res.summary is not None
    assert isinstance(res.summary, str) and len(res.summary) > 0
    assert res.why_not_text is not None and len(res.why_not_text) > 0

    # Verify agent_trace rows
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM agent_trace WHERE plan_id='test_p1' ORDER BY step ASC").fetchall()
        assert len(rows) <= 12  # Step cap 12
        assert len(rows) >= 5
        steps = [r["node"] for r in rows]
        assert steps == ["intake", "source_gate", "assemble", "assembly_meta", "candidates", "decide", "geo", "explain", "done"]
        for r in rows:
            assert r["ok"] == 1
        assert len(res.map_points) == 1
        assert "FIXTURE_m1~r0" in res.route_paths


def test_why_not_withheld_when_number_check_fails(fixture_assembler, monkeypatch):
    monkeypatch.setattr("app.explain.render_why_not", lambda w: "Why not Nashik? Rate of 999999.0 per quintal.")

    res = run_plan_flow(FIXTURE_request(), plan_id="test_why_not_fail", now=NOW)

    assert res.why_not_text is None
    assert "why_not withheld: number check failed" in res.assembly_warnings


def test_why_not_exception_keeps_summary(fixture_assembler, monkeypatch):
    """Regression: logger was undefined, so a why_not failure used to wipe the summary too."""

    def _boom(*args, **kwargs):
        raise RuntimeError("FIXTURE why_not failure")

    monkeypatch.setattr("app.engine.why_not_highest", _boom)

    res = run_plan_flow(FIXTURE_request(), plan_id="test_why_not_exc", now=NOW)

    assert res.summary is not None       # summary must survive
    assert res.why_not_text is None      # only why-not is dropped


def test_warnings_and_commodity_persisted_in_trace(fixture_assembler):
    res = run_plan_flow(FIXTURE_request("Onion"), plan_id="test_persist", now=NOW)
    assert "test warning" in res.assembly_warnings

    import json

    with get_conn() as conn:
        done = conn.execute(
            "SELECT args_json FROM agent_trace WHERE plan_id='test_persist' AND node='done'"
        ).fetchone()
        meta = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id='test_persist' AND node='assembly_meta'"
        ).fetchone()
    assert "test warning" in json.loads(done["args_json"])["warnings"]
    assert json.loads(meta["result_summary"])["commodity"] == "Onion"


@pytest.mark.parametrize("crop", ["Tomato", "Onion", "Potato"])
def test_flow_is_commodity_agnostic(fixture_assembler, crop):
    res = run_plan_flow(FIXTURE_request(crop), plan_id=f"test_crop_{crop.lower()}", now=NOW)
    assert res.status == "awaiting_approval"
    assert len(res.planset.plans) >= 1
