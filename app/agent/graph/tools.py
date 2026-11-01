"""
Discrete, typed tools invoked by autonomous agents in the MandiGuard LangGraph workflow.
Pure and deterministic operations; zero hardcoded fallback data.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Optional

from app import guards
from app.agent.graph.tracer import get_tracer
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
    t0 = get_tracer().on_tool_call(
        tool_name="validate_request_tool",
        source="System Constraints / DispatchRequest Contract",
        args={"crop": req.crop, "origin": req.origin_label, "quantity_qtl": req.quantity_qtl},
        provenance="USER-REPORTED",
    )

    if req.quantity_qtl <= 0:
        res = {"valid": False, "error": f"Quantity must be positive quintals, got {req.quantity_qtl}"}
    elif req.loading_time.tzinfo is None or req.preferred_departure.tzinfo is None:
        res = {"valid": False, "error": "Datetimes must be timezone-aware"}
    elif not req.crop:
        res = {"valid": False, "error": "Crop must be specified"}
    else:
        res = {
            "valid": True,
            "crop": req.crop,
            "origin": req.origin_label,
            "quantity_qtl": req.quantity_qtl,
            "preferred_departure": req.preferred_departure.isoformat(),
        }

    status = "SUCCESS" if res["valid"] else "FAILED"
    get_tracer().on_tool_result("validate_request_tool", t0, f"Valid: {res['valid']}", status=status, provenance="USER-REPORTED")
    return res


def check_system_mode_tool() -> dict[str, Any]:
    """
    Check current operating mode and banner status via guards.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="check_system_mode_tool",
        source="app.guards.mode",
        args={},
        provenance="SYSTEM-CONFIG",
    )

    current_mode = guards.mode()
    current_banner = guards.banner()
    res = {
        "mode": current_mode,
        "banner": current_banner,
        "live": current_mode == "live",
    }
    get_tracer().on_tool_result("check_system_mode_tool", t0, f"Operating mode: {current_mode}", provenance="SYSTEM-CONFIG")
    return res


def assemble_candidates_tool(req: DispatchRequest, now: datetime) -> CandidateAssembly:
    """
    Fetch candidate mandis, prices, routes, and route weather.
    Selects provider based on current mode or any monkeypatched candidate assembler.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="assemble_candidates_tool",
        source="Agmarknet (data.gov.in) + Open-Meteo + OSRM",
        args={"origin": [req.origin.lat, req.origin.lon], "max_km": req.max_distance_km, "crop": req.crop},
        provenance="LIVE / CACHED",
    )

    import app.agent.flow as flow_mod
    flow_assembler = getattr(flow_mod, "assemble_candidates", None)
    if flow_assembler and flow_assembler is not assemble_candidates:
        res = flow_assembler(req, now)
    else:
        from app.api.deps import get_candidate_assembler
        assembler_fn: Callable[[DispatchRequest, datetime], CandidateAssembly] = get_candidate_assembler()
        res = assembler_fn(req, now)

    summary = f"Assembled {len(res.candidates)} candidate mandis with {len(res.evidence)} evidence items"
    get_tracer().on_tool_result("assemble_candidates_tool", t0, summary, provenance="LIVE / CACHED")
    return res


def critic_audit_tool(assembly: CandidateAssembly) -> dict[str, Any]:
    """
    Deterministic critic inspecting candidate assembly for quality and completeness.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="critic_audit_tool",
        source="Deterministic Assembly Auditor",
        args={"total_candidates": len(assembly.candidates), "evidence_count": len(assembly.evidence)},
        provenance="COMPUTED",
    )

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

    res = {
        "total_candidates": total_cands,
        "priced_candidates": len(priced_cands),
        "routable_candidates": len(routable_cands),
        "notes": notes,
        "can_optimize": len(routable_cands) > 0,
    }
    summary = f"Priced: {len(priced_cands)}, Feasible: {len(routable_cands)}, Can Optimize: {res['can_optimize']}"
    get_tracer().on_tool_result("critic_audit_tool", t0, summary, provenance="COMPUTED")
    return res


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
    t0 = get_tracer().on_tool_call(
        tool_name="evaluate_and_build_plans_tool",
        source="Decision Engine (9-Scenario Matrix & CVaR-25 Optimization)",
        args={"plan_id": plan_id, "version": version, "candidate_count": len(assembly.candidates), "risk_mode": req.risk_mode},
        provenance="COMPUTED",
    )

    cfg = get_settings()
    planset = build_plan_set(
        req=req,
        cands=assembly.candidates,
        evidence=assembly.evidence,
        now=now,
        cfg=cfg,
        plan_id=plan_id,
        version=version,
    )
    plan_a_dest = planset.plans[0].option.mandi_name if planset.plans else "N/A"
    summary = f"Built {len(planset.plans)} plans (Plan A Target: {plan_a_dest}, Coverage Grade: {planset.coverage.grade})"
    get_tracer().on_tool_result("evaluate_and_build_plans_tool", t0, summary, provenance="COMPUTED")
    return planset


def extract_geo_features_tool(
    candidates: list[CandidateInputs],
    planset: PlanSet,
) -> dict[str, Any]:
    """
    Extract coordinates and route polylines for UI map rendering.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="extract_geo_features_tool",
        source="OSRM Polyline Coordinate Extractor",
        args={"candidates_count": len(candidates), "plan_count": len(planset.plans)},
        provenance="COMPUTED",
    )

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

    res = {
        "map_points": map_points,
        "route_paths": route_paths,
    }
    summary = f"Extracted {len(map_points)} map points and {len(route_paths)} route polylines"
    get_tracer().on_tool_result("extract_geo_features_tool", t0, summary, provenance="COMPUTED")
    return res


def generate_plan_summary_tool(
    planset: PlanSet,
    assembly: CandidateAssembly,
) -> tuple[Optional[str], str]:
    """
    Render deterministic plan summary if explainer module is available.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="generate_plan_summary_tool",
        source="Template Explainer (AGENT.md §7 Deterministic Wording)",
        args={"plan_id": planset.plan_id, "version": planset.version},
        provenance="COMPUTED",
    )

    try:
        from app.explain import render_plan_summary
        evidence_index = {e.id: e for e in assembly.evidence}
        summary = render_plan_summary(planset, evidence_index)
        tool_msg = "Plan summary rendered"
    except ImportError:
        summary, tool_msg = None, "Explainer not installed, summary=None"
    except Exception as e:
        summary, tool_msg = None, f"Explainer error: {e}"

    get_tracer().on_tool_result("generate_plan_summary_tool", t0, tool_msg, provenance="COMPUTED")
    return summary, tool_msg


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
    t0 = get_tracer().on_tool_call(
        tool_name="refresh_monitoring_data_tool",
        source="Agmarknet (data.gov.in) + Open-Meteo Weather Along Route",
        args={"plan_option_ids": plan_option_ids, "now": now.isoformat(), "force": force},
        provenance="LIVE / CACHED",
    )

    import app.agent.flow as flow_mod
    flow_refresh = getattr(flow_mod, "refresh_for_monitoring", None)
    if flow_refresh and flow_refresh is not refresh_for_monitoring:
        res = flow_refresh(req, plan_option_ids, now, variety=variety, grade=grade, force=force)
    else:
        res = refresh_for_monitoring(req, plan_option_ids, now, variety=variety, grade=grade, force=force)

    summary = f"Refreshed {len(res.candidates)} candidates, {len(res.evidence)} evidence items, {len(res.warnings)} warnings"
    get_tracer().on_tool_result("refresh_monitoring_data_tool", t0, summary, provenance="LIVE / CACHED")
    return res


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
    t0 = get_tracer().on_tool_call(
        tool_name="evaluate_change_and_triggers_tool",
        source="Deterministic Triggers Engine (T1-T5, Hysteresis, Remaining Leg Recompute)",
        args={"delay_min": progress.reported_delay_min, "waypoint": progress.last_waypoint_idx, "cooldown_until": state.cooldown_until},
        provenance="COMPUTED",
    )

    cfg = get_settings()
    decision = evaluate_triggers_and_recompute(
        prev=prev,
        req=req,
        fresh_cands=fresh_cands,
        fresh_evidence=fresh_evidence,
        progress=progress,
        state=state,
        now=now,
        cfg=cfg,
    )

    summary = f"Fired: {decision.fired}, Persisted: {decision.persisted}, Propose: {decision.propose_replan}. Reason: {decision.reason}"
    get_tracer().on_tool_result("evaluate_change_and_triggers_tool", t0, summary, provenance="COMPUTED")
    return decision


def render_diff_summary_tool(
    diff: PlanDiff,
    evidence_index: dict[str, Evidence],
) -> tuple[Optional[str], str]:
    """
    Render deterministic diff summary explaining the reasons for proposed plan revision.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="render_diff_summary_tool",
        source="Template Explainer (B9b Diff Summary)",
        args={"old_version": diff.old_version, "new_version": diff.new_version, "primary_reason": diff.primary_reason},
        provenance="COMPUTED",
    )

    try:
        from app.explain import render_diff
        summary = render_diff(diff, evidence_index)
        tool_msg = "PlanDiff summary rendered"
    except ImportError:
        summary, tool_msg = None, "Explainer not installed, diff_summary=None"
    except Exception as e:
        summary, tool_msg = None, f"Diff explainer error: {e}"

    get_tracer().on_tool_result("render_diff_summary_tool", t0, tool_msg, provenance="COMPUTED")
    return summary, tool_msg


def load_plan_from_db_tool(plan_id: str) -> Optional[dict[str, Any]]:
    """
    Hydrate plan, latest version planset, watch state, and progress from SQLite persistence.
    """
    t0 = get_tracer().on_tool_call(
        tool_name="load_plan_from_db_tool",
        source="SQLite Database (plans, plan_versions, watch_state)",
        args={"plan_id": plan_id},
        provenance="PERSISTED-STATE",
    )

    with get_conn() as conn:
        p_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not p_row:
            get_tracer().on_tool_result("load_plan_from_db_tool", t0, f"Plan '{plan_id}' not found", status="FAILED", provenance="PERSISTED-STATE")
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

    res = {
        "plan_id": p_row["plan_id"],
        "request": DispatchRequest.model_validate_json(p_row["request_json"]),
        "status": p_row["status"],
        "latest_version": latest_v["version"] if latest_v else 1,
        "planset": planset,
        "diff": diff,
        "watch_state": watch_state,
        "progress": last_prog,
    }
    get_tracer().on_tool_result("load_plan_from_db_tool", t0, f"Loaded plan {plan_id} (status: {p_row['status']}, version: {res['latest_version']})", provenance="PERSISTED-STATE")
    return res


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
    t0 = get_tracer().on_tool_call(
        tool_name="save_plan_version_tool",
        source="SQLite Database (plan_versions, plans)",
        args={"plan_id": plan_id, "version": version, "status": status},
        provenance="PERSISTED-STATE",
    )

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

    get_tracer().on_tool_result("save_plan_version_tool", t0, f"Persisted PlanSet v{version} with status='{status}'", provenance="PERSISTED-STATE")


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
    t0 = get_tracer().on_tool_call(
        tool_name="save_watch_state_tool",
        source="SQLite Database (watch_state, watch_events)",
        args={"plan_id": plan_id, "event_kind": event_kind, "cooldown_until": watch_state.cooldown_until},
        provenance="PERSISTED-STATE",
    )

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

    get_tracer().on_tool_result("save_watch_state_tool", t0, f"Saved watch event '{event_kind}'", provenance="PERSISTED-STATE")
