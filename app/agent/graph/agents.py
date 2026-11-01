"""
Autonomous agents for MandiGuard LangGraph workflow.
Each agent has a specific mandate, evaluates state, and autonomously decides
when and how to invoke tools and determine the next workflow action.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.agent.graph import tools
from app.agent.graph.state import PlanWorkflowState
from app.db.db import get_conn


def record_trace(
    plan_id: str,
    step: int,
    node: str,
    tool: Optional[str],
    args_json: str,
    result_summary: str,
    ok: bool,
    ts: str,
) -> None:
    """Record an agent execution step to the agent_trace SQLite table."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agent_trace(plan_id, step, node, tool, args_json, result_summary, ok, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (plan_id, step, node, tool, args_json, result_summary, 1 if ok else 0, ts),
        )


def intake_gate_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Intake & Gate Agent:
    Validates incoming request parameters and verifies operating mode integrity.
    """
    req = state["request"]
    now = state["now"]
    plan_id = state["plan_id"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 1)

    # 1. Autonomously decide to validate input request
    val_res = tools.validate_request_tool(req)
    intake_args = json.dumps({"crop": req.crop, "origin": req.origin_label, "quantity_qtl": req.quantity_qtl})
    
    if not val_res.get("valid", False):
        err_msg = val_res.get("error", "Invalid request")
        record_trace(plan_id, step_num, "intake", None, intake_args, f"Validation failed: {err_msg}", False, ts_now)
        return {
            **state,
            "step_num": step_num + 1,
            "status": "error",
            "error": err_msg,
            "next_action": "abort",
        }

    record_trace(plan_id, step_num, "intake", None, intake_args, "Request input validated", True, ts_now)
    step_num += 1

    # 2. Autonomously decide to verify system operating mode
    mode_res = tools.check_system_mode_tool()
    current_mode = mode_res["mode"]
    gate_args = json.dumps({"mode": current_mode})
    record_trace(plan_id, step_num, "source_gate", "app.guards.mode", gate_args, f"Operating mode is '{current_mode}'", True, ts_now)
    step_num += 1

    return {
        **state,
        "step_num": step_num,
        "mode": current_mode,
        "next_action": "assemble",
    }


def data_assembly_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Data Assembly Agent:
    Autonomously coordinates real-time data acquisition from external providers
    (mandi prices, geocoding, routing, and weather).
    """
    req = state["request"]
    now = state["now"]
    plan_id = state["plan_id"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 3)

    assemble_args = json.dumps({"origin": [req.origin.lat, req.origin.lon], "max_km": req.max_distance_km})

    try:
        assembly = tools.assemble_candidates_tool(req, now)
        summary = (
            f"Assembled {len(assembly.candidates)} candidates, "
            f"{len(assembly.evidence)} evidence items, {len(assembly.warnings)} warnings"
        )
        ok = True
    except Exception as exc:
        summary = f"Assembly failed: {exc}"
        ok = False
        record_trace(plan_id, step_num, "assemble", "app.data.assemble.assemble_candidates", assemble_args, summary, False, ts_now)
        raise

    record_trace(plan_id, step_num, "assemble", "app.data.assemble.assemble_candidates", assemble_args, summary, ok, ts_now)
    step_num += 1

    return {
        **state,
        "step_num": step_num,
        "assembly": assembly,
        "warnings": list(assembly.warnings),
        "source_status": list(assembly.source_status),
        "next_action": "critic",
    }


def critic_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Critic Agent:
    Inspects candidate assembly data and audits completeness, freshness, and sanity.
    """
    assembly = state.get("assembly")
    critic_res = tools.critic_audit_tool(assembly) if assembly else {"can_optimize": False, "notes": ["No assembly"]}
    
    return {
        **state,
        "critic_notes": critic_res["notes"],
        "next_action": "decide",
    }


def decision_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Decision Agent:
    Autonomously invokes the mathematical decision engine to evaluate the 9-scenario grid,
    build the robust plan set (A/B/C), and extract spatial geometries for visualization.
    """
    req = state["request"]
    now = state["now"]
    plan_id = state["plan_id"]
    assembly = state["assembly"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 4)

    # 1. Evaluate candidate options and construct plan set
    decide_args = json.dumps({"candidate_count": len(assembly.candidates), "risk_mode": req.risk_mode})
    planset = tools.evaluate_and_build_plans_tool(req, assembly, now, plan_id, version=1)
    decide_summary = f"PlanSet v1 created with {len(planset.plans)} plans (coverage: {planset.coverage.grade})"
    
    record_trace(plan_id, step_num, "decide", "app.engine.build_plan_set", decide_args, decide_summary, True, ts_now)
    step_num += 1

    # 2. Extract geospatial map points and route paths
    geo_features = tools.extract_geo_features_tool(assembly.candidates, planset)
    map_points = geo_features["map_points"]
    route_paths = geo_features["route_paths"]

    geo_summary = json.dumps({"map_points": map_points, "route_paths": route_paths})
    record_trace(plan_id, step_num, "geo", "extract_geo_points", "{}", geo_summary, True, ts_now)
    step_num += 1

    return {
        **state,
        "step_num": step_num,
        "planset": planset,
        "map_points": map_points,
        "route_paths": route_paths,
        "next_action": "explain",
    }


def explainer_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Explainer Agent:
    Synthesizes the plan set into natural language summary adhering to AGENT §7 wording rules,
    audits numeric values, and finalizes workflow status.
    """
    now = state["now"]
    plan_id = state["plan_id"]
    planset = state["planset"]
    assembly = state["assembly"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 6)

    # 1. Generate explanation summary
    summary, explain_summary = tools.generate_plan_summary_tool(planset, assembly)
    record_trace(
        plan_id,
        step_num,
        "explain",
        "app.explain.render_plan_summary" if summary else None,
        "{}",
        explain_summary,
        True,
        ts_now,
    )
    step_num += 1

    # 2. Finalize status
    is_abstained = planset.coverage.grade == "ABSTAIN" or len(planset.plans) == 0
    final_status = "abstained" if is_abstained else "awaiting_approval"
    record_trace(
        plan_id,
        step_num,
        "done",
        None,
        "{}",
        f"Flow completed with status={final_status}",
        True,
        ts_now,
    )

    return {
        **state,
        "step_num": step_num + 1,
        "summary": summary,
        "status": final_status,
        "next_action": "complete",
    }
