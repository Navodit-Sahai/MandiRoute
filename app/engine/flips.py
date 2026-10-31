"""
B7 - Flip boundaries (PLAN 7.6). Delay is applied to plan A only (D-13).

    Delay flip : scan d = 0.00 .. 1.00 step 0.05 at typical price; delay_flip_pct = smallest
                 d x 100 with Net_B_nominal > Net_A(d); else None ("no flip within tested range").
                 diversion_feasible_at_flip = B6 last_feasible_waypoint at that d is not None
                 (None if no flip or B's closing time unknown).
    Price flip : A's nominal window state fixed;
                 P_A* = (Net_B + transport_A + fixed_ded_A + hold_A) / (Q x (1 - pct))
                 = A's typical price below which B wins (None if not positive).
"""
from __future__ import annotations

from datetime import datetime

from app.contracts import CandidateInputs, DispatchRequest, FlipBoundaries, PlanOption, RouteOption
from app.engine.commit import closing_deadline, last_feasible_waypoint
from app.engine.netvalue import net_breakdown
from app.engine.scenarios import evaluate_option

NOTE = "approximate (delay applied to plan A only; tested delays 0-100% in 5% steps; typical prices)"
DELAY_STEPS = [round(i * 0.05, 2) for i in range(21)]


def _nominal(opt: PlanOption):
    return next(s for s in opt.scenario_results if s.price_level == "typical" and s.delay_level == "none")


def _net_a_at_delay(req: DispatchRequest, cand_a: CandidateInputs, route_a: RouteOption,
                    depart: datetime, delay: float, cfg) -> float:
    cfg_d = cfg.model_copy(update={"delay_mult": {"none": delay, "moderate": delay, "severe": delay}})
    return _nominal(evaluate_option(req, cand_a, route_a, depart, cfg_d)).net_value


def compute_flips(req: DispatchRequest, plan_a: PlanOption, plan_b: PlanOption, cand_a: CandidateInputs,
                  cand_b: CandidateInputs, route_a: RouteOption, cfg) -> FlipBoundaries:
    depart = datetime.fromisoformat(plan_a.depart_iso)
    net_b = _nominal(plan_b).net_value

    delay_flip: float | None = None
    for delay in DELAY_STEPS:
        if net_b > _net_a_at_delay(req, cand_a, route_a, depart, delay, cfg):
            delay_flip = delay
            break

    diversion: bool | None = None
    closing_b = cand_b.latest_arrival_local or req.mandi_latest_arrival.get(cand_b.mandi_id)
    if delay_flip is not None and closing_b is not None:
        deadline = closing_deadline(depart, closing_b, cfg.timezone_name)
        diversion = last_feasible_waypoint(depart, route_a, cand_b.mandi_id, deadline, delay_flip) is not None

    wait_h = cfg.next_day_wait_h if _nominal(plan_a).window_status == "missed" else 0.0
    bd = net_breakdown(req, cand_a.price.typical, route_a.distance_km, wait_h)
    share_kept = 1.0 - bd.percent_deduction / bd.gross
    price_flip: float | None = None
    if share_kept > 0:
        p_star = (net_b + bd.transport + bd.fixed_deduction + bd.holding) / (req.quantity_qtl * share_kept)
        price_flip = p_star if p_star > 0 else None

    note = NOTE if delay_flip is not None else f"{NOTE}; no delay flip within tested range"
    return FlipBoundaries(
        delay_flip_pct=None if delay_flip is None else round(delay_flip * 100, 1),
        price_flip_per_qtl=price_flip,
        diversion_feasible_at_flip=diversion,
        note=note,
    )
