"""
MandiGuard FastAPI Application (Stream C).

Plain threadpool endpoints (plain `def`), CORS for Streamlit, SQLite persistence,
continuous real-time monitoring and dynamic plan revision.
Endpoints:
- GET /health
- GET /sources/health
- GET /assumptions
- POST /plan
- GET /plans
- GET /plans/{id}
- GET /plans/{id}/trace
- GET /evidence/{id}
- POST /plans/{id}/dispatch
- POST /plans/{id}/approve
- POST /plans/{id}/progress
- POST /plans/{id}/watch/tick
- GET /plans/{id}/diff
- GET /plans/{id}/watch
- GET /plans/{id}/events
- POST /plans/{id}/whatif
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import guards
from app.agent.flow import FlowResult, run_plan_flow
from app.agent.graph.workflow import execute_monitoring_tick
from app.agent.watchdog import (
    get_plan_lock,
    start_watchdog_thread,
    stop_watchdog_thread,
)
from app.config import get_settings
from app.contracts import (
    CandidateInputs,
    CoverageReport,
    DiffItem,
    DispatchProgress,
    DispatchRequest,
    Evidence,
    PlanDiff,
    PlanSet,
    SourceStatus,
)
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
    # Start Telegram polling thread if MG_TELEGRAM=1
    from app.integrations.telegram import start_telegram_polling, stop_telegram_polling
    start_telegram_polling()
    yield
    # Cleanup background threads
    stop_telegram_polling()
    stop_watchdog_thread()


app = FastAPI(title="MandiGuard API", version="1.0", lifespan=lifespan)

# CORS for Streamlit UI
raw_cors = os.environ.get("MG_CORS_ORIGINS", "http://localhost:8501")
cors_origins = [orig.strip() for orig in raw_cors.split(",") if orig.strip()]
if not cors_origins:
    cors_origins = ["http://localhost:8501"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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
    banner: str | None = None


class PlanResponse(BaseModel):
    plan_id: str
    status: str                         # "awaiting_approval" | "abstained"
    planset: PlanSet
    coverage: CoverageReport
    warnings: list[str] = []
    source_status: list[SourceStatus] = []
    trace_id: str
    summary: str | None = None
    why_not_text: str | None = None
    map_points: list[dict[str, Any]] = []
    route_paths: dict[str, list[list[float]]] = {}


class DispatchActionRequest(BaseModel):
    dispatched_at: str | None = None
    by: str = "operator"


class ApprovalRequest(BaseModel):
    version: int | None = 1
    decision: str = "approve"           # "approve" | "reject"
    kind: str = "dispatch"              # "dispatch" | "replan"
    by: str = "operator"


ApproveRequest = ApprovalRequest


class ProgressInput(BaseModel):
    reported_delay_min: float | None = 0.0
    delay: str | None = None          # e.g. "delay:30" or "delay:60"
    last_waypoint_idx: int | None = None
    passed: str | None = None         # e.g. "passed:2"
    arrived: bool | None = False
    manual_eta: str | None = None
    source: str | None = "manual_eta"


ProgressUpdateRequest = ProgressInput


class WatchTickResponse(BaseModel):
    plan_id: str
    status: str
    fired_triggers: list[str] = []
    persisted_triggers: list[str] = []
    decision_reason: str
    propose_replan: bool
    version: int
    diff: PlanDiff | None = None
    diff_summary: str | None = None


class WhatIfRequest(BaseModel):
    delay_min: float | None = None
    rain_mm_segment: float | None = None
    price_delta_pct: float | None = None
    closing_time: dict[str, str] | None = None


# -----------------------------------------------------------------------------
# Endpoints (plain def for threadpool execution)
# -----------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    now_utc = datetime.now(UTC).isoformat()
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
    now = datetime.now(UTC)
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
        why_not_text=flow_result.why_not_text,
        map_points=flow_result.map_points,
        route_paths=flow_result.route_paths,
    )


@app.get("/plans")
def list_plans() -> list[dict[str, Any]]:
    """List recent plans with active status, origin, crop, and plan count."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.plan_id, p.status, p.request_json, p.created_at, p.updated_at,
                   MAX(pv.version) as latest_version
            FROM plans p
            LEFT JOIN plan_versions pv ON p.plan_id = pv.plan_id
            GROUP BY p.plan_id
            ORDER BY p.created_at DESC
            LIMIT 50
            """
        ).fetchall()

    plans_list = []
    for r in rows:
        req = {}
        try:
            req = json.loads(r["request_json"])
        except Exception:
            pass

        plans_list.append({
            "plan_id": r["plan_id"],
            "status": r["status"],
            "crop": req.get("crop", "Tomato"),
            "origin_label": req.get("origin_label", "Unknown"),
            "quantity_qtl": req.get("quantity_qtl", 0.0),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "latest_version": r["latest_version"] or 1,
        })
    return plans_list


@app.get("/plans/{id}")
def get_plan(id: str, version: int | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        # Determine target version (default: latest version)
        if version is not None:
            v_row = conn.execute(
                "SELECT * FROM plan_versions WHERE plan_id = ? AND version = ?", (id, version)
            ).fetchone()
            if not v_row:
                raise HTTPException(status_code=404, detail=f"Version {version} not found for plan '{id}'")
        else:
            v_row = conn.execute(
                "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1", (id,)
            ).fetchone()

        latest = conn.execute(
            "SELECT version, diff_json FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1", (id,)
        ).fetchone()

        # Fetch warnings from agent_trace if present
        trace_rows = conn.execute(
            "SELECT node, args_json, result_summary FROM agent_trace WHERE plan_id = ?",
            (id,),
        ).fetchall()
        warnings = []
        summary = None
        why_not_text = None
        map_points = []
        route_paths = {}
        for tr in trace_rows:
            node = tr["node"]
            args = {}
            if tr["args_json"]:
                try:
                    args = json.loads(tr["args_json"])
                except Exception:
                    pass
            res = {}
            if tr["result_summary"]:
                try:
                    res = json.loads(tr["result_summary"])
                except Exception:
                    pass
            if node == "done":
                warnings = args.get("warnings", [])
            elif node == "explain":
                summary = args.get("summary")
                why_not_text = args.get("why_not_text")
            elif node == "geo":
                map_points = res.get("map_points", [])
                route_paths = res.get("route_paths", {})

        # Fetch watch_state
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (id,)).fetchone()
        watch_state_dict = None
        if w_row:
            watch_state_dict = {
                "last_tick_ts": w_row["last_tick_ts"],
                "trigger_counts": json.loads(w_row["trigger_counts_json"]) if w_row["trigger_counts_json"] else {},
                "cooldown_until": w_row["cooldown_until"],
                "last_progress": json.loads(w_row["last_progress_json"]) if w_row["last_progress_json"] else None,
            }

    planset = json.loads(v_row["planset_json"]) if v_row else None
    coverage = planset.get("coverage") if planset else None
    active_version = v_row["version"] if v_row else 1
    diff = None
    if latest and latest["diff_json"]:
        diff = json.loads(latest["diff_json"])

    return {
        "plan_id": id,
        "status": plan_row["status"],
        "request": json.loads(plan_row["request_json"]),
        "created_at": plan_row["created_at"],
        "updated_at": plan_row["updated_at"],
        "version": active_version,
        "latest_version": latest["version"] if latest else None,
        "planset": planset,
        "coverage": coverage,
        "diff": diff,
        "watch_state": watch_state_dict,
        "summary": summary,
        "why_not_text": why_not_text,
        "map_points": map_points,
        "route_paths": route_paths,
        "warnings": warnings,
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
    if not row:
        raise HTTPException(status_code=404, detail=f"Evidence '{id}' not found")
    return row


@app.post("/plans/{id}/dispatch")
def dispatch_plan(id: str, action: DispatchActionRequest | None = None) -> dict[str, Any]:
    """Transition a plan from 'awaiting_approval' to 'monitoring' / dispatched."""
    if action is None:
        action = DispatchActionRequest()
    return apply_plan_approval(id, version=1, decision="approve", kind="dispatch", by=action.by)


def apply_plan_approval(
    id: str,
    version: int,
    decision: Literal["approve", "reject"] | str,
    kind: Literal["dispatch", "replan"] | str,
    by: str = "operator",
) -> dict[str, Any]:
    """
    Core approval logic called by HTTP endpoints, Dispatch button, and Telegram callback.
    Enforces valid state transitions, writes to approvals table, updates status,
    initializes watch_state, and sends notifications.
    """
    now = datetime.now(UTC)
    now_iso = now.isoformat()

    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        current_status = plan_row["status"]

        # Validate state transition
        if kind == "dispatch":
            if current_status != "awaiting_approval":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot approve dispatch for plan in status '{current_status}' (must be 'awaiting_approval')",
                )
            if decision == "approve":
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

        elif kind == "replan":
            if current_status != "replan_pending":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot approve replan for plan in status '{current_status}' (must be 'replan_pending')",
                )
            if decision == "reject":
                # FIX: the proposed version was already saved as latest, so a rejected
                # replan silently became the new baseline. Drop it.
                latest_v = conn.execute(
                    "SELECT MAX(version) AS v FROM plan_versions WHERE plan_id = ?", (id,)
                ).fetchone()["v"]
                if latest_v is not None and latest_v > 1 and latest_v == version:
                    conn.execute(
                        "DELETE FROM plan_versions WHERE plan_id = ? AND version = ?",
                        (id, version),
                    )
            conn.execute(
                "UPDATE plans SET status = 'monitoring', updated_at = ? WHERE plan_id = ?",
                (now_iso, id),
            )
            new_status = "monitoring"
        else:
            new_status = current_status

        # Record approval
        conn.execute(
            """
            INSERT INTO approvals(plan_id, version, kind, decision, by, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, version, kind, decision, by, now_iso),
        )

        plan_a_mandi = "Mandi"
        pv = conn.execute(
            "SELECT planset_json FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1",
            (id,),
        ).fetchone()
        if pv and pv["planset_json"]:
            try:
                ps = json.loads(pv["planset_json"])
                plan_a = next((p for p in ps.get("plans", []) if p.get("label") == "A"), None)
                if plan_a:
                    plan_a_mandi = plan_a.get("option", {}).get("mandi_name", "Mandi")
            except Exception:
                pass

    from app.integrations.telegram import build_callback_data

    if kind == "dispatch" and decision == "approve":
        dispatch_buttons = [
            {"text": "Passed next waypoint", "callback_data": build_callback_data("checkin", id, version, "passed:1")},
            {"text": "Delayed +30 min", "callback_data": build_callback_data("checkin", id, version, "delay:30")},
            {"text": "Delayed +60 min", "callback_data": build_callback_data("checkin", id, version, "delay:60")},
            {"text": "Arrived", "callback_data": build_callback_data("checkin", id, version, "arrived")},
        ]
        notify(
            plan_id=id,
            kind="dispatch_approved",
            text=f"Dispatched to {plan_a_mandi}. Send transit check-ins:",
            buttons=dispatch_buttons,
        )
    else:
        notify(
            plan_id=id,
            kind=f"{kind}_{decision}",
            text=f"Plan {id} v{version} {kind} was " + ("approved" if decision == "approve" else "rejected"),
        )

    return {
        "ok": True,
        "plan_id": id,
        "status": new_status,
        "version": version,
        "decision": decision,
    }


def apply_plan_progress(
    id: str,
    *,
    delay: str | None = None,
    passed: str | None = None,
    arrived: bool | None = False,
    reported_delay_min: float | None = None,
    last_waypoint_idx: int | None = None,
    manual_eta: str | None = None,
    source: str | None = "manual_eta",
) -> dict[str, Any]:
    """Core transit progress update called by both HTTP endpoint and Telegram callback."""
    now = datetime.now(UTC)
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
    if delay:
        parts = delay.split(":")
        if len(parts) == 2:
            try:
                prog.reported_delay_min = float(parts[1])
            except ValueError:
                pass
    elif reported_delay_min is not None:
        prog.reported_delay_min = float(reported_delay_min)

    if passed:
        parts = passed.split(":")
        if len(parts) == 2:
            try:
                prog.last_waypoint_idx = int(parts[1])
            except ValueError:
                pass
    elif last_waypoint_idx is not None:
        prog.last_waypoint_idx = int(last_waypoint_idx)

    if source:
        prog.source = source

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
        if arrived and plan_row["status"] == "monitoring":
            conn.execute(
                "UPDATE plans SET status = 'completed', updated_at = ? WHERE plan_id = ?",
                (now_iso, id),
            )

    return {"ok": True, "plan_id": id, "progress": prog.model_dump(), "arrived": bool(arrived)}


@app.post("/plans/{id}/approve")
def approve_plan(id: str, body: ApprovalRequest) -> dict[str, Any]:
    return apply_plan_approval(id, body.version or 1, body.decision, body.kind, by=body.by)


approve_plan_action = approve_plan


def _unused_approve_plan(id: str, body: ApprovalRequest) -> dict[str, Any]:
    """
    Handle plan approval or rejection (dispatch or replan).
    Enforces valid state transitions, writes to approvals table,
    updates status, and initializes watch_state.
    """
    version = body.version or 1
    return apply_plan_approval(id, version, body.decision, body.kind, by=body.by)


@app.post("/plans/{id}/progress")
def update_progress(id: str, body: ProgressInput) -> dict[str, Any]:
    """
    Update driver transit progress:
    e.g. delay:30, delay:60, passed:2, arrived, or manual ETA.
    """
    return apply_plan_progress(
        id=id,
        delay=body.delay,
        passed=body.passed,
        arrived=body.arrived,
        reported_delay_min=body.reported_delay_min,
        last_waypoint_idx=body.last_waypoint_idx,
        manual_eta=body.manual_eta,
        source=body.source,
    )


@app.post("/plans/{id}/watch/tick")
def tick_plan_watch(
    id: str,
    progress: DispatchProgress | None = None,
    force: bool = Query(False),
) -> dict[str, Any]:
    """
    Execute a single watchdog monitoring tick.
    Wrapped in plan lock to prevent race condition with background watchdog thread.
    """
    now = datetime.now(UTC)
    with get_plan_lock(id):
        def _format_plain_tick(res: dict[str, Any]) -> dict[str, Any]:
            with get_conn() as conn:
                p_row = conn.execute("SELECT status FROM plans WHERE plan_id = ?", (id,)).fetchone()
                cur_status = p_row["status"] if p_row else "monitoring"
                v_row = conn.execute("SELECT MAX(version) AS v FROM plan_versions WHERE plan_id = ?", (id,)).fetchone()
                cur_version = v_row["v"] if v_row and v_row["v"] else 1
            res.setdefault("ok", True)
            res.setdefault("plan_id", id)
            res.setdefault("status", cur_status)
            res.setdefault("version", cur_version)
            res.setdefault("decision_reason", res.get("decision", "no change"))
            res.setdefault("propose_replan", bool(res.get("new_version")))
            res.setdefault("fired_triggers", res.get("triggers", []))
            res.setdefault("persisted_triggers", res.get("persisted", []))
            res.setdefault("diff_summary", res.get("diff_text"))
            return res

        if os.environ.get("MG_AGENT") == "plain":
            from app.agent.watchdog import _tick_plan_unlocked
            return _format_plain_tick(_tick_plan_unlocked(id, force_fresh=force))

        try:
            state = execute_monitoring_tick(plan_id=id, progress=progress, now=now)
            decision = state.get("trigger_decision")
            diff = state.get("diff")
            diff_summary = state.get("diff_summary")
            propose = decision.propose_replan if decision else False
            new_v = state.get("active_version", 1) if propose else None

            return {
                "ok": True,
                "plan_id": id,
                "status": state.get("status", "monitoring"),
                "fired_triggers": decision.fired if decision else [],
                "persisted_triggers": decision.persisted if decision else [],
                "decision_reason": decision.reason if decision else "No change",
                "decision": decision.reason if decision else "no change",
                "propose_replan": propose,
                "version": state.get("active_version", 1),
                "new_version": new_v,
                "diff": diff.model_dump() if hasattr(diff, "model_dump") else diff,
                "diff_text": diff_summary,
                "diff_summary": diff_summary,
            }
        except Exception as e:
            logger.warning("Graph tick failed (%s), falling back to tick_plan", e)
            return _format_plain_tick(_tick_plan_unlocked(id, force_fresh=force))


@app.get("/plans/{id}/diff")
def get_plan_diff(
    id: str,
    from_version: int | None = Query(None, alias="from"),
    to_version: int | None = Query(None, alias="to"),
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
    except Exception:
        diff_text = f"Version {diff.new_version} proposed: {diff.primary_reason}"

    res = diff.model_dump()
    res["diff"] = diff.model_dump()
    res["diff_text"] = diff_text
    return res


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


@app.get("/plans/{id}/events")
def get_plan_events(id: str, limit: int = 50) -> list[dict[str, Any]]:
    """List recent watchdog and lifecycle events for plan."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watch_events WHERE plan_id = ? ORDER BY id ASC LIMIT ?",
            (id, limit),
        ).fetchall()

    return [
        {
            "id": r["id"],
            "plan_id": r["plan_id"],
            "ts": r["ts"],
            "kind": r["kind"],
            "detail": json.loads(r["detail_json"]) if r["detail_json"] else {},
        }
        for r in rows
    ]


@app.post("/plans/{id}/whatif")
def plan_whatif(id: str, body: WhatIfRequest) -> dict[str, Any]:
    """
    Run what-if scenario recomputation on an in-memory clone of a plan.
    Never persists to plan_versions or plans table.
    """
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' not found")

        v_row = conn.execute(
            "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1",
            (id,),
        ).fetchone()
        if not v_row:
            raise HTTPException(status_code=404, detail=f"Plan '{id}' has no versions")

        cand_rows = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'candidates'",
            (id,),
        ).fetchall()

    req_orig = DispatchRequest.model_validate(json.loads(plan_row["request_json"]))
    orig_planset = PlanSet.model_validate(json.loads(v_row["planset_json"]))
    plan_a = next((p for p in orig_planset.plans if p.label == "A"), None)
    plan_a_mandi_id = plan_a.option.mandi_id if plan_a else None
    plan_a_route_id = getattr(plan_a.option, "route_id", None) if plan_a else None

    cands_raw = []
    ev_ids = []
    for cr in cand_rows:
        if cr["result_summary"]:
            try:
                res_data = json.loads(cr["result_summary"])
                cands_raw.extend(res_data.get("candidates", []))
                ev_ids.extend(res_data.get("evidence_ids", []))
            except Exception:
                pass

    cands_orig: list[CandidateInputs] = [CandidateInputs.model_validate(c) for c in cands_raw]

    # Fetch evidence records
    evidence_list: list[Evidence] = []
    for eid in ev_ids:
        row = get_evidence(eid)
        if row:
            try:
                evidence_list.append(Evidence.model_validate(row))
            except Exception:
                pass

    # Work strictly on COPIES
    req_copy = req_orig.model_copy(deep=True)
    cands_copy = [c.model_copy(deep=True) for c in cands_orig]
    ev_copy = [e.model_copy(deep=True) for e in evidence_list]

    # 1. Closing time overrides
    if body.closing_time:
        req_copy.mandi_latest_arrival.update(body.closing_time)
        for cand in cands_copy:
            if cand.mandi_id in body.closing_time:
                cand.latest_arrival_local = body.closing_time[cand.mandi_id]

    # 2. Price change % on Plan A's mandi
    if body.price_delta_pct is not None and plan_a_mandi_id:
        factor = max(0.0, 1.0 + (body.price_delta_pct / 100.0))
        for cand in cands_copy:
            if cand.mandi_id == plan_a_mandi_id and cand.price is not None:
                cand.price.low = round(cand.price.low * factor, 2)
                cand.price.typical = round(cand.price.typical * factor, 2)
                cand.price.high = round(cand.price.high * factor, 2)

    # 3. Rain mm on Plan A's route
    if body.rain_mm_segment is not None and plan_a_mandi_id:
        for cand in cands_copy:
            if cand.mandi_id == plan_a_mandi_id:
                for r in cand.routes:
                    if r.route_id == plan_a_route_id:
                        for ws in r.weather:
                            if ws.precip_mm is not None:
                                ws.precip_mm += body.rain_mm_segment
                            else:
                                ws.precip_mm = body.rain_mm_segment

    # 4. Delay minutes on Plan A's route
    if body.delay_min is not None and plan_a_mandi_id:
        for cand in cands_copy:
            if cand.mandi_id == plan_a_mandi_id:
                for r in cand.routes:
                    if r.route_id == plan_a_route_id:
                        r.duration_h_nominal += body.delay_min / 60.0

    # Recompute with build_plan_set
    from app.engine.plans import build_plan_set

    now = datetime.now(UTC)
    cfg = get_settings()

    new_planset = build_plan_set(
        req=req_copy,
        cands=cands_copy,
        evidence=ev_copy,
        now=now,
        cfg=cfg,
        plan_id=id,
        version=orig_planset.version + 1,
    )

    # Build PlanDiff & render diff text
    diff_items = []
    old_a_opt = plan_a.option if plan_a else None
    new_a_plan = next((p for p in new_planset.plans if p.label == "A"), None)
    new_a_opt = new_a_plan.option if new_a_plan else None

    if old_a_opt and new_a_opt:
        if old_a_opt.mandi_name != new_a_opt.mandi_name:
            diff_items.append(
                DiffItem(
                    term="destination",
                    old=old_a_opt.mandi_name,
                    new=new_a_opt.mandi_name,
                    delta=None,
                    effect=f"Plan A shifted from {old_a_opt.mandi_name} to {new_a_opt.mandi_name}",
                )
            )
        diff_items.append(
            DiffItem(
                term="score_rs",
                old=round(old_a_opt.score, 0),
                new=round(new_a_opt.score, 0),
                delta=round(new_a_opt.score - old_a_opt.score, 0),
                effect="risk-adjusted value change",
            )
        )
        diff_items.append(
            DiffItem(
                term="duration_h",
                old=round(old_a_opt.duration_h_nominal, 2),
                new=round(new_a_opt.duration_h_nominal, 2),
                delta=round(new_a_opt.duration_h_nominal - old_a_opt.duration_h_nominal, 2),
                effect="travel time change",
            )
        )

    rec_changed = bool(old_a_opt and new_a_opt and (old_a_opt.mandi_id != new_a_opt.mandi_id))
    diff = PlanDiff(
        old_version=orig_planset.version,
        new_version=new_planset.version,
        primary_reason="What-if scenario simulation",
        items=diff_items,
        recommendation_changed=rec_changed,
    )

    # Render diff_text
    ev_index = {e.id: e for e in ev_copy}
    try:
        diff_text = render_diff(diff, ev_index)
    except Exception:
        diff_text = f"What-if evaluated: {'Plan A changed to ' + new_a_opt.mandi_name if rec_changed else 'Plan A unchanged'}"

    changes = {
        "delay_min": body.delay_min,
        "price_delta_pct": body.price_delta_pct,
        "rain_mm_segment": body.rain_mm_segment,
        "closing_time": body.closing_time,
    }

    # NEVER persist in plan_versions or plans!
    return {
        "planset": new_planset.model_dump(),
        "what_if": True,
        "changes": changes,
        "diff_text": diff_text,
        "diff": diff.model_dump(),
    }
