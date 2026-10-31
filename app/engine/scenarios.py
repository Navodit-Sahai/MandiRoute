"""
B4 - Option evaluator & 9-scenario grid (BLUEPRINT B4, PLAN 7.2, 7.3).

Evaluates an option (CandidateInputs x RouteOption x Departure) across the 9 money
scenarios (3 price levels x 3 delay levels).
Pure function, no network or database I/O.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Sequence

from app.config import Settings
from app.contracts import CandidateInputs, PlanOption, RouteOption, ScenarioResult
from app.contracts.inputs import DispatchRequest
from app.engine.netvalue import net_value
from app.engine.units import delayed_arrival, hours_to_seconds, lowest_k_count, mean_of_lowest, window_status

PRICE_LEVELS: tuple[Literal["low", "typical", "high"], ...] = ("low", "typical", "high")
DELAY_LEVELS: tuple[Literal["none", "moderate", "severe"], ...] = ("none", "moderate", "severe")


def evaluate_option(
    req: DispatchRequest,
    cand: CandidateInputs,
    route: RouteOption,
    depart: datetime,
    cfg: Settings,
    wait_hours_after: float = 0.0,
) -> PlanOption:
    """
    Produce a PlanOption containing 9 ScenarioResults and risk-weighted score.

    Ripening is OFF by default (SR-1): ripening=None and ripening is neither
    imported nor called when cfg.ripening_enabled is False.
    """
    if depart.tzinfo is None:
        raise ValueError("departure datetime must be timezone-aware")
    if cand.price is None:
        raise ValueError(f"candidate '{cand.mandi_id}' has no price band")
    if wait_hours_after < 0:
        raise ValueError("wait_hours_after cannot be negative")

    # Closing time from candidate or request mapping
    closing_hhmm = cand.latest_arrival_local or req.mandi_latest_arrival.get(cand.mandi_id)

    duration_nominal_s = hours_to_seconds(route.duration_h_nominal)
    tz_name = cfg.timezone_name

    scenario_results: list[ScenarioResult] = []

    for p_level in PRICE_LEVELS:
        price_per_qtl = float(getattr(cand.price, p_level))
        for d_level in DELAY_LEVELS:
            mult = cfg.delay_mult.get(d_level, 0.0)
            arrival = delayed_arrival(depart, duration_nominal_s, mult)
            w_status = window_status(arrival, closing_hhmm, tz_name)

            wait_h = (cfg.next_day_wait_h if w_status == "missed" else 0.0) + wait_hours_after
            net = net_value(req, price_per_qtl, route.distance_km, wait_h)

            scenario_results.append(
                ScenarioResult(
                    price_level=p_level,
                    delay_level=d_level,
                    net_value=net,
                    arrival_iso=arrival.isoformat(),
                    window_status=w_status,
                )
            )

    # Scoring: CVaR25 (mean of 3 lowest) or mean
    all_nets = [sr.net_value for sr in scenario_results]
    mean_net = sum(all_nets) / len(all_nets)

    if req.risk_mode == "mean":
        score = mean_net
    else:
        k = lowest_k_count(len(all_nets), cfg.cvar_q)
        score = mean_of_lowest(all_nets, k)

    # Rain exposure aggregation
    rain_exposure_mm: float | None = None
    if route.weather:
        precip_samples = [w.precip_mm for w in route.weather if w.precip_mm is not None]
        if precip_samples:
            rain_exposure_mm = sum(precip_samples)

    # Ripening exposure (flag-guarded: none when disabled)
    ripening = None
    if getattr(cfg, "ripening_enabled", False):
        from app.engine.ripening import ripening_exposure
        # Re-enable if flag is turned on in future
        segments: list[tuple[float, float]] = []
        if route.waypoints:
            total_wps = len(route.waypoints)
            seg_duration_h = route.duration_h_nominal / max(1, total_wps - 1)
            for idx in range(max(1, total_wps - 1)):
                temp_c = 25.0
                if idx < len(route.weather) and route.weather[idx].temp_c is not None:
                    temp_c = route.weather[idx].temp_c
                segments.append((seg_duration_h, temp_c))
        if segments and req.ripeness is not None:
            ripening = ripening_exposure(
                stage=req.ripeness,
                segments=segments,
                vehicle=req.vehicle,
                setpoint_c=req.refrigerated_setpoint_c,
                limit_pct=req.ripening_limit_pct,
                cfg=cfg,
            )

    # Evidence IDs aggregation (preserve order, deduplicate)
    ev_ids: list[str] = []
    if cand.price and cand.price.evidence_ids:
        ev_ids.extend(cand.price.evidence_ids)
    ev_ids.extend(route.evidence_ids)
    for w in route.weather:
        if w.evidence_id:
            ev_ids.append(w.evidence_id)

    seen = set()
    unique_evidence_ids = [eid for eid in ev_ids if not (eid in seen or seen.add(eid))]

    option_id = f"{cand.mandi_id}::{route.route_id}::{depart.isoformat()}"

    return PlanOption(
        option_id=option_id,
        mandi_id=cand.mandi_id,
        mandi_name=cand.mandi_name,
        route_id=route.route_id,
        depart_iso=depart.isoformat(),
        distance_km=route.distance_km,
        duration_h_nominal=route.duration_h_nominal,
        rain_exposure_mm=rain_exposure_mm,
        scenario_results=scenario_results,
        score=score,
        mean_net=mean_net,
        ripening=ripening,
        evidence_ids=unique_evidence_ids,
    )


def option_sort_key(opt: PlanOption) -> tuple[float, float, float, str]:
    """
    Deterministic tie-break key (PLAN 7.3):
    1. Higher score first (-score)
    2. Higher mean_net first (-mean_net)
    3. Shorter nominal duration first (duration_h_nominal)
    4. option_id ascending (option_id)
    """
    return (-opt.score, -opt.mean_net, opt.duration_h_nominal, opt.option_id)


def sort_options(options: Sequence[PlanOption]) -> list[PlanOption]:
    """Sort options by score with deterministic tie-breaking."""
    return sorted(options, key=option_sort_key)

