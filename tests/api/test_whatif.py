"""
Tests for what-if scenarios and Dispatch & Monitor endpoints (Stream C / C5 part 2).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
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
    now_utc = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
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
        low=1500.0,
        typical=2000.0,
        high=2500.0,
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
        low=1400.0,
        typical=1800.0,
        high=2200.0,
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
def client():
    with TestClient(app) as c:
        yield c


def test_whatif_404_on_unknown_plan(client):
    res = client.post("/plans/non_existent_plan_xyz/whatif", json={"delay_min": 30})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_get_plan_does_not_return_candidates_blob(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    res_get = client.get(f"/plans/{plan_id}")
    assert res_get.status_code == 200
    plan_data = res_get.json()
    assert "candidates" not in plan_data
    assert "candidate_inputs" not in plan_data


def test_whatif_never_persists_to_plan_versions(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    with get_conn() as conn:
        versions_before = conn.execute(
            "SELECT COUNT(*) FROM plan_versions WHERE plan_id = ?", (plan_id,)
        ).fetchone()[0]
        plans_row_before = conn.execute(
            "SELECT status FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()

    # Call what-if
    res_whatif = client.post(
        f"/plans/{plan_id}/whatif",
        json={"delay_min": 45, "price_delta_pct": -10.0},
    )
    assert res_whatif.status_code == 200
    body = res_whatif.json()
    assert body["what_if"] is True
    assert "planset" in body
    assert "changes" in body

    with get_conn() as conn:
        versions_after = conn.execute(
            "SELECT COUNT(*) FROM plan_versions WHERE plan_id = ?", (plan_id,)
        ).fetchone()[0]
        plans_row_after = conn.execute(
            "SELECT status FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()

    # Must NOT have created any new version or altered plans table
    assert versions_before == versions_after
    assert plans_row_before[0] == plans_row_after[0]


def test_whatif_price_drop_swaps_plan_a(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]
    original_plan_a_mandi = res.json()["planset"]["plans"][0]["option"]["mandi_name"]
    assert original_plan_a_mandi == "FIXTURE Nashik"

    # Drop price on Plan A's mandi by 40% (well below FIXTURE Pimpri)
    res_whatif = client.post(
        f"/plans/{plan_id}/whatif",
        json={"price_delta_pct": -40.0},
    )
    assert res_whatif.status_code == 200
    body = res_whatif.json()
    new_plan_a_mandi = body["planset"]["plans"][0]["option"]["mandi_name"]
    # Should have flipped to FIXTURE Pimpri (FIXTURE_m2)
    assert new_plan_a_mandi == "FIXTURE Pimpri"
    assert "price" in body["diff_text"].lower() or len(body["changes"]) > 0


def test_whatif_delay_causes_window_miss(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    # Departure is 05:00 UTC (10:30 IST), Nashik nominal duration is 2.0h -> arrival ~12:30 IST
    # Set closing time to 11:00 IST and add 60 min delay -> arrival ~13:30 IST -> window missed
    res_whatif = client.post(
        f"/plans/{plan_id}/whatif",
        json={"delay_min": 60, "closing_time": {"FIXTURE_m1": "11:00"}},
    )
    assert res_whatif.status_code == 200
    body = res_whatif.json()
    opt = body["planset"]["plans"][0]["option"]
    typ_s = next(s for s in opt["scenario_results"] if s["price_level"] == "typical" and s["delay_level"] == "none")
    assert typ_s["window_status"] == "missed"


def test_get_events_chronological(client, monkeypatch):
    monkeypatch.setattr("app.agent.flow.assemble_candidates", lambda req, now: FIXTURE_candidate_assembly())
    monkeypatch.setattr("app.agent.watchdog.refresh_for_monitoring", lambda *args, **kwargs: FIXTURE_candidate_assembly())

    req = FIXTURE_request()
    res = client.post("/plan", json=json.loads(req.model_dump_json()))
    assert res.status_code == 200
    plan_id = res.json()["plan_id"]

    # Approve dispatch
    client.post(f"/plans/{plan_id}/approve", json={"version": 1, "decision": "approve", "kind": "dispatch"})

    # Tick watchdog
    client.post(f"/plans/{plan_id}/watch/tick")

    # Post progress
    client.post(f"/plans/{plan_id}/progress", json={"delay": "delay:30"})

    # Tick watchdog again
    client.post(f"/plans/{plan_id}/watch/tick")

    # Fetch events
    res_ev = client.get(f"/plans/{plan_id}/events")
    assert res_ev.status_code == 200
    events = res_ev.json()
    assert len(events) >= 2
    # Verify chronological ordering
    ts_list = [e["ts"] for e in events]
    assert ts_list == sorted(ts_list)
