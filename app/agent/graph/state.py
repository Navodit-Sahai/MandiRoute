"""
State definition for the MandiGuard LangGraph agentic workflow.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TypedDict

from app.contracts import CandidateInputs, CoverageReport, DispatchRequest, PlanSet, SourceStatus
from app.data.assemble import CandidateAssembly


class PlanWorkflowState(TypedDict, total=False):
    """
    Typed state shared across all agents in the LangGraph workflow.
    """
    plan_id: str
    request: DispatchRequest
    now: datetime
    step_num: int
    mode: str
    assembly: Optional[CandidateAssembly]
    critic_notes: list[str]
    planset: Optional[PlanSet]
    map_points: list[dict[str, Any]]
    route_paths: dict[str, list[list[float]]]
    summary: Optional[str]
    status: str  # "awaiting_approval" | "abstained" | "error"
    warnings: list[str]
    source_status: list[SourceStatus]
    trace_id: str
    next_action: Optional[str]
    error: Optional[str]

