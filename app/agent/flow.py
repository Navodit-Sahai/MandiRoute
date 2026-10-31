"""
Plain state-machine orchestrator (C3-lite). NO LangGraph, NO LLM.

Steps:
  1. intake (validate input)
  2. source_gate (check environment/mode readiness)
  3. assemble (fetch candidates, prices, routes, weather)
  4. decide (call engine build_plan_set once)
  5. explain (render summary via app.explain if installed, else None)
  6. done (finalize flow result)

Every step is recorded to the agent_trace SQLite table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app import guards
from app.config import get_settings
from app.contracts import CoverageReport, DispatchRequest, PlanSet, SourceStatus
from app.data.assemble import CandidateAssembly, assemble_candidates
from app.db.db import get_conn
from app.engine.plans import build_plan_set

STEP_CAP = 12


@dataclass
class FlowResult:
    plan_id: str
    status: str                         # "awaiting_approval" | "abstained"
    planset: PlanSet
    assembly_warnings: list[str]
    source_status: list[SourceStatus]
    trace_id: str
    summary: Optional[str] = None


def _record_trace(
    plan_id: str,
    step: int,
    node: str,
    tool: Optional[str],
    args_json: str,
    result_summary: str,
    ok: bool,
    ts: str,
) -> None:
    """Record one step to the agent_trace table."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agent_trace(plan_id, step, node, tool, args_json, result_summary, ok, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (plan_id, step, node, tool, args_json, result_summary, 1 if ok else 0, ts),
        )


def run_plan_flow(req: DispatchRequest, plan_id: str, now: datetime) -> FlowResult:
    """
    Execute the plan orchestration state machine with step recording.
    Step cap is 12.
    """
    cfg = get_settings()
    step_num = 1
    trace_id = plan_id

    # 1. Intake: Validate request
    ts_now = now.isoformat()
    intake_args = json.dumps({"crop": req.crop, "origin": req.origin_label, "quantity_qtl": req.quantity_qtl})
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="intake",
        tool=None,
        args_json=intake_args,
        result_summary="Request input validated",
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 2. Source Gate: Check system mode & source readiness
    current_mode = guards.mode()
    gate_args = json.dumps({"mode": current_mode})
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="source_gate",
        tool="app.guards.mode",
        args_json=gate_args,
        result_summary=f"Operating mode is '{current_mode}'",
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 3. Assemble: Fetch candidate mandis, prices, routes, and weather
    assemble_args = json.dumps({"origin": [req.origin.lat, req.origin.lon], "max_km": req.max_distance_km})
    try:
        assembly: CandidateAssembly = assemble_candidates(req, now)
        assemble_summary = (
            f"Assembled {len(assembly.candidates)} candidates, "
            f"{len(assembly.evidence)} evidence items, {len(assembly.warnings)} warnings"
        )
        assemble_ok = True
    except Exception as exc:
        assemble_summary = f"Assembly failed: {exc}"
        assemble_ok = False
        _record_trace(
            plan_id=plan_id,
            step=step_num,
            node="assemble",
            tool="app.data.assemble.assemble_candidates",
            args_json=assemble_args,
            result_summary=assemble_summary,
            ok=False,
            ts=ts_now,
        )
        raise

    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="assemble",
        tool="app.data.assemble.assemble_candidates",
        args_json=assemble_args,
        result_summary=assemble_summary,
        ok=assemble_ok,
        ts=ts_now,
    )
    step_num += 1

    # 4. Decide: Build plan set using decision engine (ONE call)
    decide_args = json.dumps({"candidate_count": len(assembly.candidates), "risk_mode": req.risk_mode})
    planset: PlanSet = build_plan_set(
        req=req,
        cands=assembly.candidates,
        evidence=assembly.evidence,
        now=now,
        cfg=cfg,
        plan_id=plan_id,
        version=1,
    )
    decide_summary = f"PlanSet v1 created with {len(planset.plans)} plans (coverage: {planset.coverage.grade})"
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="decide",
        tool="app.engine.build_plan_set",
        args_json=decide_args,
        result_summary=decide_summary,
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 5. Explain: Render summary if explainer module exists
    summary: Optional[str] = None
    explain_summary = "Explainer not installed, summary=None"
    try:
        from app.explain import render_plan_summary  # type: ignore[import-not-found]
        evidence_index = {e.id: e for e in assembly.evidence}
        summary = render_plan_summary(planset, evidence_index)
        explain_summary = "Plan summary rendered"
    except ImportError:
        summary = None

    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="explain",
        tool="app.explain.render_plan_summary" if summary else None,
        args_json="{}",
        result_summary=explain_summary,
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 6. Done: Finalize status
    is_abstained = planset.coverage.grade == "ABSTAIN" or len(planset.plans) == 0
    final_status = "abstained" if is_abstained else "awaiting_approval"
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="done",
        tool=None,
        args_json="{}",
        result_summary=f"Flow completed with status={final_status}",
        ok=True,
        ts=ts_now,
    )

    return FlowResult(
        plan_id=plan_id,
        status=final_status,
        planset=planset,
        assembly_warnings=assembly.warnings,
        source_status=assembly.source_status,
        trace_id=trace_id,
        summary=summary,
    )

