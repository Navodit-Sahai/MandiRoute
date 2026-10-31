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
