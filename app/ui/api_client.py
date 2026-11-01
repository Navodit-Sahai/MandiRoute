"""
HTTP API client for Streamlit UI (Stream C).

Calls the MandiGuard FastAPI server exclusively; imports no engine/data code.
Base URL configurable via MG_API_URL (default: http://localhost:8000).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests


def api_url() -> str:
    return os.environ.get("MG_API_URL", "http://localhost:8000").rstrip("/")


def get_health() -> dict[str, Any]:
    url = f"{api_url()}/health"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def get_sources_health() -> list[dict[str, Any]]:
    url = f"{api_url()}/sources/health"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_assumptions() -> dict[str, Any]:
    url = f"{api_url()}/assumptions"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def post_plan(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{api_url()}/plan"
    # Timeout 120s as assembly can take 30-70s cold
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def list_plans() -> list[dict[str, Any]]:
    url = f"{api_url()}/plans"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_plan(plan_id: str) -> dict[str, Any]:
    url = f"{api_url()}/plans/{plan_id}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_evidence(evidence_id: str) -> dict[str, Any]:
    url = f"{api_url()}/evidence/{evidence_id}"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


_EVIDENCE_CACHE: dict[str, dict[str, Any]] = {}


def get_evidence_cached(evidence_id: str) -> dict[str, Any]:
    """Fetch evidence by ID with in-memory caching."""
    if evidence_id not in _EVIDENCE_CACHE:
        try:
            _EVIDENCE_CACHE[evidence_id] = get_evidence(evidence_id)
        except Exception:
            _EVIDENCE_CACHE[evidence_id] = {}
    return _EVIDENCE_CACHE[evidence_id]


def post_dispatch(plan_id: str, by: str = "operator") -> dict[str, Any]:
    """Approve dispatch and initiate active monitoring."""
    url = f"{api_url()}/plans/{plan_id}/dispatch"
    resp = requests.post(url, json={"by": by}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_progress(
    plan_id: str,
    delay_min: float = 0.0,
    waypoint_idx: Optional[int] = None,
    source: str = "checkin_button",
) -> dict[str, Any]:
    """Report check-in delay or waypoint progress."""
    url = f"{api_url()}/plans/{plan_id}/progress"
    payload = {
        "reported_delay_min": delay_min,
        "last_waypoint_idx": waypoint_idx,
        "source": source,
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_watch_tick(plan_id: str) -> dict[str, Any]:
    """Trigger a monitoring tick through the LangGraph agentic workflow."""
    url = f"{api_url()}/plans/{plan_id}/watch/tick"
    resp = requests.post(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def post_approval(
    plan_id: str,
    kind: str,
    decision: str,
    by: str = "operator",
) -> dict[str, Any]:
    """Approve or reject a proposed plan revision."""
    url = f"{api_url()}/plans/{plan_id}/approve"
    payload = {"kind": kind, "decision": decision, "by": by}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_diff(plan_id: str) -> Optional[dict[str, Any]]:
    """Fetch latest plan revision diff."""
    url = f"{api_url()}/plans/{plan_id}/diff"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_watch(plan_id: str) -> dict[str, Any]:
    """Fetch watchdog status and recent events."""
    url = f"{api_url()}/plans/{plan_id}/watch"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()
