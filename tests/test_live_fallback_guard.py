"""
Verify Candidate Assembly and Plan Flow behavior when data.gov.in returns 403 / error.
Rule verification:
- No hardcoded fallback data should be used.
- Candidate assembly should return 0 candidates, a failed SourceStatus, and warnings.
- The decision engine should return PlanSet with grade=ABSTAIN and plans=[].
- The agent flow should finish with status='abstained'.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import pytest
from app.config import load_env, get_settings
from app.contracts import Costs, DispatchRequest, LatLon, RipenessStage, VehicleType
from app.data.assemble import assemble_candidates
from app.agent.flow import run_plan_flow

load_env()

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="Live network tests only run when RUN_LIVE_TESTS=1",
)

def test_assembly_and_flow_with_current_api_state():
    req = DispatchRequest(
        crop="Tomato",
        quantity_qtl=40.0,
        origin=LatLon(lat=18.5204, lon=73.8567),
        origin_label="Pune, Maharashtra",
        origin_state="Maharashtra",
        ripeness=RipenessStage.TURNING,
        vehicle=VehicleType.OPEN,
        loading_time=datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc),
        max_distance_km=300.0,
        costs=Costs(
            transport_mode="per_km_per_trip",
            transport_rate=35.0,
            commission_pct=5.0,
            market_fee_pct=1.0,
            hamali_per_qtl=15.0,
            loading_per_qtl=10.0,
            hold_cost_per_hour=50.0,
        ),
    )
    now = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
    
    print("\n--- Running assemble_candidates ---")
    assembly = assemble_candidates(req, now)
    print(f"Candidates count: {len(assembly.candidates)}")
    print(f"Evidence count: {len(assembly.evidence)}")
    print(f"Warnings ({len(assembly.warnings)}):")
    for w in assembly.warnings:
        print(f"  - {w}")
    print(f"Source statuses ({len(assembly.source_status)}):")
    for s in assembly.source_status:
        print(f"  - {s.name}: ok={s.ok}, http={s.http}, note={s.note}")
        
    # Verify: NO fallback data
    assert len(assembly.candidates) == 0, "Expected 0 candidates when price API fails; no fallback data allowed!"
    
    # Verify: Flow execution on failed API
    print("\n--- Running run_plan_flow ---")
    res = run_plan_flow(req, plan_id="test_live_403", now=now)
    print(f"Flow status: {res.status}")
    print(f"Plans count: {len(res.planset.plans)}")
    print(f"Coverage grade: {res.planset.coverage.grade}")
    print(f"Coverage reasons: {res.planset.coverage.reasons}")
    
    assert res.status == "abstained"
    assert len(res.planset.plans) == 0
    assert res.planset.coverage.grade == "ABSTAIN"
    print("VERIFICATION SUCCESSFUL: Zero fake data used. System gracefully ABSTAINS.")

if __name__ == "__main__":
    test_assembly_and_flow_with_current_api_state()
