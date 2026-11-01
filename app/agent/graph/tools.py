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
from app.contracts import CandidateInputs, DispatchRequest, PlanSet
from app.data.assemble import CandidateAssembly, assemble_candidates
from app.engine.plans import build_plan_set


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
