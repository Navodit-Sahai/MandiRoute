"""
B9 - Triggers, hysteresis, recompute, diff (BLUEPRINT B9, PLAN 7.9). Pure, no I/O.

T1 ETA slip >= A's eta_slip_h reconsider threshold. T2 window-miss scenarios >= WINDOW_MISS_TRIGGER
(or A missed at nominal). T3 remaining-route rain up >= cfg.rain_trigger_delta_mm (CR-6) vs plan time.
T4 ripening: DISABLED unless cfg.ripening_enabled (SR-1). T5 new daily price record at or below
A's price-flip boundary: an event, fires at most once per record, no hysteresis.
T6 river-flow proxy: advisory only, never fires, never recomputes.
Comparisons are on the remaining leg only (sunk cost is identical for every option).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel

from app.config import Settings
from app.contracts import (
    CandidateInputs, DispatchProgress, DispatchRequest, DiffItem, Evidence, Plan, PlanDiff, PlanOption, PlanSet,
    RouteOption, Waypoint, WeatherSample,
)
from app.engine.plans import build_plan_set
from app.engine.scenarios import evaluate_option

HYSTERESIS_TRIGGERS: tuple[str, ...] = ("T1", "T2", "T3", "T4")
REASON_PRIORITY: tuple[str, ...] = ("T2", "T1", "T5", "T3", "T4")
TRIGGER_TEXT: dict[str, str] = {
    "T1": "ETA slip beyond the delay flip point",
    "T2": "closing-time misses in too many scenarios",
    "T3": "more rain expected on the remaining route",
    "T4": "ripening limit",
    "T5": "new price record at or below the price flip point",
}


class WatchState(BaseModel):
    trigger_counts: dict[str, int] = {}
    cooldown_until: str | None = None
    last_price_record_date: str | None = None


class TriggerDecision(BaseModel):
    fired: list[str]
    persisted: list[str]
    propose_replan: bool
    reason: str
    new_state: WatchState
    new_plan_set: PlanSet | None = None
    diff: PlanDiff | None = None


def _plan(ps: PlanSet, label: str) -> Optional[Plan]:
    return next((p for p in ps.plans if p.label == label), None)


def _nominal(opt: PlanOption):
    return next(s for s in opt.scenario_results if s.price_level == "typical" and s.delay_level == "none")


def _misses(opt: PlanOption) -> int:
    return sum(1 for s in opt.scenario_results if s.window_status == "missed")


def _remaining_routes(
    cands: list[CandidateInputs], cand_a: CandidateInputs, route_a: RouteOption, wp_idx: int,
) -> Optional[list[CandidateInputs]]:
    """Candidates rebuilt on the remaining leg from waypoint wp_idx; None if the waypoint is unknown."""
    wp: Optional[Waypoint] = next((w for w in route_a.waypoints if w.idx == wp_idx), None)
    total_s: float = route_a.duration_h_nominal * 3600.0
    if wp is None or total_s <= 0:
        return None
    rem_s: float = max(0.0, total_s - wp.elapsed_nominal_s)
    speed_kmh: float = route_a.distance_km / route_a.duration_h_nominal
    rem_wps: list[Waypoint] = [w.model_copy(update={"elapsed_nominal_s": max(0.0, w.elapsed_nominal_s - wp.elapsed_nominal_s)})
                               for w in route_a.waypoints if w.idx >= wp_idx]
    rem_wx: list[WeatherSample] = [w for w in route_a.weather if w.waypoint_idx >= wp_idx]
    out: list[CandidateInputs] = []
    for cand in cands:
        if cand.mandi_id == cand_a.mandi_id:
            own: RouteOption = route_a.model_copy(update={
                "distance_km": route_a.distance_km * rem_s / total_s, "duration_h_nominal": rem_s / 3600.0,
                "waypoints": rem_wps, "weather": rem_wx})
            out.append(cand.model_copy(update={"routes": [own]}))
            continue
        div_s: Optional[float] = wp.to_dest_s.get(cand.mandi_id)
        if div_s is None or div_s <= 0:
            continue   # no diversion time known from here: not offered
        div: RouteOption = RouteOption(
            route_id=f"{route_a.route_id}+wp{wp_idx}", distance_km=div_s / 3600.0 * speed_kmh,
            duration_h_nominal=div_s / 3600.0, waypoints=[], weather=[], geometry_ok=True,
            evidence_ids=list(route_a.evidence_ids))
        out.append(cand.model_copy(update={"routes": [div]}))
    return out


def _item(term: str, old, new, effect: str) -> Optional[DiffItem]:
    if old == new:
        return None
    delta: Optional[float] = None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        delta = round(float(new) - float(old), 2)
    return DiffItem(term=term, old=old, new=new, delta=delta, effect=effect)


def _build_diff(prev: PlanSet, cur: PlanOption, new_ps: PlanSet, cands: list[CandidateInputs],
                persisted: list[str], slip_h: float, gain: float) -> PlanDiff:
    old_a: PlanOption = _plan(prev, "A").option   # type: ignore[union-attr]
    new_a: PlanOption = new_ps.plans[0].option
    price: dict[str, float] = {c.mandi_id: c.price.typical for c in cands if c.price is not None}
    old_eta: datetime = datetime.fromisoformat(_nominal(cur).arrival_iso)
    new_eta: datetime = datetime.fromisoformat(_nominal(new_a).arrival_iso)
    raw: list[Optional[DiffItem]] = [
        _item("destination", old_a.mandi_name, new_a.mandi_name, "Plan A and the proposed plan swap destination"),
        _item("typical_price_per_qtl", price.get(old_a.mandi_id), price.get(new_a.mandi_id),
              "typical price of the latest record at each destination"),
        _item("eta_slip_h", 0.0, round(slip_h, 2), "reported delay against the plan"),
        _item("eta_nominal", old_eta.isoformat(), new_eta.isoformat(), "arrival at nominal speed, current vs proposed"),
        _item("window_status_nominal", _nominal(old_a).window_status, _nominal(new_a).window_status,
              "closing time at nominal speed, plan time vs proposed"),
        _item("window_miss_scenarios", _misses(old_a), _misses(new_a), "scenarios that miss the closing time"),
        _item("rain_mm_remaining", cur.rain_exposure_mm, new_a.rain_exposure_mm, "rain along the remaining route"),
        _item("score_rs", round(cur.score, 2), round(new_a.score, 2), f"risk-adjusted value gain Rs {gain:,.0f}"),
    ]
    items: list[DiffItem] = [i for i in raw if i is not None]
    top: str = next((t for t in REASON_PRIORITY if t in persisted), "")
    primary: str = f"{top}: {TRIGGER_TEXT[top]}" if top else "fresh data changed the best option"
    rec_changed: bool = (new_a.mandi_id, new_a.route_id) != (old_a.mandi_id, old_a.route_id)
    return PlanDiff(old_version=prev.version, new_version=new_ps.version, primary_reason=primary,
                    items=items, recommendation_changed=rec_changed)


def evaluate_triggers_and_recompute(
    prev: PlanSet,
    req: DispatchRequest,
    fresh_cands: list[CandidateInputs],
    fresh_evidence: list[Evidence],
    progress: DispatchProgress,
    state: WatchState,
    now: datetime,
    cfg: Settings,
) -> TriggerDecision:
    """One watchdog tick. Never mutates prev/state; returns a new WatchState."""
    counts: dict[str, int] = dict(state.trigger_counts)
    last_rec: Optional[str] = state.last_price_record_date
    plan_a: Optional[Plan] = _plan(prev, "A")
    notes: list[str] = []
    if any("river" in (e.label or "").lower() for e in fresh_evidence):
        notes.append("river-flow proxy noted (advisory only)")

    def _done(fired: list[str], persisted: list[str], reason: str, **kw) -> TriggerDecision:
        st: WatchState = WatchState(trigger_counts=counts, cooldown_until=kw.pop("cooldown", state.cooldown_until),
                                    last_price_record_date=last_rec)
        text: str = "; ".join([reason] + notes)
        return TriggerDecision(fired=fired, persisted=persisted, propose_replan=kw.pop("propose", False),
                               reason=text, new_state=st, **kw)

    if plan_a is None:
        return _done([], [], "no Plan A to monitor")

    opt_a: PlanOption = plan_a.option
    cand_a: Optional[CandidateInputs] = next((c for c in fresh_cands if c.mandi_id == opt_a.mandi_id), None)
    route_a: Optional[RouteOption] = None
    if cand_a is not None:
        route_a = next((r for r in cand_a.routes if r.route_id == opt_a.route_id), None)
    if cand_a is None or cand_a.price is None or route_a is None:
        notes.insert(0, "fresh data for Plan A missing; triggers T2/T3/T5 not checked")

    slip_h: float = max(0.0, progress.reported_delay_min / 60.0)
    depart: datetime = max(now, datetime.fromisoformat(opt_a.depart_iso)) + timedelta(hours=slip_h)
    dispatched: bool = progress.last_waypoint_idx is not None

    # ---- rebuild candidates on the remaining leg (dispatched) or as-is (not yet departed)
    work: Optional[list[CandidateInputs]] = None
    if cand_a is not None and cand_a.price is not None and route_a is not None:
        if dispatched:
            work = _remaining_routes(fresh_cands, cand_a, route_a, progress.last_waypoint_idx)  # type: ignore[arg-type]
            if work is None:
                notes.insert(0, "current waypoint not on the route; remaining route unknown")
        else:
            work = list(fresh_cands)

    cur: Optional[PlanOption] = None
    if work is not None:
        cand_cur: CandidateInputs = next(c for c in work if c.mandi_id == opt_a.mandi_id)
        cur = evaluate_option(req, cand_cur, cand_cur.routes[0] if dispatched else route_a, depart, cfg)  # type: ignore[arg-type]

    # ---- evaluate triggers
    fired: list[str] = []
    slip_conds: list[float] = [c.threshold for c in plan_a.reconsider_if if c.kind == "eta_slip_h"]
    if slip_conds and slip_h >= min(slip_conds) > 0:
        fired.append("T1")
    if cur is not None:
        if _misses(cur) >= cfg.window_miss_trigger or _nominal(cur).window_status == "missed":
            fired.append("T2")
        if (cur.rain_exposure_mm is not None and opt_a.rain_exposure_mm is not None
                and cur.rain_exposure_mm - opt_a.rain_exposure_mm >= cfg.rain_trigger_delta_mm):
            fired.append("T3")
        if cfg.ripening_enabled and cur.ripening is not None and cur.ripening.worst_consumed_pct is not None \
                and cur.ripening.worst_consumed_pct >= req.ripening_limit_pct:
            fired.append("T4")
    if cand_a is not None and cand_a.price is not None and cand_a.price.record_date != last_rec:
        last_rec = cand_a.price.record_date
        flip_p: Optional[float] = plan_a.flip.price_flip_per_qtl
        if flip_p is not None and cand_a.price.typical <= flip_p:
            fired.append("T5")

    # ---- hysteresis
    for t in HYSTERESIS_TRIGGERS:
        if t in fired:
            counts[t] = counts.get(t, 0) + 1
        else:
            counts.pop(t, None)
    persisted: list[str] = [t for t in HYSTERESIS_TRIGGERS if counts.get(t, 0) >= cfg.hysteresis_n]
    if "T5" in fired:
        persisted.append("T5")

    if not persisted:
        return _done(fired, [], "no trigger persisted" if fired else "no trigger")
    if state.cooldown_until is not None and now < datetime.fromisoformat(state.cooldown_until):
        return _done(fired, persisted, f"cooldown until {state.cooldown_until}; no replan")
    if work is None or cur is None:
        return _done(fired, persisted, "trigger persisted but fresh data missing; continue current plan")

    # ---- recompute from fresh data
    req2: DispatchRequest = req.model_copy(update={"preferred_departure": depart})
    cfg2: Settings = cfg.model_copy(update={"departure_slots_h": []}) if dispatched else cfg
    new_ps: PlanSet = build_plan_set(req2, work, fresh_evidence, now, cfg2, prev.plan_id, prev.version + 1)
    if not new_ps.plans:
        return _done(fired, persisted, "trigger fired, no usable fresh option; continue current plan")
    best: PlanOption = new_ps.plans[0].option
    gain: float = best.score - cur.score
    margin: float = max(cfg.replan_margin_abs_rs, cfg.replan_margin_rel * abs(cur.score))
    if (best.mandi_id, best.route_id) == (cur.mandi_id, cur.route_id) or gain < margin:
        return _done(fired, persisted, f"trigger fired, no better option (gain Rs {max(gain, 0.0):,.0f} "
                                       f"< margin Rs {margin:,.0f}); continue")
    for t in persisted:
        counts.pop(t, None)
    diff: PlanDiff = _build_diff(prev, cur, new_ps, work, persisted, slip_h, gain)
    until: str = (now + timedelta(minutes=cfg.cooldown_min)).isoformat()
    return _done(fired, persisted, f"replan proposed: {diff.primary_reason}; approval required",
                 propose=True, cooldown=until, new_plan_set=new_ps, diff=diff)
