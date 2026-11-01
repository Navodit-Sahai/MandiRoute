"""
MandiGuard FastAPI Application (Stream C).

Plain threadpool endpoints (plain `def`), CORS for Streamlit, SQLite persistence,
continuous real-time monitoring and dynamic plan revision.
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
from app.agent.graph.workflow import execute_monitoring_tick
from app.config import get_settings
from app.contracts import (
    CoverageReport,
    DispatchProgress,
    DispatchRequest,
    PlanDiff,
    PlanSet,
    SourceStatus,
)
from app.data.health import source_health
from app.db.db import get_conn, get_evidence, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema
    init_db()
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
# Request & Response Models
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
    map_points: list[dict[str, Any]] = []
    route_paths: dict[str, list[list[float]]] = {}


class DispatchActionRequest(BaseModel):
    dispatched_at: Optional[str] = None
    by: str = "operator"


class ProgressUpdateRequest(BaseModel):
    reported_delay_min: float = 0.0
    last_waypoint_idx: Optional[int] = None
    source: str = "checkin_button"


class ApprovalRequest(BaseModel):
    kind: str = "dispatch"              # "dispatch" | "replan"
    decision: str = "approve"           # "approve" | "reject"
    by: str = "operator"


class WatchTickResponse(BaseModel):
    plan_id: str
    status: str
    fired_triggers: list[str] = []
    persisted_triggers: list[str] = []
    decision_reason: str
    propose_replan: bool
    version: int
    diff: Optional[PlanDiff] = None
    diff_summary: Optional[str] = None


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
        map_points=flow_result.map_points,
        route_paths=flow_result.route_paths,
    )


@app.get("/plans")
def list_plans() -> list[dict[str, Any]]:
    """List recent plans for UI selection."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT plan_id, status, created_at, updated_at
            FROM plans
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/plans/{id}")
def get_plan(id: str) -> dict[str, Any]:
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        version_rows = conn.execute(
            "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC", (id,)
        ).fetchall()

        geo_row = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'geo' ORDER BY id DESC LIMIT 1",
            (id,),
        ).fetchone()

        assemble_row = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'assemble' ORDER BY id DESC LIMIT 1",
            (id,),
        ).fetchone()

        diff_row = conn.execute(
            "SELECT diff_json FROM plan_versions WHERE plan_id = ? AND diff_json IS NOT NULL ORDER BY version DESC LIMIT 1",
            (id,),
        ).fetchone()

        watch_row = conn.execute(
            "SELECT * FROM watch_state WHERE plan_id = ?", (id,)
        ).fetchone()

    map_points: list[dict[str, Any]] = []
    route_paths: dict[str, list[list[float]]] = {}
    if geo_row and geo_row["result_summary"]:
        try:
            geo_data = json.loads(geo_row["result_summary"])
            map_points = geo_data.get("map_points", [])
            route_paths = geo_data.get("route_paths", {})
        except Exception:
            pass

    warnings: list[str] = []
    if assemble_row and assemble_row["result_summary"]:
        warnings.append(assemble_row["result_summary"])

    latest = version_rows[0] if version_rows else None
    planset = json.loads(latest["planset_json"]) if latest else None
    coverage = planset.get("coverage") if planset else None
    diff = json.loads(diff_row["diff_json"]) if diff_row and diff_row["diff_json"] else None

    watch_state_dict = None
    if watch_row:
        watch_state_dict = {
            "last_tick_ts": watch_row["last_tick_ts"],
            "trigger_counts": json.loads(watch_row["trigger_counts_json"]) if watch_row["trigger_counts_json"] else {},
            "cooldown_until": watch_row["cooldown_until"],
            "last_progress": json.loads(watch_row["last_progress_json"]) if watch_row["last_progress_json"] else None,
        }

    return {
        "plan_id": plan_row["plan_id"],
        "request": json.loads(plan_row["request_json"]),
        "status": plan_row["status"],
        "created_at": plan_row["created_at"],
        "updated_at": plan_row["updated_at"],
        "latest_version": latest["version"] if latest else None,
        "planset": planset,
        "coverage": coverage,
        "diff": diff,
        "watch_state": watch_state_dict,
        "map_points": map_points,
        "route_paths": route_paths,
        "warnings": warnings,
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


# -----------------------------------------------------------------------------
# Dispatch, Progress & Monitoring Endpoints
# -----------------------------------------------------------------------------

@app.post("/plans/{id}/dispatch")
def dispatch_plan(id: str, action: DispatchActionRequest) -> dict[str, Any]:
    """Human approval to dispatch the truck. Initiates monitoring lifecycle."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    dispatched_at = action.dispatched_at or now_iso

    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        # 1. Update status to monitoring
        conn.execute(
            "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
            (now_iso, id),
        )

        # 2. Record approval audit entry
        conn.execute(
            """
            INSERT INTO approvals(plan_id, version, kind, decision, by, ts)
            VALUES (?, 1, 'dispatch', 'approve', ?, ?)
            """,
            (id, action.by, now_iso),
        )

        # 3. Initialize watch_state & progress
        prog = DispatchProgress(
            plan_id=id,
            dispatched_at=dispatched_at,
            reported_delay_min=0.0,
            source="checkin_button",
        )
        conn.execute(
            """
            INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
            VALUES (?, ?, '{}', NULL, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                last_tick_ts = excluded.last_tick_ts,
                last_progress_json = excluded.last_progress_json
            """,
            (id, now_iso, prog.model_dump_json()),
        )

        # 4. Record event
        conn.execute(
            """
            INSERT INTO watch_events(plan_id, ts, kind, detail_json)
            VALUES (?, ?, 'dispatched', ?)
            """,
            (id, now_iso, json.dumps({"dispatched_at": dispatched_at, "by": action.by})),
        )

    return {
        "plan_id": id,
        "status": "monitoring",
        "dispatched_at": dispatched_at,
    }


@app.post("/plans/{id}/progress")
def report_progress(id: str, body: ProgressUpdateRequest) -> dict[str, Any]:
    """Report check-in or progress update from driver/operator."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_conn() as conn:
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (id,)).fetchone()
        dispatched_at = now_iso
        if w_row and w_row["last_progress_json"]:
            old_p = json.loads(w_row["last_progress_json"])
            dispatched_at = old_p.get("dispatched_at", now_iso)

        prog = DispatchProgress(
            plan_id=id,
            dispatched_at=dispatched_at,
            last_waypoint_idx=body.last_waypoint_idx,
            reported_delay_min=body.reported_delay_min,
            source=body.source,
        )

        conn.execute(
            """
            INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
            VALUES (?, ?, '{}', NULL, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                last_progress_json = excluded.last_progress_json
            """,
            (id, now_iso, prog.model_dump_json()),
        )

        conn.execute(
            """
            INSERT INTO watch_events(plan_id, ts, kind, detail_json)
            VALUES (?, ?, 'progress_reported', ?)
            """,
            (id, now_iso, prog.model_dump_json()),
        )

    return {
        "plan_id": id,
        "progress": prog.model_dump(),
    }


@app.post("/plans/{id}/watch/tick", response_model=WatchTickResponse)
def trigger_watch_tick(id: str) -> WatchTickResponse:
    """
    Run one monitoring tick through the LangGraph agentic workflow.
    Checks price and weather observations, evaluates triggers, and revises plan if warranted.
    """
    now = datetime.now(timezone.utc)

    # 1. Fetch current progress if stored
    with get_conn() as conn:
        p_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not p_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (id,)).fetchone()

    progress = None
    if w_row and w_row["last_progress_json"]:
        progress = DispatchProgress.model_validate_json(w_row["last_progress_json"])

    # 2. Invoke LangGraph monitoring workflow
    state = execute_monitoring_tick(plan_id=id, progress=progress, now=now)

    decision = state.get("trigger_decision")
    diff = state.get("diff")
    diff_summary = state.get("diff_summary")

    return WatchTickResponse(
        plan_id=id,
        status=state.get("status", "monitoring"),
        fired_triggers=decision.fired if decision else [],
        persisted_triggers=decision.persisted if decision else [],
        decision_reason=decision.reason if decision else "No change",
        propose_replan=decision.propose_replan if decision else False,
        version=state.get("active_version", 1),
        diff=diff,
        diff_summary=diff_summary,
    )


@app.post("/plans/{id}/approve")
def approve_plan_action(id: str, req: ApprovalRequest) -> dict[str, Any]:
    """
    Handle operator approval/rejection for dispatch or proposed dynamic replanning.
    Adheres strictly to NN-11 (human in the loop).
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_conn() as conn:
        p_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not p_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        latest_v_row = conn.execute(
            "SELECT version FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1", (id,)
        ).fetchone()
        version = latest_v_row["version"] if latest_v_row else 1

        # Record human approval
        conn.execute(
            """
            INSERT INTO approvals(plan_id, version, kind, decision, by, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, version, req.kind, req.decision, req.by, now_iso),
        )

        if req.kind == "replan":
            if req.decision == "approve":
                # Approved replan: plan continues monitoring under new version
                conn.execute(
                    "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                conn.execute(
                    """
                    INSERT INTO watch_events(plan_id, ts, kind, detail_json)
                    VALUES (?, ?, 'replan_approved', ?)
                    """,
                    (id, now_iso, json.dumps({"version": version, "by": req.by})),
                )
            else:
                # Rejected replan: reverts status to monitoring previous plan
                conn.execute(
                    "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                conn.execute(
                    """
                    INSERT INTO watch_events(plan_id, ts, kind, detail_json)
                    VALUES (?, ?, 'replan_rejected', ?)
                    """,
                    (id, now_iso, json.dumps({"version": version, "by": req.by})),
                )

        elif req.kind == "dispatch":
            new_status = "monitoring" if req.decision == "approve" else "rejected"
            conn.execute(
                "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                (new_status, now_iso, id),
            )

    return {
        "plan_id": id,
        "kind": req.kind,
        "decision": req.decision,
        "status": "monitoring",
    }


@app.get("/plans/{id}/diff")
def get_plan_diff(id: str) -> Optional[dict[str, Any]]:
    """Get the latest proposed plan revision diff."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT diff_json FROM plan_versions WHERE plan_id = ? AND diff_json IS NOT NULL ORDER BY version DESC LIMIT 1",
            (id,),
        ).fetchone()
    if not row or not row["diff_json"]:
        return None
    return json.loads(row["diff_json"])


@app.get("/plans/{id}/watch")
def get_plan_watch(id: str) -> dict[str, Any]:
    """Get watchdog state, trigger counts, and recent monitoring events."""
    with get_conn() as conn:
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (id,)).fetchone()
        events = conn.execute(
            "SELECT * FROM watch_events WHERE plan_id = ? ORDER BY id DESC LIMIT 20", (id,)
        ).fetchall()

    if not w_row:
        return {"watch_state": None, "events": []}

    return {
        "watch_state": {
            "last_tick_ts": w_row["last_tick_ts"],
            "trigger_counts": json.loads(w_row["trigger_counts_json"]) if w_row["trigger_counts_json"] else {},
            "cooldown_until": w_row["cooldown_until"],
            "last_progress": json.loads(w_row["last_progress_json"]) if w_row["last_progress_json"] else None,
        },
        "events": [
            {
                "id": e["id"],
                "ts": e["ts"],
                "kind": e["kind"],
                "detail": json.loads(e["detail_json"]) if e["detail_json"] else {},
            }
            for e in events
        ],
    }
