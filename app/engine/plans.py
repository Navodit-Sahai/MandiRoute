"""
B5 - Plan builder A/B/C (BLUEPRINT B5, PLAN 7.4).

Constructs the three dispatch options (A = primary, B = hedge/standby, C = delayed departure)
and packages them into a PlanSet. Pure function, no I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from app.config import Settings
from app.contracts import (
    CandidateInputs, Condition, CoverageReport, DispatchRequest, Evidence, Plan, PlanOption, PlanSet, RouteOption,
)
from app.engine.commit import compute_commit_points
from app.engine.coverage import grade_coverage
from app.engine.flips import compute_flips
from app.engine.scenarios import evaluate_option, sort_options


def _find_route(cand: CandidateInputs, route_id: str) -> RouteOption:
    return next(r for r in cand.routes if r.route_id == route_id)


def _reconsider_conditions(opt_a: PlanOption, typical_a: float, delay_flip_pct: float | None,
                           price_flip: float | None, cfg: Settings) -> list[Condition]:
    """Reconsider-if rules for A (PLAN 7.6). Every number comes from the engine; no ripening rule (SR-1)."""
    conds: list[Condition] = []
    if delay_flip_pct is not None:
        slip_h = delay_flip_pct / 100.0 * opt_a.duration_h_nominal
        conds.append(Condition(kind="eta_slip_h", comparison=">=", threshold=round(slip_h, 2),
                               rationale=f"a delay of {delay_flip_pct:g}% of normal travel time makes B better than A"))
    if price_flip is not None and 0 < price_flip < typical_a:
        drop_pct = (typical_a - price_flip) / typical_a * 100.0
        conds.append(Condition(kind="price_drop_pct", comparison=">=", threshold=round(drop_pct, 1),
                               rationale=f"below Rs {price_flip:,.0f}/qtl typical price at A, B is better"))
    conds.append(Condition(kind="window_miss_scenarios", comparison=">=", threshold=float(cfg.window_miss_trigger),
                           rationale=f"A misses the closing time in {cfg.window_miss_trigger} or more tested scenarios"))
    return conds


def _typical_price(cands: list[CandidateInputs], mandi_id: str) -> float:
    return next(c.price.typical for c in cands if c.mandi_id == mandi_id and c.price is not None)


def build_plan_set(
    req: DispatchRequest,
    cands: list[CandidateInputs],
    evidence: list[Evidence],
    now: datetime,
    cfg: Settings,
    plan_id: str,
    version: int = 1,
) -> PlanSet:
    """
    Build PlanSet with Plan A, Plan B (hedge or standby), and Plan C (departure slots).

    Ripening is OFF (SR-1): no ripening eligibility filter, no staging ripening for C,
    and no ripening lines in conditions.
    """
    coverage_report: CoverageReport = grade_coverage(req, cands, evidence, now, cfg)

    # Collect priced candidates with usable routes
    priced_cands: list[CandidateInputs] = [c for c in cands if c.price is not None]

    if coverage_report.grade == "ABSTAIN" or not priced_cands:
        initial_ev_ids: list[str] = [e.id for e in evidence]
        return PlanSet(
            plan_id=plan_id,
            version=version,
            created_at=now.isoformat(),
            plans=[],
            robustness_a_best_in=0,
            robustness_of=9,
            coverage=coverage_report,
            evidence_ids=initial_ev_ids,
        )

    # 1. Evaluate all candidate options departing at preferred_departure
    eligible_options: list[PlanOption] = []
    for cand in priced_cands:
        for route in cand.routes:
            if not route.geometry_ok:
                continue
            option: PlanOption = evaluate_option(
                req=req,
                cand=cand,
                route=route,
                depart=req.preferred_departure,
                cfg=cfg,
                wait_hours_after=0.0,
            )
            eligible_options.append(option)

    if not eligible_options:
        return PlanSet(
            plan_id=plan_id,
            version=version,
            created_at=now.isoformat(),
            plans=[],
            robustness_a_best_in=0,
            robustness_of=9,
            coverage=coverage_report,
            evidence_ids=[e.id for e in evidence],
        )

    # Sort eligible options deterministically by score
    sorted_eligible: list[PlanOption] = sort_options(eligible_options)

    # Plan A = argmax score over eligible options
    opt_a: PlanOption = sorted_eligible[0]
    a_note: str = f"Primary recommendation (best score under {req.risk_mode})"
    plan_a: Plan = Plan(label="A", option=opt_a, role_note=a_note)
    plans: list[Plan] = [plan_a]

    # Robustness of Plan A across the 9 money scenarios
    robustness_count: int = 0
    for s_idx in range(9):
        best_scenario_net: float = max(opt.scenario_results[s_idx].net_value for opt in eligible_options)
        if opt_a.scenario_results[s_idx].net_value >= best_scenario_net - 1e-9:
            robustness_count += 1

    # 2. Identify Fail(A) scenarios
    fail_a_indices: list[int] = []
    for s_idx in range(9):
        is_window_missed: bool = opt_a.scenario_results[s_idx].window_status == "missed"
        net_a: float = opt_a.scenario_results[s_idx].net_value
        max_p_net: float = max(opt.scenario_results[s_idx].net_value for opt in eligible_options)
        regret: float = max_p_net - net_a
        regret_threshold: float = max(cfg.eps_abs_rs, cfg.eps_rel * abs(net_a))
        if is_window_missed or (regret > regret_threshold):
            fail_a_indices.append(s_idx)

    # 3. Plan B = best alternative option
    b_pool: list[PlanOption] = [opt for opt in eligible_options if opt.option_id != opt_a.option_id]
    if b_pool:
        if not fail_a_indices:
            best_b_opt: PlanOption = sort_options(b_pool)[0]
            b_note: str = "A is robust in all tested scenarios; B is the standby"
        else:
            def _b_key(p: PlanOption) -> tuple[float, float, float, float, str]:
                mean_fail_net: float = sum(p.scenario_results[idx].net_value for idx in fail_a_indices) / len(fail_a_indices)
                return (-mean_fail_net, -p.score, -p.mean_net, p.duration_h_nominal, p.option_id)

            best_b_opt = min(b_pool, key=_b_key)
            b_note = f"Alternative hedge: best performance in {len(fail_a_indices)} scenarios where Plan A underperforms"

        plan_b: Plan = Plan(label="B", option=best_b_opt, role_note=b_note)
        plans.append(plan_b)

        # Commitment points (B6) and flip boundaries (B7): A monitored, B the alternative
        cand_by_id: dict[str, CandidateInputs] = {c.mandi_id: c for c in priced_cands}
        cand_a: CandidateInputs = cand_by_id[opt_a.mandi_id]
        cand_b: CandidateInputs = cand_by_id[best_b_opt.mandi_id]
        route_a: RouteOption = _find_route(cand_a, opt_a.route_id)
        plan_a.commit = compute_commit_points(req, opt_a, route_a, best_b_opt, cand_b, cfg)
        plan_a.flip = compute_flips(req, opt_a, best_b_opt, cand_a, cand_b, route_a, cfg)
    plan_a.reconsider_if = _reconsider_conditions(
        opt_a, _typical_price(priced_cands, opt_a.mandi_id), plan_a.flip.delay_flip_pct,
        plan_a.flip.price_flip_per_qtl, cfg)

    # 4. Plan C = best over departure slots
    c_pool: list[PlanOption] = []
    slots: list[float] = getattr(cfg, "departure_slots_h", [2.0, 4.0, 8.0, 12.0])
    for slot_hours in slots:
        depart_c: datetime = req.preferred_departure + timedelta(hours=slot_hours)
        for cand in priced_cands:
            for route in cand.routes:
                if not route.geometry_ok:
                    continue
                opt_c: PlanOption = evaluate_option(
                    req=req,
                    cand=cand,
                    route=route,
                    depart=depart_c,
                    cfg=cfg,
                    wait_hours_after=slot_hours,
                )
                # Route weather is aligned to the preferred departure only -> no rain figure for C
                c_pool.append(opt_c.model_copy(update={"rain_exposure_mm": None}))

    if c_pool:
        best_c_opt: PlanOption = sort_options(c_pool)[0]
        score_diff: float = best_c_opt.score - opt_a.score
        delay_h: float = (datetime.fromisoformat(best_c_opt.depart_iso) - req.preferred_departure).total_seconds() / 3600.0
        delay_display: str = f"{delay_h:g}"

        if score_diff > 1e-6:
            c_note: str = f"Departure +{delay_display}h beats Plan A score by Rs {round(score_diff):,}"
        elif score_diff < -1e-6:
            c_note = f"Departure +{delay_display}h trails Plan A score by Rs {round(abs(score_diff)):,}"
        else:
            c_note = f"Departure +{delay_display}h matches Plan A score"

        plan_c: Plan = Plan(label="C", option=best_c_opt, role_note=c_note)
        plans.append(plan_c)

    # Aggregate evidence IDs (preserve order, deduplicate)
    collected_ev_ids: list[str] = [e.id for e in evidence]
    for p in plans:
        collected_ev_ids.extend(p.option.evidence_ids)

    seen_ids: set[str] = set()
    unique_ev_ids: list[str] = [eid for eid in collected_ev_ids if not (eid in seen_ids or seen_ids.add(eid))]

    return PlanSet(
        plan_id=plan_id,
        version=version,
        created_at=now.isoformat(),
        plans=plans,
        robustness_a_best_in=robustness_count,
        robustness_of=9,
        coverage=coverage_report,
        evidence_ids=unique_ev_ids,
    )

