"""
LangGraph workflow compilation and execution for MandiGuard dispatch planning
and continuous real-time monitoring and dynamic plan revision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

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
from app.agent.graph.tracer import get_tracer
from app.contracts import CandidateInputs, DispatchProgress, DispatchRequest
from app.data.assemble import CandidateAssembly
from app.engine.triggers import WatchState


def entry_router_node(state: PlanWorkflowState) -> PlanWorkflowState:
    """Pass-through router node to direct to initial planning or monitoring branches."""
    return state


def _route_entry_mode(state: PlanWorkflowState) -> str:
    mode = state.get("mode")
    if mode == "watch_tick":
        selected = "monitor_ingress"
        alts = [("intake_gate", f"mode is '{mode}', not 'live' or 'plan'")]
    else:
        selected = "intake_gate"
        alts = [("monitor_ingress", f"mode is '{mode}', not 'watch_tick'")]

    get_tracer().on_conditional_route(
        source_node="entry_router",
        router_fn_name="_route_entry_mode",
        evaluated_condition=f"state['mode'] == '{mode}'",
        selected_dest=selected,
        alternatives=alts,
    )
    return selected


def _route_after_intake(state: PlanWorkflowState) -> str:
    action = state.get("next_action")
    if action == "abort":
        selected = END
        alts = [("data_assembly", f"request validation failed: {state.get('error')}")]
    else:
        selected = "data_assembly"
        alts = [(END, "request validation succeeded")]

    get_tracer().on_conditional_route(
        source_node="intake_gate",
        router_fn_name="_route_after_intake",
        evaluated_condition=f"state['next_action'] == '{action}'",
        selected_dest=selected,
        alternatives=alts,
    )
    return selected


def _route_after_monitor_ingress(state: PlanWorkflowState) -> str:
    action = state.get("next_action")
    if action == "abort":
        selected = END
        alts = [("data_refresh", f"plan hydration or gate failed: {state.get('error')}")]
    else:
        selected = "data_refresh"
        alts = [(END, "plan hydrated successfully from DB or memory state")]

    get_tracer().on_conditional_route(
        source_node="monitor_ingress",
        router_fn_name="_route_after_monitor_ingress",
        evaluated_condition=f"state['next_action'] == '{action}'",
        selected_dest=selected,
        alternatives=alts,
    )
    return selected


def _route_after_change_detection(state: PlanWorkflowState) -> str:
    action = state.get("next_action")
    dec = state.get("trigger_decision")
    reason = dec.reason if dec else "N/A"

    if action == "replan":
        selected = "replanning"
        alts = [("retain_plan", f"triggers persisted across hysteresis and gain >= margin: {reason}")]
    else:
        selected = "retain_plan"
        alts = [("replanning", f"triggers not persisted, within cooldown, or gain < margin: {reason}")]

    get_tracer().on_conditional_route(
        source_node="change_detection",
        router_fn_name="_route_after_change_detection",
        evaluated_condition=f"state['next_action'] == '{action}' (propose_replan={dec.propose_replan if dec else False})",
        selected_dest=selected,
        alternatives=alts,
    )
    return selected


def _instrument_node(name: str, fn: Callable[[PlanWorkflowState], PlanWorkflowState]) -> Callable[[PlanWorkflowState], PlanWorkflowState]:
    """Wraps an agent node function to emit live execution traces."""
    def _wrapped(state: PlanWorkflowState) -> PlanWorkflowState:
        t0 = get_tracer().on_node_start(name, state)
        try:
            out = fn(state)
            get_tracer().on_node_end(name, t0, out, "SUCCESS")
            return out
        except Exception as exc:
            get_tracer().on_node_end(name, t0, {"error": str(exc)}, "FAILED")
            raise
    return _wrapped


def build_plan_graph() -> Any:
    """
    Construct and compile the multi-agent StateGraph supporting initial planning
    and continuous real-time monitoring and replanning.
    """
    workflow = StateGraph(PlanWorkflowState)

    # 1. Register router
    workflow.add_node("entry_router", entry_router_node)

    # 2. Register initial planning agent nodes (instrumented)
    workflow.add_node("intake_gate", _instrument_node("intake_gate", intake_gate_agent))
    workflow.add_node("data_assembly", _instrument_node("data_assembly", data_assembly_agent))
    workflow.add_node("critic", _instrument_node("critic", critic_agent))
    workflow.add_node("decision", _instrument_node("decision", decision_agent))
    workflow.add_node("explainer", _instrument_node("explainer", explainer_agent))

    # 3. Register monitoring & replanning agent nodes (instrumented)
    workflow.add_node("monitor_ingress", _instrument_node("monitor_ingress", monitor_ingress_agent))
    workflow.add_node("data_refresh", _instrument_node("data_refresh", data_refresh_agent))
    workflow.add_node("change_detection", _instrument_node("change_detection", change_detection_agent))
    workflow.add_node("replanning", _instrument_node("replanning", replanning_agent))
    workflow.add_node("replan_explainer", _instrument_node("replan_explainer", replan_explainer_agent))
    workflow.add_node("retain_plan", _instrument_node("retain_plan", retain_plan_agent))

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

    get_tracer().reset_run()
    config = {"configurable": {"thread_id": plan_id}}
    final_state = graph.invoke(initial_state, config=config)
    get_tracer().print_execution_summary(final_state)
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

    get_tracer().reset_run()
    final_state = graph.invoke(tick_input, config=config)
    get_tracer().print_execution_summary(final_state)
    return final_state
