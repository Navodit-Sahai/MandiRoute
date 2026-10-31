"""
Plain state-machine orchestrator (C3-lite). NO LangGraph, NO LLM.

Steps:
  1. intake (validate input)
  2. source_gate (check environment/mode readiness)
  3. assemble (fetch candidates, prices, routes, weather)
  4. decide (call engine build_plan_set once)
  5. geo (extract map_points and route_paths for UI)
  6. explain (render summary via app.explain if installed, else None)
  7. done (finalize flow result)

Every step is recorded to the agent_trace SQLite table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app import guards
from app.config import get_settings
from app.contracts import DispatchRequest, PlanSet, SourceStatus
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
    summary: str | None = None
    map_points: list[dict[str, Any]] = field(default_factory=list)
    route_paths: dict[str, list[list[float]]] = field(default_factory=dict)


def _record_trace(
    plan_id: str,
    step: int,
    node: str,
    tool: str | None,
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

    # 3b. Assembly metadata: persist selected grade and variety
    meta_json = json.dumps({
        "selected_grade": assembly.selected_grade,
        "selected_variety": assembly.selected_variety,
    })
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="assembly_meta",
        tool=None,
        args_json="{}",
        result_summary=meta_json,
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 3c. Candidates: persist assembled candidates and evidence IDs for what-if
    cands_blob = json.dumps({
        "candidates": [c.model_dump() for c in assembly.candidates],
        "evidence_ids": [e.id for e in assembly.evidence],
    })
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="candidates",
        tool=None,
        args_json=json.dumps({"evidence_ids": [e.id for e in assembly.evidence]}),
        result_summary=cands_blob,
        ok=True,
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

    # 5. Geo extraction: map points and routes for UI
    map_points: list[dict[str, Any]] = [
        {
            "mandi_id": c.mandi_id,
            "name": c.mandi_name,
            "lat": c.location.lat,
            "lon": c.location.lon,
            "precision": c.location_precision,
        }
        for c in assembly.candidates
    ]
    used_route_ids = {p.option.route_id for p in planset.plans}
    route_paths: dict[str, list[list[float]]] = {}
    for c in assembly.candidates:
        for r in c.routes:
            if r.route_id in used_route_ids and r.route_id not in route_paths:
                route_paths[r.route_id] = [[wp.point.lat, wp.point.lon] for wp in r.waypoints]

    geo_summary = json.dumps({"map_points": map_points, "route_paths": route_paths})
    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="geo",
        tool="extract_geo_points",
        args_json="{}",
        result_summary=geo_summary,
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 6. Explain: Render summary and check numbers
    summary: str | None = None
    explain_summary = "Explainer not installed, summary=None"
    try:
        from app.contracts.evidence import Evidence
        from app.db.db import get_evidence
        from app.explain import allowed_values, check_numbers, render_plan_summary

        all_ev_ids = set(planset.evidence_ids)
        for p in planset.plans:
            all_ev_ids.update(p.option.evidence_ids)

        evidence_index: dict[str, Evidence] = {}
        for eid in all_ev_ids:
            row = get_evidence(eid)
            if row:
                try:
                    evidence_index[eid] = Evidence.model_validate(row)
                except Exception:
                    pass
        for ev in assembly.evidence:
            if ev.id not in evidence_index:
                evidence_index[ev.id] = ev

        text = render_plan_summary(planset, evidence_index)
        allowed = allowed_values(planset, evidence_index)
        bad_numbers = check_numbers(text, allowed)
        if bad_numbers:
            summary = None
            assembly.warnings.append("summary withheld: number check failed")
            explain_summary = f"summary withheld: number check failed ({bad_numbers})"
        else:
            summary = text
            explain_summary = "Plan summary rendered and verified"
    except Exception as exc:
        summary = None
        explain_summary = f"Explainer error: {exc}"

    _record_trace(
        plan_id=plan_id,
        step=step_num,
        node="explain",
        tool="app.explain.render_plan_summary" if summary else None,
        args_json=json.dumps({"summary": summary}),
        result_summary=explain_summary,
        ok=True,
        ts=ts_now,
    )
    step_num += 1

    # 7. Done: Finalize status
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
        map_points=map_points,
        route_paths=route_paths,
    )
