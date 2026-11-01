"""
Direct in-process client for Streamlit UI (Stream C).

Connects the Streamlit frontend directly to the LangGraph workflow,
decision engine, and SQLite state without needing an external FastAPI HTTP server or ports.
Zero HTTP calls to localhost:8000.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, UTC
from typing import Any

from app import guards
from app.config import get_settings
from app.contracts import DispatchRequest
from app.api.main import (
    ProgressInput,
    WhatIfRequest,
    apply_plan_approval,
    get_plan as api_get_plan,
    list_plans as api_list_plans,
    plan_whatif as api_plan_whatif,
    get_plan_diff as api_get_plan_diff,
    get_plan_watch as api_get_plan_watch,
    get_plan_events as api_get_plan_events,
    get_plan_trace as api_get_plan_trace,
    update_progress as api_update_progress,
    tick_plan_watch as api_tick_plan_watch,
    create_plan as api_create_plan,
    get_sources_health as api_get_sources_health,
    get_evidence_by_id as api_get_evidence_by_id,
)

logger = logging.getLogger(__name__)


def api_url() -> str:
    return "in-process"


def list_plans() -> list[dict[str, Any]]:
    """List recent plans from the database for the monitoring dashboard."""
    return api_list_plans()


def get_health() -> dict[str, Any]:
    """Return backend health in-process."""
    return {
        "status": "ok",
        "mode": guards.mode(),
        "time": datetime.now(UTC).isoformat(),
        "banner": guards.banner(),
    }


def get_sources_health() -> list[dict[str, Any]]:
    """Return data source health in-process."""
    try:
        return [s.model_dump() for s in api_get_sources_health()]
    except Exception as e:
        logger.warning("source_health failed: %s", e)
        return []


def get_assumptions() -> dict[str, Any]:
    """Return frozen engine settings/assumptions in-process."""
    return get_settings().model_dump()


def post_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute LangGraph planning workflow directly and return serialized PlanResponse."""
    req = DispatchRequest.model_validate(payload)
    resp = api_create_plan(req)
    return resp.model_dump()


def get_plan(plan_id: str) -> dict[str, Any]:
    """Load plan details, latest planset, diff, and traces directly from SQLite."""
    return api_get_plan(plan_id)


def get_plan_trace(plan_id: str) -> list[dict[str, Any]]:
    """Load execution trace steps directly from agent_trace table."""
    return api_get_plan_trace(plan_id)


def get_evidence(evidence_id: str) -> dict[str, Any]:
    """Load evidence item directly in-process."""
    try:
        return api_get_evidence_by_id(evidence_id)
    except Exception:
        return {}


_EVIDENCE_CACHE: dict[str, dict[str, Any]] = {}


def get_evidence_cached(evidence_id: str) -> dict[str, Any]:
    """In-memory cached lookup for evidence records to minimize overhead on multi-plan screens."""
    if evidence_id not in _EVIDENCE_CACHE:
        try:
            _EVIDENCE_CACHE[evidence_id] = get_evidence(evidence_id)
        except Exception:
            _EVIDENCE_CACHE[evidence_id] = {}
    return _EVIDENCE_CACHE[evidence_id]


def post_dispatch(plan_id: str, by: str = "operator") -> dict[str, Any]:
    """Approve dispatch and initiate active monitoring in-process."""
    return apply_plan_approval(plan_id, version=1, decision="approve", kind="dispatch", by=by)


def post_progress(
    plan_id: str,
    payload_or_delay: dict[str, Any] | float = 0.0,
    waypoint_idx: int | None = None,
    source: str = "checkin_button",
) -> dict[str, Any]:
    """Report check-in delay or waypoint progress in-process."""
    if isinstance(payload_or_delay, dict):
        body = ProgressInput.model_validate(payload_or_delay)
    else:
        body = ProgressInput(
            reported_delay_min=float(payload_or_delay),
            last_waypoint_idx=waypoint_idx,
            source=source,
        )
    return api_update_progress(plan_id, body)


def post_watch_tick(plan_id: str, *, fresh: int = 0) -> dict[str, Any]:
    """Trigger a monitoring tick through the monitoring workflow in-process."""
    return api_tick_plan_watch(plan_id, force=bool(fresh))


def post_approval(
    plan_id: str,
    kind: str,
    decision: str,
    *,
    version: int | None = None,
    by: str = "operator",
) -> dict[str, Any]:
    """Approve or reject a proposed plan revision or dispatch in-process."""
    return apply_plan_approval(plan_id, version or 1, decision, kind, by=by)


# Alias for compatibility with callers expecting post_approve
post_approve = post_approval


def get_diff(plan_id: str, *, from_ver: int | None = None, to_ver: int | None = None) -> dict[str, Any] | None:
    """Fetch plan revision diff in-process."""
    try:
        return api_get_plan_diff(plan_id, from_version=from_ver, to_version=to_ver)
    except Exception:
        return None


def get_watch(plan_id: str) -> dict[str, Any]:
    """Fetch watchdog status and recent events in-process."""
    return api_get_plan_watch(plan_id)


def get_events(plan_id: str) -> list[dict[str, Any]]:
    """Fetch watchdog event timeline in-process."""
    return api_get_plan_events(plan_id)


def post_whatif(plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute what-if simulation on stored candidates in-process."""
    body = WhatIfRequest.model_validate(payload)
    return api_plan_whatif(plan_id, body)


class _InProcessRequestsStub:
    """Fallback stub so any legacy requests.get call resolves in-process."""
    @staticmethod
    def get(url: str, **kwargs) -> Any:
        class _Resp:
            status_code = 200
            def json(self):
                if "/trace" in url:
                    parts = url.split("/plans/")[1].split("/")[0]
                    return get_plan_trace(parts)
                if "/plans" in url:
                    return list_plans()
                return {}
            def raise_for_status(self):
                pass
        return _Resp()

requests = _InProcessRequestsStub()
