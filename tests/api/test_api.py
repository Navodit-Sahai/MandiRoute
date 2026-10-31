"""
Tests for FastAPI application endpoints (C1).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import guards
from app.api.main import app
from app.api.stubs import FIXTURE_assembly, FIXTURE_candidates
from app.contracts import CandidateInputs, Costs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn


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
        mandi_latest_arrival={},
        risk_mode="cvar25",
        ripening_limit_pct=25.0,
    )
    values.update(overrides)
    return DispatchRequest(**values)


def FIXTURE_candidate_assembly() -> CandidateAssembly:
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
        warnings=["FIXTURE test assembly"],
        selected_variety="Other",
        selected_grade="FAQ",
    )


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client, monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["mode"] == "live"
    assert data["banner"] is None


def test_assumptions_endpoint(client):
    res = client.get("/assumptions")
    assert res.status_code == 200
    data = res.json()
    assert "delay_mult" in data
    assert "cvar_q" in data
    assert "ripening_enabled" in data


def test_evidence_endpoint_404_and_found(client):
    # 404 for unknown evidence
    res_404 = client.get("/evidence/ev_nonexistent")
    assert res_404.status_code == 404

    # Insert test evidence into SQLite
    ev_id = "ev_01234567"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO evidence(id, kind, label, value, unit, source, as_of, fetched_at, stale, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ev_id, "observed", "Test price", json.dumps(1500.0), "Rs/qtl", "test", "2026-10-31", "2026-10-31T05:00:00Z", 0, "test note"),
        )

    res_found = client.get(f"/evidence/{ev_id}")
    assert res_found.status_code == 200
    data = res_found.json()
    assert data["id"] == ev_id
    assert data["value"] == 1500.0


def test_post_plan_abstained_when_empty_assembly(client, monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    # Monkeypatch assemble_candidates to return empty assembly
    empty_assembly = CandidateAssembly(
        candidates=[],
        evidence=[],
        source_status=[],
        warnings=["no candidate mandis within range"],
        selected_variety=None,
        selected_grade=None,
    )
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: empty_assembly)

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "abstained"
    assert data["planset"]["plans"] == []
    assert data["coverage"]["grade"] == "ABSTAIN"
    assert len(data["plan_id"]) <= 8


def test_post_plan_with_fixture_assembly_persists_rows(client, monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    fixture_assembly = FIXTURE_candidate_assembly()
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: fixture_assembly)

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "awaiting_approval"
    assert len(data["planset"]["plans"]) >= 1
    plan_id = data["plan_id"]

    # Verify rows persisted in SQLite
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        assert plan_row is not None
        assert plan_row["status"] == "awaiting_approval"

        version_rows = conn.execute("SELECT * FROM plan_versions WHERE plan_id = ?", (plan_id,)).fetchall()
        assert len(version_rows) == 1
        assert version_rows[0]["version"] == 1

        trace_rows = conn.execute("SELECT * FROM agent_trace WHERE plan_id = ? ORDER BY step ASC", (plan_id,)).fetchall()
        assert len(trace_rows) >= 5
        nodes = [r["node"] for r in trace_rows]
        assert "intake" in nodes
        assert "source_gate" in nodes
        assert "assemble" in nodes
        assert "decide" in nodes
        assert "done" in nodes


def test_stub_provider_raises_in_live_mode(monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    assert guards.mode() == "live"
    with pytest.raises(RuntimeError):
        FIXTURE_candidates()
    with pytest.raises(RuntimeError):
        req = FIXTURE_request()
        FIXTURE_assembly(req, datetime.now(timezone.utc))


def test_get_plan_round_trip(client, monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    post_res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert post_res.status_code == 200
    created_id = post_res.json()["plan_id"]

    get_res = client.get(f"/plans/{created_id}")
    assert get_res.status_code == 200
    plan_data = get_res.json()
    assert plan_data["plan_id"] == created_id
    assert plan_data["status"] == "awaiting_approval"
    assert plan_data["latest_version"] == 1
    assert "request" in plan_data
    assert "planset" in plan_data


def test_get_plan_trace(client, monkeypatch):
    monkeypatch.delenv("MG_MODE", raising=False)
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    post_res = client.post("/plan", json=json.loads(req.model_dump_json()))
    created_id = post_res.json()["plan_id"]

    trace_res = client.get(f"/plans/{created_id}/trace")
    assert trace_res.status_code == 200
    trace_steps = trace_res.json()
    assert len(trace_steps) >= 5
    assert trace_steps[0]["node"] == "intake"
    assert trace_steps[0]["ok"] is True


def test_get_plan_404(client):
    res = client.get("/plans/nonexistent_id")
    assert res.status_code == 404


def test_bad_request_body_returns_422(client):
    res = client.post("/plan", json={"bad_key": 123})
    assert res.status_code == 422

