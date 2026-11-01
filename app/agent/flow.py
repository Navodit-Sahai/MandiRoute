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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app import guards
from app.config import get_settings
from app.contracts import CoverageReport, DispatchRequest, PlanSet, SourceStatus
from app.data.assemble import CandidateAssembly, assemble_candidates
from app.db.db import get_conn
from app.engine.plans import build_plan_set
from app.agent.graph.workflow import execute_plan_graph

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
    map_points: list[dict[str, Any]] = field(default_factory=list)
    route_paths: dict[str, list[list[float]]] = field(default_factory=dict)


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
    Execute the plan orchestration workflow using the multi-agent LangGraph workflow.
    Step cap is 12.
    """
    final_state = execute_plan_graph(req, plan_id, now)

    assembly = final_state.get("assembly")
    planset = final_state.get("planset")
    warnings = list(assembly.warnings) if assembly else final_state.get("warnings", [])
    source_status = list(assembly.source_status) if assembly else final_state.get("source_status", [])

    return FlowResult(
        plan_id=plan_id,
        status=final_state.get("status", "abstained"),
        planset=planset,
        assembly_warnings=warnings,
        source_status=source_status,
        trace_id=plan_id,
        summary=final_state.get("summary"),
        map_points=final_state.get("map_points", []),
        route_paths=final_state.get("route_paths", {}),
    )
