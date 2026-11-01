"""
LangGraph workflow compilation and execution for MandiGuard dispatch planning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.agent.graph.agents import (
    critic_agent,
    data_assembly_agent,
    decision_agent,
    explainer_agent,
    intake_gate_agent,
)
from app.agent.graph.state import PlanWorkflowState
from app.contracts import DispatchRequest


def _route_after_intake(state: PlanWorkflowState) -> str:
    if state.get("next_action") == "abort":
        return END
    return "data_assembly"


def build_plan_graph() -> Any:
    """
    Construct and compile the multi-agent StateGraph.
    """
    workflow = StateGraph(PlanWorkflowState)

    # Register autonomous agent nodes
    workflow.add_node("intake_gate", intake_gate_agent)
    workflow.add_node("data_assembly", data_assembly_agent)
    workflow.add_node("critic", critic_agent)
    workflow.add_node("decision", decision_agent)
    workflow.add_node("explainer", explainer_agent)

    # Define entry point
    workflow.set_entry_point("intake_gate")

    # Dynamic routing between agents
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

    # Compile with memory checkpointing
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
    Execute the agentic LangGraph workflow from an initial state.
    """
    graph = get_plan_graph()
    initial_state: PlanWorkflowState = {
        "plan_id": plan_id,
        "request": req,
        "now": now,
        "step_num": 1,
        "mode": "live",
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
    }

    config = {"configurable": {"thread_id": plan_id}}
    final_state = graph.invoke(initial_state, config=config)
    return final_state
