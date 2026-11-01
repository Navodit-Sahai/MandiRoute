"""
State definition for the MandiGuard LangGraph agentic workflow.
Supports initial plan synthesis, continuous monitoring, change detection, and dynamic replanning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TypedDict

from app.contracts import (
    CandidateInputs,
    CoverageReport,
    DispatchProgress,
    PlanDiff,
    PlanSet,
    DispatchRequest,
    SourceStatus,
)
from app.data.assemble import CandidateAssembly
from app.engine.triggers import TriggerDecision, WatchState


class PlanWorkflowState(TypedDict, total=False):
    """
    Typed state shared across all agents in the LangGraph workflow.
    """
    plan_id: str
    request: DispatchRequest
    now: datetime
    step_num: int
    mode: str  # "plan" | "watch_tick" | "live"
    active_version: int
    assembly: Optional[CandidateAssembly]
    critic_notes: list[str]
    planset: Optional[PlanSet]
    map_points: list[dict[str, Any]]
    route_paths: dict[str, list[list[float]]]
    summary: Optional[str]
    status: str  # "draft" | "awaiting_approval" | "dispatched" | "monitoring" | "replan_pending" | "abstained" | "error"
    warnings: list[str]
    source_status: list[SourceStatus]
    trace_id: str
    next_action: Optional[str]
    error: Optional[str]

    # Monitoring & Dynamic Revision Fields
    progress: Optional[DispatchProgress]
    watch_state: Optional[WatchState]
    trigger_decision: Optional[TriggerDecision]
    diff: Optional[PlanDiff]
    diff_summary: Optional[str]
    history: list[dict[str, Any]]
