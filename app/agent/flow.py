"""
Agent Orchestrator powered by LangGraph.
Executes autonomous multi-agent workflow for dispatch planning.

Agents:
  - intake_gate_agent: Validates request parameters & checks system mode
  - data_assembly_agent: Gathers candidate mandis, prices, routes, and route weather
  - critic_agent: Audits candidate data completeness and feasibility
  - decision_agent: Evaluates scenario grid, CVaR25, commit points, flips, coverage & geo extraction
  - explainer_agent: Generates approved natural language summary, number audits & finalizes status

Every step is recorded to the agent_trace SQLite table.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.graph.workflow import execute_plan_graph
from app.config import get_settings
from app.contracts import DispatchRequest, PlanSet, SourceStatus
from app.data.assemble import CandidateAssembly, assemble_candidates  # noqa: F401
from app.db.db import get_conn

logger = logging.getLogger(__name__)

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
    why_not_text: str | None = None


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
    Execute the plan orchestration workflow using the multi-agent LangGraph workflow.
    Step cap is 12.
    """
    if os.environ.get("MG_AGENT") == "plain":
        from app.agent.flow_plain import run_plan_flow as run_plain
        return run_plain(req, plan_id, now)

    final_state = execute_plan_graph(req, plan_id, now)

    assembly: CandidateAssembly | None = final_state.get("assembly")
    planset: PlanSet | None = final_state.get("planset")
    warnings = list(assembly.warnings) if assembly else final_state.get("warnings", [])
    source_status = list(assembly.source_status) if assembly else final_state.get("source_status", [])
    cfg = get_settings()
    ts_now = now.isoformat()

    # Calculate why_not_text
    why_not_text: str | None = None
    if planset and assembly:
        try:
            from app.engine import why_not_highest
            from app.explain import allowed_values, check_numbers, render_why_not

            w = why_not_highest(req, assembly.candidates, planset, cfg)
            wn_raw = render_why_not(w)
            wn_allowed = allowed_values(w, {})
            wn_bad = check_numbers(wn_raw, wn_allowed)
            if wn_bad:
                why_not_text = None
                warnings.append("why_not withheld: number check failed")
            else:
                why_not_text = wn_raw
        except Exception as exc:
            why_not_text = None
            logger.warning("why_not calculation failed: %s", exc)

    # Contract trace nodes for UI & main.py compatibility:
    # 1. 'geo': map_points, route_paths
    map_points = final_state.get("map_points", [])
    route_paths = final_state.get("route_paths", {})
    if not map_points and assembly:
        map_points = [
            {
                "mandi_id": c.mandi_id,
                "name": c.mandi_name,
                "lat": c.location.lat,
                "lon": c.location.lon,
                "precision": c.location_precision,
            }
            for c in assembly.candidates
        ]
    if not route_paths and assembly and planset:
        used_route_ids = {p.option.route_id for p in planset.plans}
        for c in assembly.candidates:
            for r in c.routes:
                if r.route_id in used_route_ids and r.route_id not in route_paths:
                    route_paths[r.route_id] = [[wp.point.lat, wp.point.lon] for wp in r.waypoints]

    geo_summary = json.dumps({"map_points": map_points, "route_paths": route_paths})
    _record_trace(plan_id, 10, "geo", "extract_geo_points", "{}", geo_summary, True, ts_now)

    # 2. 'explain': args_json: {"summary": summary, "why_not_text": why_not_text}
    summary = final_state.get("summary")
    _record_trace(
        plan_id,
        11,
        "explain",
        "app.explain.render_plan_summary" if summary else None,
        json.dumps({"summary": summary, "why_not_text": why_not_text}),
        final_state.get("explain_summary", "Summary and why-not rendered"),
        True,
        ts_now,
    )

    # 3. 'candidates': result_summary: {"candidates": [...], "evidence_ids": [...]}
    if assembly:
        cands_blob = json.dumps({
            "candidates": [c.model_dump() for c in assembly.candidates],
            "evidence_ids": [e.id for e in assembly.evidence],
        })
        _record_trace(
            plan_id,
            12,
            "candidates",
            None,
            json.dumps({"evidence_ids": [e.id for e in assembly.evidence]}),
            cands_blob,
            True,
            ts_now,
        )

        # 4. 'assembly_meta': result_summary: {"commodity", "selected_grade", "selected_variety"}
        meta_json = json.dumps({
            "commodity": req.crop,
            "selected_grade": assembly.selected_grade,
            "selected_variety": assembly.selected_variety,
        })
        _record_trace(plan_id, 13, "assembly_meta", None, "{}", meta_json, True, ts_now)

    # 5. 'done': args_json: {"warnings": [...]}
    final_status = final_state.get("status", "abstained")
    _record_trace(
        plan_id,
        14,
        "done",
        None,
        json.dumps({"warnings": warnings}),
        f"Flow completed with status={final_status}",
        True,
        ts_now,
    )

    return FlowResult(
        plan_id=plan_id,
        status=final_status,
        planset=planset,
        assembly_warnings=warnings,
        source_status=source_status,
        trace_id=plan_id,
        summary=summary,
        map_points=map_points,
        route_paths=route_paths,
        why_not_text=why_not_text,
    )
