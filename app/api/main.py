"""
MandiGuard FastAPI Application (Stream C).

Plain threadpool endpoints (plain `def`), CORS for Streamlit, SQLite persistence.
"""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import guards
from app.agent.flow import FlowResult, run_plan_flow
from app.config import get_settings
from app.contracts import CoverageReport, DispatchRequest, PlanSet, SourceStatus
from app.data.health import source_health
from app.db.db import get_conn, get_evidence, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema
    init_db()
    # Watchdog and Telegram poller threads are NOT started yet
    yield


app = FastAPI(title="MandiGuard API", version="1.0", lifespan=lifespan)

# CORS for Streamlit UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Response Models
# -----------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    mode: str
    time: str
    banner: Optional[str] = None


class PlanResponse(BaseModel):
    plan_id: str
    status: str                         # "awaiting_approval" | "abstained"
    planset: PlanSet
    coverage: CoverageReport
    warnings: list[str] = []
    source_status: list[SourceStatus] = []
    trace_id: str


# -----------------------------------------------------------------------------
# Endpoints (plain def for threadpool execution)
# -----------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    now_utc = datetime.now(timezone.utc).isoformat()
    return HealthResponse(
        status="ok",
        mode=guards.mode(),
        time=now_utc,
        banner=guards.banner(),
    )


@app.get("/sources/health")
def get_sources_health() -> list[SourceStatus]:
    return source_health(probe=True)


@app.get("/assumptions")
def get_assumptions() -> dict[str, Any]:
    # Numeric and policy assumptions registry (contains no secrets)
    return get_settings().model_dump()


@app.post("/plan", response_model=PlanResponse)
def create_plan(req: DispatchRequest) -> PlanResponse:
    now = datetime.now(timezone.utc)
    # Short random ID (8 chars, no spaces)
    plan_id = secrets.token_hex(4)

    flow_result: FlowResult = run_plan_flow(req=req, plan_id=plan_id, now=now)

    # Persist in plans and plan_versions tables
    now_iso = now.isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO plans(plan_id, request_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (plan_id, req.model_dump_json(), flow_result.status, now_iso, now_iso),
        )
        conn.execute(
            """
            INSERT INTO plan_versions(plan_id, version, planset_json, diff_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (plan_id, 1, flow_result.planset.model_dump_json(), None, now_iso),
        )

    return PlanResponse(
        plan_id=plan_id,
        status=flow_result.status,
        planset=flow_result.planset,
        coverage=flow_result.planset.coverage,
        warnings=flow_result.assembly_warnings,
        source_status=flow_result.source_status,
        trace_id=flow_result.trace_id,
    )


@app.get("/plans/{id}")
def get_plan(id: str) -> dict[str, Any]:
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        version_rows = conn.execute(
            "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC", (id,)
        ).fetchall()

    latest = version_rows[0] if version_rows else None
    return {
        "plan_id": plan_row["plan_id"],
        "request": json.loads(plan_row["request_json"]),
        "status": plan_row["status"],
        "created_at": plan_row["created_at"],
        "updated_at": plan_row["updated_at"],
        "latest_version": latest["version"] if latest else None,
        "planset": json.loads(latest["planset_json"]) if latest else None,
        "versions": [dict(v) for v in version_rows],
    }


@app.get("/plans/{id}/trace")
def get_plan_trace(id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        plan_row = conn.execute("SELECT plan_id FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        trace_rows = conn.execute(
            "SELECT * FROM agent_trace WHERE plan_id = ? ORDER BY step ASC", (id,)
        ).fetchall()

    return [
        {
            "id": r["id"],
            "plan_id": r["plan_id"],
            "step": r["step"],
            "node": r["node"],
            "tool": r["tool"],
            "args_json": r["args_json"],
            "result_summary": r["result_summary"],
            "ok": bool(r["ok"]),
            "ts": r["ts"],
        }
        for r in trace_rows
    ]


@app.get("/evidence/{id}")
def get_evidence_by_id(id: str) -> dict[str, Any]:
    row = get_evidence(id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Evidence '{id}' not found")
    return row

