"""
LangGraph workflow compilation and execution for MandiGuard dispatch planning
and continuous real-time monitoring and dynamic plan revision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.agent.graph.agents import (
    change_detection_agent,
    critic_agent,
    data_assembly_agent,
    data_refresh_agent,
    decision_agent,
    explainer_agent,
    intake_gate_agent,
    monitor_ingress_agent,
    replan_explainer_agent,
    replanning_agent,
    retain_plan_agent,
)
from app.agent.graph.state import PlanWorkflowState
from app.contracts import CandidateInputs, DispatchProgress, DispatchRequest
from app.data.assemble import CandidateAssembly
from app.engine.triggers import WatchState


def entry_router_node(state: PlanWorkflowState) -> PlanWorkflowState:
    """Pass-through router node to direct to initial planning or monitoring branches."""
    return state


def _route_entry_mode(state: PlanWorkflowState) -> str:
    if state.get("mode") == "watch_tick":
        return "monitor_ingress"
    return "intake_gate"


def _route_after_intake(state: PlanWorkflowState) -> str:
    if state.get("next_action") == "abort":
        return END
    return "data_assembly"


def _route_after_monitor_ingress(state: PlanWorkflowState) -> str:
    if state.get("next_action") == "abort":
        return END
    return "data_refresh"


def _route_after_change_detection(state: PlanWorkflowState) -> str:
    action = state.get("next_action")
    if action == "replan":
        return "replanning"
    return "retain_plan"


def build_plan_graph() -> Any:
    """
    Construct and compile the multi-agent StateGraph supporting initial planning
    and continuous real-time monitoring and replanning.
    """
    workflow = StateGraph(PlanWorkflowState)

    # 1. Register router
    workflow.add_node("entry_router", entry_router_node)

    # 2. Register initial planning agent nodes
    workflow.add_node("intake_gate", intake_gate_agent)
    workflow.add_node("data_assembly", data_assembly_agent)
    workflow.add_node("critic", critic_agent)
    workflow.add_node("decision", decision_agent)
    workflow.add_node("explainer", explainer_agent)

    # 3. Register monitoring & replanning agent nodes
    workflow.add_node("monitor_ingress", monitor_ingress_agent)
    workflow.add_node("data_refresh", data_refresh_agent)
    workflow.add_node("change_detection", change_detection_agent)
    workflow.add_node("replanning", replanning_agent)
    workflow.add_node("replan_explainer", replan_explainer_agent)
    workflow.add_node("retain_plan", retain_plan_agent)

    # 4. Entry Point & Routing
    workflow.set_entry_point("entry_router")

    workflow.add_conditional_edges(
        "entry_router",
        _route_entry_mode,
        {
            "intake_gate": "intake_gate",
            "monitor_ingress": "monitor_ingress",
        },
    )

    # 5. Planning workflow transitions
    workflow.add_conditional_edges(
        "intake_gate",
        _route_after_intake,
        {
            "data_assembly": "data_assembly",
            END: END,
        },
    )
    workflow.add_edge("data_assembly", "critic")
    workflow.add_edge("critic", "decision")
    workflow.add_edge("decision", "explainer")
    workflow.add_edge("explainer", END)

    # 6. Monitoring & replanning workflow transitions
    workflow.add_conditional_edges(
        "monitor_ingress",
        _route_after_monitor_ingress,
        {
            "data_refresh": "data_refresh",
            END: END,
        },
    )
    workflow.add_edge("data_refresh", "change_detection")
    workflow.add_conditional_edges(
        "change_detection",
        _route_after_change_detection,
        {
            "replanning": "replanning",
            "retain_plan": "retain_plan",
        },
    )
    workflow.add_edge("replanning", "replan_explainer")
    workflow.add_edge("replan_explainer", END)
    workflow.add_edge("retain_plan", END)

    # 7. Compile with persistent memory checkpointing
    checkpointer = InMemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# Singleton compiled graph instance
_PLAN_GRAPH = None


def get_plan_graph() -> Any:
    global _PLAN_GRAPH
    if _PLAN_GRAPH is None:
        _PLAN_GRAPH = build_plan_graph()
    return _PLAN_GRAPH


def execute_plan_graph(req: DispatchRequest, plan_id: str, now: datetime) -> PlanWorkflowState:
    """
    Execute the agentic LangGraph workflow for initial plan generation.
    """
    graph = get_plan_graph()
    initial_state: PlanWorkflowState = {
        "plan_id": plan_id,
        "request": req,
        "now": now,
        "step_num": 1,
        "mode": "live",
        "active_version": 1,
        "assembly": None,
        "critic_notes": [],
        "planset": None,
        "map_points": [],
        "route_paths": {},
        "summary": None,
        "status": "pending",
        "warnings": [],
        "source_status": [],
        "trace_id": plan_id,
        "next_action": None,
        "error": None,
        "history": [],
        "diff": None,
        "diff_summary": None,
        "trigger_decision": None,
        "progress": None,
        "watch_state": None,
    }

    config = {"configurable": {"thread_id": plan_id}}
    final_state = graph.invoke(initial_state, config=config)
    return final_state


def execute_monitoring_tick(
    plan_id: str,
    progress: Optional[DispatchProgress] = None,
    now: Optional[datetime] = None,
    assembly: Optional[CandidateAssembly] = None,
    watch_state: Optional[WatchState] = None,
) -> PlanWorkflowState:
    """
    Execute a single monitoring tick through the LangGraph workflow.
    Evaluates current observations, updates triggers/hysteresis, and replans if justified.
    """
    graph = get_plan_graph()
    current_now = now or datetime.now(timezone.utc)
    config = {"configurable": {"thread_id": plan_id}}

    tick_input: PlanWorkflowState = {
        "plan_id": plan_id,
        "now": current_now,
        "mode": "watch_tick",
    }
    if progress is not None:
        tick_input["progress"] = progress
    if assembly is not None:
        tick_input["assembly"] = assembly
    if watch_state is not None:
        tick_input["watch_state"] = watch_state

    final_state = graph.invoke(tick_input, config=config)
    return final_state
