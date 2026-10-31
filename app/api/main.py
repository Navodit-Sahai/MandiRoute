"""
MandiGuard FastAPI Application (Stream C).

Plain threadpool endpoints (plain `def`), CORS for Streamlit, SQLite persistence.
Endpoints:
- GET /health
- GET /sources/health
- GET /assumptions
- POST /plan
- GET /plans/{id}
- GET /plans/{id}/trace
- GET /evidence/{id}
- POST /plans/{id}/approve
- POST /plans/{id}/progress
- POST /plans/{id}/watch/tick
- GET /plans/{id}/diff
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import secrets
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import guards
from app.agent.flow import FlowResult, run_plan_flow
from app.agent.watchdog import start_watchdog_thread, stop_watchdog_thread, tick_plan
from app.config import get_settings
from app.contracts import CoverageReport, DispatchProgress, DispatchRequest, Evidence, PlanDiff, PlanSet, SourceStatus
from app.data.health import source_health
from app.db.db import get_conn, get_evidence, init_db
from app.explain import render_diff
from app.integrations.notify import notify

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite database schema
    init_db()
    # Start watchdog thread if MG_WATCHDOG=1
    start_watchdog_thread()
    yield
    # Cleanup background watchdog
    stop_watchdog_thread()


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
    summary: Optional[str] = None
    map_points: list[dict[str, Any]] = []
    route_paths: dict[str, list[list[float]]] = {}


class ApproveRequest(BaseModel):
    version: int
    decision: Literal["approve", "reject"]
    kind: Literal["dispatch", "replan"]


class ProgressInput(BaseModel):
    reported_delay_min: Optional[float] = None
    delay: Optional[str] = None          # e.g. "delay:30" or "delay:60"
    last_waypoint_idx: Optional[int] = None
    passed: Optional[str] = None         # e.g. "passed:2"
    arrived: Optional[bool] = False
    manual_eta: Optional[str] = None
    source: Optional[Literal["telegram_location", "checkin_button", "manual_eta", "route_estimate"]] = "manual_eta"


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
        summary=flow_result.summary,
        map_points=flow_result.map_points,
        route_paths=flow_result.route_paths,
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

        geo_row = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'geo' ORDER BY id DESC LIMIT 1",
            (id,),
        ).fetchone()

        assemble_row = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'assemble' ORDER BY id DESC LIMIT 1",
            (id,),
        ).fetchone()

        explain_row = conn.execute(
            "SELECT args_json, result_summary FROM agent_trace WHERE plan_id = ? AND node = 'explain' ORDER BY id DESC LIMIT 1",
            (id,),
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

    summary: Optional[str] = None
    if explain_row and explain_row["args_json"]:
        try:
            ex_data = json.loads(explain_row["args_json"])
            summary = ex_data.get("summary")
        except Exception:
            pass

    latest = version_rows[0] if version_rows else None
    planset = json.loads(latest["planset_json"]) if latest else None
    coverage = planset.get("coverage") if planset else None

    return {
        "plan_id": plan_row["plan_id"],
        "request": json.loads(plan_row["request_json"]),
        "status": plan_row["status"],
        "created_at": plan_row["created_at"],
        "updated_at": plan_row["updated_at"],
        "latest_version": latest["version"] if latest else None,
        "planset": planset,
        "coverage": coverage,
        "summary": summary,
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


@app.post("/plans/{id}/approve")
def approve_plan(id: str, body: ApproveRequest) -> dict[str, Any]:
    """
    Handle plan approval or rejection (dispatch or replan).
    Enforces valid state transitions, writes to approvals table,
    updates status, and initializes watch_state.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        current_status = plan_row["status"]

        # Validate state transition
        if body.kind == "dispatch":
            if current_status != "awaiting_approval":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot approve dispatch for plan in status '{current_status}' (must be 'awaiting_approval')",
                )
            if body.decision == "approve":
                # dispatch+approve -> status "dispatched" then "monitoring"
                conn.execute(
                    "UPDATE plans SET status = 'dispatched', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                conn.execute(
                    "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                new_status = "monitoring"

                # Store initial DispatchProgress in watch_state
                prog = DispatchProgress(
                    plan_id=id,
                    dispatched_at=now_iso,
                    source="route_estimate",
                )
                conn.execute(
                    """
                    INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        last_progress_json = excluded.last_progress_json
                    """,
                    (id, now_iso, "{}", None, prog.model_dump_json()),
                )
            else:
                conn.execute(
                    "UPDATE plans SET status = 'rejected', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                new_status = "rejected"

        elif body.kind == "replan":
            if current_status != "replan_pending":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot approve replan for plan in status '{current_status}' (must be 'replan_pending')",
                )
            if body.decision == "approve":
                # replan+approve -> that version becomes active (status "monitoring")
                conn.execute(
                    "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                new_status = "monitoring"
            else:
                # reject keeps current version, returns to monitoring
                conn.execute(
                    "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                    (now_iso, id),
                )
                new_status = "monitoring"

        # Record approval
        conn.execute(
            """
            INSERT INTO approvals(plan_id, version, kind, decision, by, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, body.version, body.kind, body.decision, "operator", now_iso),
        )

    # Trigger notification
    notify(
        plan_id=id,
        kind=f"{body.kind}_{body.decision}",
        text=f"Plan {id} v{body.version} {body.kind} was {body.decision}d",
    )

    return {
        "ok": True,
        "plan_id": id,
        "status": new_status,
        "version": body.version,
        "decision": body.decision,
    }


@app.post("/plans/{id}/progress")
def update_progress(id: str, body: ProgressInput) -> dict[str, Any]:
    """
    Update driver transit progress:
    e.g. delay:30, delay:60, passed:2, arrived, or manual ETA.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        ws_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (id,)).fetchone()

    # Reconstruct or create DispatchProgress
    if ws_row and ws_row["last_progress_json"]:
        try:
            prog = DispatchProgress.model_validate(json.loads(ws_row["last_progress_json"]))
        except Exception:
            prog = DispatchProgress(plan_id=id, dispatched_at=now_iso)
    else:
        prog = DispatchProgress(plan_id=id, dispatched_at=now_iso)

    # Parse inputs
    if body.delay:
        # e.g. "delay:30" or "delay:60"
        parts = body.delay.split(":")
        if len(parts) == 2:
            try:
                prog.reported_delay_min = float(parts[1])
            except ValueError:
                pass
    elif body.reported_delay_min is not None:
        prog.reported_delay_min = float(body.reported_delay_min)

    if body.passed:
        # e.g. "passed:2"
        parts = body.passed.split(":")
        if len(parts) == 2:
            try:
                prog.last_waypoint_idx = int(parts[1])
            except ValueError:
                pass
    elif body.last_waypoint_idx is not None:
        prog.last_waypoint_idx = int(body.last_waypoint_idx)

    if body.source:
        prog.source = body.source

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                last_progress_json = excluded.last_progress_json
            """,
            (id, now_iso, "{}", None, prog.model_dump_json()),
        )

    return {"ok": True, "plan_id": id, "progress": prog.model_dump()}


@app.post("/plans/{id}/watch/tick")
def watch_tick(id: str, fresh: int = Query(0)) -> dict[str, Any]:
    """
    Perform a monitoring tick on the plan:
    refreshes data, checks triggers, recomputes replans, and writes events.
    Query param ?fresh=1 forces fresh API fetches.
    """
    return tick_plan(id, force_fresh=bool(fresh))


@app.get("/plans/{id}/diff")
def get_plan_diff(
    id: str,
    from_version: Optional[int] = Query(None, alias="from"),
    to_version: Optional[int] = Query(None, alias="to"),
) -> dict[str, Any]:
    """
    Return PlanDiff and rendered diff text.
    """
    with get_conn() as conn:
        plan_row = conn.execute("SELECT plan_id FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        if to_version is not None:
            v_row = conn.execute(
                "SELECT * FROM plan_versions WHERE plan_id = ? AND version = ?",
                (id, to_version),
            ).fetchone()
        else:
            v_row = conn.execute(
                "SELECT * FROM plan_versions WHERE plan_id = ? AND diff_json IS NOT NULL ORDER BY version DESC LIMIT 1",
                (id,),
            ).fetchone()

    if not v_row or not v_row["diff_json"]:
        raise HTTPException(status_code=404, detail=f"No diff found for plan '{id}'")

    diff_dict = json.loads(v_row["diff_json"])
    diff = PlanDiff.model_validate(diff_dict)

    # Build evidence index for diff rendering
    planset_dict = json.loads(v_row["planset_json"])
    planset = PlanSet.model_validate(planset_dict)
    all_eids = set(planset.evidence_ids)
    for p in planset.plans:
        all_eids.update(p.option.evidence_ids)

    ev_index: dict[str, Evidence] = {}
    for eid in all_eids:
        row = get_evidence(eid)
        if row:
            try:
                ev_index[eid] = Evidence.model_validate(row)
            except Exception:
                pass

    try:
        diff_text = render_diff(diff, ev_index)
    except Exception as e:
        logger.warning("render_diff failed in diff endpoint: %s", e)
        diff_text = f"Plan diff v{diff.old_version} -> v{diff.new_version}: {diff.primary_reason}"

    return {
        "diff": diff.model_dump(),
        "diff_text": diff_text,
    }
