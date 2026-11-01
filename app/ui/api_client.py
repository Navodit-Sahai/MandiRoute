"""
HTTP API client for Streamlit UI (Stream C).

Calls the MandiGuard FastAPI server exclusively; imports no engine/data code.
Base URL configurable via MG_API_URL (default: http://localhost:8000).
"""
from __future__ import annotations

import os
from typing import Any

import requests


def api_url() -> str:
    url = os.environ.get("MG_API_URL", "http://localhost:8000").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        local = url.startswith(("localhost", "127.0.0.1"))
        url = f"{'http' if local else 'https'}://{url}"
    return url


def list_plans() -> list[dict[str, Any]]:
    """List recent plans from the database for the monitoring dashboard."""
    url = f"{api_url()}/plans"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_health() -> dict[str, Any]:
    url = f"{api_url()}/health"
    resp = requests.get(url, timeout=30)
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


def get_plan(plan_id: str) -> dict[str, Any]:
    url = f"{api_url()}/plans/{plan_id}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_plan_trace(plan_id: str) -> list[dict[str, Any]]:
    url = f"{api_url()}/plans/{plan_id}/trace"
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
    """In-memory cached lookup for evidence records to minimize HTTP overhead on multi-plan screens."""
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
    payload_or_delay: dict[str, Any] | float = 0.0,
    waypoint_idx: int | None = None,
    source: str = "checkin_button",
) -> dict[str, Any]:
    """Report check-in delay or waypoint progress (accepts dictionary or kwargs)."""
    url = f"{api_url()}/plans/{plan_id}/progress"
    if isinstance(payload_or_delay, dict):
        body = payload_or_delay
    else:
        body = {
            "reported_delay_min": float(payload_or_delay),
            "last_waypoint_idx": waypoint_idx,
            "source": source,
        }
    resp = requests.post(url, json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_watch_tick(plan_id: str, *, fresh: int = 0) -> dict[str, Any]:
    """Trigger a monitoring tick through the monitoring workflow."""
    url = f"{api_url()}/plans/{plan_id}/watch/tick?fresh={fresh}"
    resp = requests.post(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def post_approval(
    plan_id: str,
    kind: str,
    decision: str,
    *,
    version: int | None = None,
    by: str = "operator",
) -> dict[str, Any]:
    """Approve or reject a proposed plan revision or dispatch."""
    url = f"{api_url()}/plans/{plan_id}/approve"
    payload: dict[str, Any] = {"kind": kind, "decision": decision, "by": by}
    if version is not None:
        payload["version"] = version
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# Alias for compatibility with callers expecting post_approve
post_approve = post_approval


def get_diff(plan_id: str, *, from_ver: int | None = None, to_ver: int | None = None) -> dict[str, Any] | None:
    """Fetch plan revision diff."""
    params: dict[str, Any] = {}
    if from_ver is not None:
        params["from"] = from_ver
    if to_ver is not None:
        params["to"] = to_ver
    url = f"{api_url()}/plans/{plan_id}/diff"
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_watch(plan_id: str) -> dict[str, Any]:
    """Fetch watchdog status and recent events."""
    url = f"{api_url()}/plans/{plan_id}/watch"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_events(plan_id: str) -> list[dict[str, Any]]:
    """Fetch watchdog event timeline."""
    url = f"{api_url()}/plans/{plan_id}/events"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_whatif(plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute what-if simulation on stored candidates."""
    url = f"{api_url()}/plans/{plan_id}/whatif"
    resp = requests.post(url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()
