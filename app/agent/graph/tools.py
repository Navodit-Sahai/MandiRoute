"""
Discrete, typed tools invoked by autonomous agents in the MandiGuard LangGraph workflow.
Pure and deterministic operations; zero hardcoded fallback data.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Optional

from app import guards
from app.config import get_settings
from app.contracts import (
    CandidateInputs,
    DispatchProgress,
    DispatchRequest,
    Evidence,
    PlanDiff,
    PlanSet,
)
from app.data.assemble import CandidateAssembly, assemble_candidates, refresh_for_monitoring
from app.db.db import get_conn
from app.engine.plans import build_plan_set
from app.engine.triggers import TriggerDecision, WatchState, evaluate_triggers_and_recompute


def validate_request_tool(req: DispatchRequest) -> dict[str, Any]:
    """
    Validate dispatch request constraints.
    Returns validation status and summary.
    """
    if req.quantity_qtl <= 0:
        return {"valid": False, "error": f"Quantity must be positive quintals, got {req.quantity_qtl}"}
    if req.loading_time.tzinfo is None or req.preferred_departure.tzinfo is None:
        return {"valid": False, "error": "Datetimes must be timezone-aware"}
    if req.crop != "Tomato":
        return {"valid": False, "error": f"MVP supports crop 'Tomato' only, got '{req.crop}'"}
    return {
        "valid": True,
        "crop": req.crop,
        "origin": req.origin_label,
        "quantity_qtl": req.quantity_qtl,
        "preferred_departure": req.preferred_departure.isoformat(),
    }


def check_system_mode_tool() -> dict[str, Any]:
    """
    Check current operating mode and banner status via guards.
    """
    current_mode = guards.mode()
    current_banner = guards.banner()
    return {
        "mode": current_mode,
        "banner": current_banner,
        "live": current_mode == "live",
    }


def assemble_candidates_tool(req: DispatchRequest, now: datetime) -> CandidateAssembly:
    """
    Fetch candidate mandis, prices, routes, and route weather.
    Selects provider based on current mode or any monkeypatched candidate assembler.
    """
    import app.agent.flow as flow_mod
    flow_assembler = getattr(flow_mod, "assemble_candidates", None)
    if flow_assembler and flow_assembler is not assemble_candidates:
        return flow_assembler(req, now)
    from app.api.deps import get_candidate_assembler
    assembler_fn: Callable[[DispatchRequest, datetime], CandidateAssembly] = get_candidate_assembler()
    return assembler_fn(req, now)


def critic_audit_tool(assembly: CandidateAssembly) -> dict[str, Any]:
    """
    Deterministic critic inspecting candidate assembly for quality and completeness.
    """
    total_cands = len(assembly.candidates)
    priced_cands = [c for c in assembly.candidates if c.price is not None]
    routable_cands = [c for c in priced_cands if any(r.geometry_ok for r in c.routes)]

    notes: list[str] = []
    if total_cands == 0:
        notes.append("No candidates assembled")
    elif len(priced_cands) == 0:
        notes.append("No candidate mandis have price bands")
    elif len(routable_cands) == 0:
        notes.append("No priced candidate mandis have feasible routes")

    return {
        "total_candidates": total_cands,
        "priced_candidates": len(priced_cands),
        "routable_candidates": len(routable_cands),
        "notes": notes,
        "can_optimize": len(routable_cands) > 0,
    }


def evaluate_and_build_plans_tool(
    req: DispatchRequest,
    assembly: CandidateAssembly,
    now: datetime,
    plan_id: str,
    version: int = 1,
) -> PlanSet:
    """
    Execute decision engine to construct Plan A/B/C, scenarios, flips, commit points, and coverage report.
    """
    cfg = get_settings()
    return build_plan_set(
        req=req,
        cands=assembly.candidates,
        evidence=assembly.evidence,
        now=now,
        cfg=cfg,
        plan_id=plan_id,
        version=version,
    )


def extract_geo_features_tool(
    candidates: list[CandidateInputs],
    planset: PlanSet,
) -> dict[str, Any]:
    """
    Extract coordinates and route polylines for UI map rendering.
    """
    map_points: list[dict[str, Any]] = [
        {
            "mandi_id": c.mandi_id,
            "name": c.mandi_name,
            "lat": c.location.lat,
            "lon": c.location.lon,
            "precision": c.location_precision,
        }
        for c in candidates
    ]
    used_route_ids = {p.option.route_id for p in planset.plans}
    route_paths: dict[str, list[list[float]]] = {}
    for c in candidates:
        for r in c.routes:
            if r.route_id in used_route_ids and r.route_id not in route_paths:
                route_paths[r.route_id] = [[wp.point.lat, wp.point.lon] for wp in r.waypoints]

    return {
        "map_points": map_points,
        "route_paths": route_paths,
    }


def generate_plan_summary_tool(
    planset: PlanSet,
    assembly: CandidateAssembly,
) -> tuple[Optional[str], str]:
    """
    Render deterministic plan summary if explainer module is available.
    """
    try:
        from app.explain import render_plan_summary
        evidence_index = {e.id: e for e in assembly.evidence}
        summary = render_plan_summary(planset, evidence_index)
        return summary, "Plan summary rendered"
    except ImportError:
        return None, "Explainer not installed, summary=None"
    except Exception as e:
        return None, f"Explainer error: {e}"


# -----------------------------------------------------------------------------
# Monitoring & Replanning Tools
# -----------------------------------------------------------------------------

def refresh_monitoring_data_tool(
    req: DispatchRequest,
    plan_option_ids: list[str],
    now: datetime,
    variety: Optional[str] = None,
    grade: Optional[str] = None,
    force: bool = False,
) -> CandidateAssembly:
    """
    Targeted re-fetching of price and weather observations for mandis in active plan options.
    """
    import app.agent.flow as flow_mod
    flow_refresh = getattr(flow_mod, "refresh_for_monitoring", None)
    if flow_refresh and flow_refresh is not refresh_for_monitoring:
        return flow_refresh(req, plan_option_ids, now, variety=variety, grade=grade, force=force)
    return refresh_for_monitoring(req, plan_option_ids, now, variety=variety, grade=grade, force=force)


def evaluate_change_and_triggers_tool(
    prev: PlanSet,
    req: DispatchRequest,
    fresh_cands: list[CandidateInputs],
    fresh_evidence: list[Evidence],
    progress: DispatchProgress,
    state: WatchState,
    now: datetime,
) -> TriggerDecision:
    """
    Pure decision engine evaluation of triggers (T1-T5), hysteresis counters,
    cooldowns, and dynamic recompute on remaining routes.
    """
    cfg = get_settings()
    return evaluate_triggers_and_recompute(
        prev=prev,
        req=req,
        fresh_cands=fresh_cands,
        fresh_evidence=fresh_evidence,
        progress=progress,
        state=state,
        now=now,
        cfg=cfg,
    )


def render_diff_summary_tool(
    diff: PlanDiff,
    evidence_index: dict[str, Evidence],
) -> tuple[Optional[str], str]:
    """
    Render deterministic diff summary explaining the reasons for proposed plan revision.
    """
    try:
        from app.explain import render_diff
        summary = render_diff(diff, evidence_index)
        return summary, "PlanDiff summary rendered"
    except ImportError:
        return None, "Explainer not installed, diff_summary=None"
    except Exception as e:
        return None, f"Diff explainer error: {e}"


def load_plan_from_db_tool(plan_id: str) -> Optional[dict[str, Any]]:
    """
    Hydrate plan, latest version planset, watch state, and progress from SQLite persistence.
    """
    with get_conn() as conn:
        p_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not p_row:
            return None
        v_rows = conn.execute(
            "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC", (plan_id,)
        ).fetchall()
        w_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (plan_id,)).fetchone()

    latest_v = v_rows[0] if v_rows else None
    planset = None
    if latest_v and latest_v["planset_json"]:
        planset = PlanSet.model_validate_json(latest_v["planset_json"])

    diff = None
    if latest_v and latest_v["diff_json"]:
        diff = PlanDiff.model_validate_json(latest_v["diff_json"])

    watch_state = None
    last_prog = None
    if w_row:
        counts = json.loads(w_row["trigger_counts_json"]) if w_row["trigger_counts_json"] else {}
        watch_state = WatchState(
            trigger_counts=counts,
            cooldown_until=w_row["cooldown_until"],
        )
        if w_row["last_progress_json"]:
            last_prog = DispatchProgress.model_validate_json(w_row["last_progress_json"])

    return {
        "plan_id": p_row["plan_id"],
        "request": DispatchRequest.model_validate_json(p_row["request_json"]),
        "status": p_row["status"],
        "latest_version": latest_v["version"] if latest_v else 1,
        "planset": planset,
        "diff": diff,
        "watch_state": watch_state,
        "progress": last_prog,
    }


def save_plan_version_tool(
    plan_id: str,
    version: int,
    planset: PlanSet,
    diff: Optional[PlanDiff],
    status: str,
    now: datetime,
) -> None:
    """
    Persist new plan version in SQLite plan_versions and update plan status.
    """
    now_iso = now.isoformat()
    diff_json = diff.model_dump_json() if diff else None
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO plan_versions(plan_id, version, planset_json, diff_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (plan_id, version, planset.model_dump_json(), diff_json, now_iso),
        )
        conn.execute(
            """
            UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?
            """,
            (status, now_iso, plan_id),
        )


def save_watch_state_tool(
    plan_id: str,
    watch_state: WatchState,
    progress: Optional[DispatchProgress],
    event_kind: str,
    event_detail: dict[str, Any],
    now: datetime,
) -> None:
    """
    Update persistent watch state and record monitoring audit trail in watch_events.
    """
    now_iso = now.isoformat()
    counts_json = json.dumps(watch_state.trigger_counts)
    prog_json = progress.model_dump_json() if progress else None
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                last_tick_ts = excluded.last_tick_ts,
                trigger_counts_json = excluded.trigger_counts_json,
                cooldown_until = excluded.cooldown_until,
                last_progress_json = excluded.last_progress_json
            """,
            (plan_id, now_iso, counts_json, watch_state.cooldown_until, prog_json),
        )
        conn.execute(
            """
            INSERT INTO watch_events(plan_id, ts, kind, detail_json)
            VALUES (?, ?, ?, ?)
            """,
            (plan_id, now_iso, event_kind, json.dumps(event_detail)),
        )
