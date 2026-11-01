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
from app.contracts import DispatchProgress
from app.db.db import get_conn
from app.engine.triggers import TriggerDecision, WatchState


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


# =============================================================================
# Initial Planning Agents
# =============================================================================

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
        "active_version": 1,
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


# =============================================================================
# Continuous Monitoring & Dynamic Replanning Agents
# =============================================================================

def monitor_ingress_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Monitor Ingress Agent:
    Validates that a plan exists (in memory state or hydrated from SQLite persistence),
    checks plan status, and ensures dispatch progress and watch state are initialized.
    """
    plan_id = state["plan_id"]
    now = state.get("now", datetime.now())
    ts_now = now.isoformat()
    step_num = state.get("step_num", 1)

    planset = state.get("planset")
    req = state.get("request")
    watch_state = state.get("watch_state")
    progress = state.get("progress")

    # If state is missing core objects or status changed, sync from persistent storage
    status = state.get("status", "monitoring")
    db_data = tools.load_plan_from_db_tool(plan_id)
    if db_data is not None:
        status = db_data.get("status", status)
        if planset is None and db_data.get("planset"):
            planset = db_data["planset"]
        if req is None and db_data.get("request"):
            req = db_data["request"]
        if watch_state is None and db_data.get("watch_state"):
            watch_state = db_data["watch_state"]
        if progress is None and db_data.get("progress"):
            progress = db_data["progress"]

    if planset is None or req is None:
        record_trace(
            plan_id, step_num, "monitor_ingress", "load_plan_from_db",
            json.dumps({"plan_id": plan_id}), f"Plan '{plan_id}' not found in database", False, ts_now
        )
        return {
            **state,
            "step_num": step_num + 1,
            "status": "error",
            "error": f"Plan '{plan_id}' not found",
            "next_action": "abort",
        }

    if watch_state is None:
        watch_state = WatchState()

    if progress is None:
        progress = DispatchProgress(
            plan_id=plan_id,
            dispatched_at=now.isoformat(),
            reported_delay_min=0.0,
            source="route_estimate",
        )

    ingress_summary = f"Monitoring active for plan {plan_id} v{planset.version} (status: {state.get('status', 'monitoring')})"
    record_trace(
        plan_id, step_num, "monitor_ingress", None,
        json.dumps({"delay_min": progress.reported_delay_min, "waypoint": progress.last_waypoint_idx}),
        ingress_summary, True, ts_now
    )
    step_num += 1

    return {
        **state,
        "request": req,
        "planset": planset,
        "active_version": planset.version,
        "status": status,
        "watch_state": watch_state,
        "progress": progress,
        "step_num": step_num,
        "next_action": "refresh",
    }


def data_refresh_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Data Refresh Agent:
    Autonomously determines which mandi IDs and routes belong to the current plan set,
    and invokes targeted refresh of prices, weather, and evidence.
    """
    plan_id = state["plan_id"]
    req = state["request"]
    planset = state["planset"]
    now = state["now"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 2)

    # If assembly with fresh candidates was explicitly passed into state (e.g. from test or runner), reuse it
    assembly = state.get("assembly")
    if assembly and assembly.candidates:
        refresh_summary = f"Reusing {len(assembly.candidates)} fresh candidates from state"
        record_trace(plan_id, step_num, "data_refresh", None, "{}", refresh_summary, True, ts_now)
        step_num += 1
        return {
            **state,
            "step_num": step_num,
            "next_action": "change_detection",
        }

    # Otherwise extract option IDs from PlanSet
    option_ids = [p.option.option_id for p in planset.plans if p.option]
    args = json.dumps({"option_ids": option_ids, "now": ts_now})

    try:
        fresh_assembly = tools.refresh_monitoring_data_tool(
            req=req,
            plan_option_ids=option_ids,
            now=now,
        )
        refresh_summary = (
            f"Refreshed {len(fresh_assembly.candidates)} candidates, "
            f"{len(fresh_assembly.evidence)} evidence items, {len(fresh_assembly.warnings)} warnings"
        )
        ok = True
    except Exception as exc:
        refresh_summary = f"Data refresh failed: {exc}"
        ok = False
        record_trace(plan_id, step_num, "data_refresh", "refresh_for_monitoring", args, refresh_summary, False, ts_now)
        return {
            **state,
            "step_num": step_num + 1,
            "next_action": "retain",
            "error": str(exc),
        }

    record_trace(plan_id, step_num, "data_refresh", "refresh_for_monitoring", args, refresh_summary, ok, ts_now)
    step_num += 1

    return {
        **state,
        "assembly": fresh_assembly,
        "warnings": list(fresh_assembly.warnings),
        "source_status": list(fresh_assembly.source_status),
        "step_num": step_num,
        "next_action": "change_detection",
    }


def change_detection_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Change Detection Agent:
    Compares latest observations against current plan parameters, evaluates triggers (T1-T5),
    tracks hysteresis and cooldowns, and determines whether dynamic replanning is justified.
    """
    plan_id = state["plan_id"]
    req = state["request"]
    planset = state["planset"]
    assembly = state["assembly"]
    progress = state["progress"]
    watch_state = state["watch_state"]
    now = state["now"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 3)

    fresh_cands = assembly.candidates if assembly else []
    fresh_evidence = assembly.evidence if assembly else []

    decision = tools.evaluate_change_and_triggers_tool(
        prev=planset,
        req=req,
        fresh_cands=fresh_cands,
        fresh_evidence=fresh_evidence,
        progress=progress,
        state=watch_state,
        now=now,
    )

    det_args = json.dumps({
        "fired": decision.fired,
        "persisted": decision.persisted,
        "propose_replan": decision.propose_replan,
    })
    det_summary = f"Triggers fired: {decision.fired}; Persisted: {decision.persisted}; Propose: {decision.propose_replan}. Reason: {decision.reason}"

    record_trace(
        plan_id, step_num, "change_detection", "evaluate_triggers_and_recompute",
        det_args, det_summary, True, ts_now
    )
    step_num += 1

    next_action = "replan" if decision.propose_replan else "retain"

    return {
        **state,
        "trigger_decision": decision,
        "watch_state": decision.new_state,
        "step_num": step_num,
        "next_action": next_action,
    }


def replanning_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Replanning Agent:
    Activates the newly generated PlanSet (v+1) and PlanDiff, updates geospatial routing,
    persists the revision to SQLite with status 'replan_pending', and records the audit event.
    """
    plan_id = state["plan_id"]
    now = state["now"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 4)
    decision = state["trigger_decision"]
    cur_planset = state["planset"]
    assembly = state["assembly"]
    progress = state.get("progress")

    new_planset = decision.new_plan_set
    diff = decision.diff

    # Update history
    history = list(state.get("history", []))
    history.append(cur_planset.model_dump())

    # Extract updated geo features
    geo_features = tools.extract_geo_features_tool(assembly.candidates, new_planset)
    map_points = geo_features["map_points"]
    route_paths = geo_features["route_paths"]

    # Persist version to DB (status replan_pending awaiting human approval)
    tools.save_plan_version_tool(
        plan_id=plan_id,
        version=new_planset.version,
        planset=new_planset,
        diff=diff,
        status="replan_pending",
        now=now,
    )

    # Save updated watch state and audit event
    tools.save_watch_state_tool(
        plan_id=plan_id,
        watch_state=decision.new_state,
        progress=progress,
        event_kind="replan_proposed",
        event_detail={
            "old_version": cur_planset.version,
            "new_version": new_planset.version,
            "reason": diff.primary_reason if diff else decision.reason,
            "recommendation_changed": diff.recommendation_changed if diff else False,
        },
        now=now,
    )

    replan_summary = f"PlanSet revision v{new_planset.version} generated: {diff.primary_reason if diff else decision.reason}"
    record_trace(plan_id, step_num, "replanning", "save_plan_version", "{}", replan_summary, True, ts_now)
    step_num += 1

    return {
        **state,
        "planset": new_planset,
        "diff": diff,
        "active_version": new_planset.version,
        "history": history,
        "map_points": map_points,
        "route_paths": route_paths,
        "status": "replan_pending",
        "step_num": step_num,
        "next_action": "replan_explainer",
    }


def replan_explainer_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Replan Explainer Agent:
    Renders the deterministic diff summary explaining the reasons for proposed revision
    per AGENT §7 wording rules.
    """
    plan_id = state["plan_id"]
    now = state["now"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 5)
    diff = state.get("diff")
    assembly = state.get("assembly")

    evidence_index = {e.id: e for e in assembly.evidence} if assembly else {}
    diff_summary, tool_msg = tools.render_diff_summary_tool(diff, evidence_index) if diff else (None, "No diff")

    record_trace(
        plan_id, step_num, "replan_explainer", "render_diff",
        "{}", tool_msg, True, ts_now
    )
    step_num += 1

    return {
        **state,
        "diff_summary": diff_summary,
        "step_num": step_num,
        "next_action": "complete",
    }


def retain_plan_agent(state: PlanWorkflowState) -> PlanWorkflowState:
    """
    Retain Plan Agent:
    Retains current plan without replanning when triggers are inactive, below hysteresis,
    within cooldown, or have insufficient gain margin. Records monitoring audit tick.
    """
    plan_id = state["plan_id"]
    now = state["now"]
    ts_now = now.isoformat()
    step_num = state.get("step_num", 4)
    decision = state.get("trigger_decision")
    progress = state.get("progress")
    watch_state = decision.new_state if decision else state.get("watch_state", WatchState())

    # Update watch state and tick event in database
    tools.save_watch_state_tool(
        plan_id=plan_id,
        watch_state=watch_state,
        progress=progress,
        event_kind="tick",
        event_detail={
            "fired": decision.fired if decision else [],
            "persisted": decision.persisted if decision else [],
            "reason": decision.reason if decision else "No change",
        },
        now=now,
    )

    retain_summary = f"Plan retained. {decision.reason if decision else 'No triggers fired'}"
    record_trace(plan_id, step_num, "retain_plan", "save_watch_state", "{}", retain_summary, True, ts_now)
    step_num += 1

    cur_status = state.get("status")
    if cur_status not in ("replan_pending", "abstained", "rejected"):
        cur_status = "monitoring"

    return {
        **state,
        "diff": state.get("diff"),
        "diff_summary": state.get("diff_summary"),
        "status": cur_status,
        "step_num": step_num,
        "next_action": "complete",
    }
