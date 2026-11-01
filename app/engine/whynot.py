"""
Why not the highest price? Pure engine function (no I/O, no ripening).

Takes the priced candidate with the highest typical price that is NOT Plan A's mandi, evaluates its
best route at Plan A's departure with B4 (evaluate_option), and compares Net per money scenario with A.
For every scenario where it loses, the reasons come from B3 net_breakdown of both options:
    transport  : its transport cost is higher (longer distance)          -> amount = extra Rs
    hold       : its hold/wait cost is higher (e.g. next-day wait)       -> amount = extra Rs
    window     : its closing time is missed or unknown in that scenario  -> amount = extra hold Rs (or None)
    price      : at that price level its price after % deductions is lower than A's -> amount = Rs less
Ties in typical price are broken by mandi_id (deterministic).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.contracts import (
    CandidateInputs,
    DispatchRequest,
    PlanOption,
    PlanSet,
    RouteOption,
    ScenarioResult,
)
from app.engine.coverage import usable_price
from app.engine.netvalue import NetBreakdown, net_breakdown
from app.engine.scenarios import evaluate_option, sort_options

EPS_RS = 0.5   # differences under half a rupee are treated as equal


class WhyNotReason(BaseModel):
    kind: Literal["transport", "window", "hold", "price"]
    amount_rs: float | None = None      # how much worse than A, rupees (None = not a money amount)
    window_status: Literal["met", "missed", "unknown"] | None = None


class WhyNotScenario(BaseModel):
    price_level: Literal["low", "typical", "high"]
    delay_level: Literal["none", "moderate", "severe"]
    net_a: float
    net_highest: float
    gap_rs: float                          # net_highest - net_a (negative = loses to A)
    loses: bool
    reasons: list[WhyNotReason] = []


class WhyNot(BaseModel):
    status: Literal["compared", "a_is_highest", "no_alternative", "no_plan"]
    a_mandi_name: str | None = None
    a_typical_price: float | None = None
    a_distance_km: float | None = None
    highest_option_id: str | None = None
    highest_mandi_name: str | None = None
    highest_typical_price: float | None = None
    highest_distance_km: float | None = None
    scenarios: list[WhyNotScenario] = []
    wins_in: int = 0
    loses_in: int = 0
    of: int = 0


def _wait_h(s: ScenarioResult, cfg: Settings) -> float:
    return cfg.next_day_wait_h if s.window_status == "missed" else 0.0


def _reasons(req: DispatchRequest, s_a: ScenarioResult, s_h: ScenarioResult, price_a: float, price_h: float,
             route_a: RouteOption, route_h: RouteOption, cfg: Settings) -> list[WhyNotReason]:
    bd_a: NetBreakdown = net_breakdown(req, price_a, route_a.distance_km, _wait_h(s_a, cfg))
    bd_h: NetBreakdown = net_breakdown(req, price_h, route_h.distance_km, _wait_h(s_h, cfg))
    out: list[WhyNotReason] = []
    extra_transport: float = bd_h.transport - bd_a.transport
    if extra_transport > EPS_RS:
        out.append(WhyNotReason(kind="transport", amount_rs=extra_transport))
    extra_hold: float = bd_h.holding - bd_a.holding
    if s_h.window_status in ("missed", "unknown"):
        amount: float | None = extra_hold if s_h.window_status == "missed" and extra_hold > EPS_RS else None
        out.append(WhyNotReason(kind="window", amount_rs=amount, window_status=s_h.window_status))
    elif extra_hold > EPS_RS:
        out.append(WhyNotReason(kind="hold", amount_rs=extra_hold))
    price_gap: float = (bd_a.gross - bd_a.percent_deduction) - (bd_h.gross - bd_h.percent_deduction)
    if price_gap > EPS_RS:
        out.append(WhyNotReason(kind="price", amount_rs=price_gap))
    return out


def why_not_highest(req: DispatchRequest, cands: list[CandidateInputs], plan_set: PlanSet, cfg: Settings) -> WhyNot:
    plan_a = next((p for p in plan_set.plans if p.label == "A"), None)
    if plan_a is None:
        return WhyNot(status="no_plan")
    opt_a: PlanOption = plan_a.option
    priced: list[CandidateInputs] = [c for c in cands if usable_price(c.price)]
    cand_a: CandidateInputs | None = next((c for c in priced if c.mandi_id == opt_a.mandi_id), None)
    route_a: RouteOption | None = None
    if cand_a is not None:
        route_a = next((r for r in cand_a.routes if r.route_id == opt_a.route_id), None)
    if cand_a is None or route_a is None:
        return WhyNot(status="no_plan", a_mandi_name=opt_a.mandi_name)
    typ_a: float = cand_a.price.typical   # type: ignore[union-attr]
    base = {"a_mandi_name": opt_a.mandi_name, "a_typical_price": typ_a, "a_distance_km": opt_a.distance_km}

    others: list[CandidateInputs] = sorted(
        (c for c in priced if c.mandi_id != opt_a.mandi_id and any(r.geometry_ok for r in c.routes)),
        key=lambda c: (-c.price.typical, c.mandi_id))   # type: ignore[union-attr]
    if not others:
        return WhyNot(status="no_alternative", **base)
    cand_h: CandidateInputs = others[0]
    typ_h: float = cand_h.price.typical   # type: ignore[union-attr]
    if typ_h <= typ_a:
        return WhyNot(status="a_is_highest", **base)

    depart: datetime = datetime.fromisoformat(opt_a.depart_iso)
    opt_h: PlanOption = sort_options([evaluate_option(req, cand_h, r, depart, cfg)
                                      for r in cand_h.routes if r.geometry_ok])[0]
    route_h: RouteOption = next(r for r in cand_h.routes if r.route_id == opt_h.route_id)

    rows: list[WhyNotScenario] = []
    for s_a, s_h in zip(opt_a.scenario_results, opt_h.scenario_results):
        gap: float = s_h.net_value - s_a.net_value
        loses: bool = gap < -EPS_RS
        reasons: list[WhyNotReason] = []
        if loses:
            price_a: float = float(getattr(cand_a.price, s_a.price_level))
            price_h: float = float(getattr(cand_h.price, s_h.price_level))
            reasons = _reasons(req, s_a, s_h, price_a, price_h, route_a, route_h, cfg)
        rows.append(WhyNotScenario(price_level=s_a.price_level, delay_level=s_a.delay_level, net_a=s_a.net_value,
                                   net_highest=s_h.net_value, gap_rs=gap, loses=loses, reasons=reasons))
    return WhyNot(status="compared", **base, highest_option_id=opt_h.option_id, highest_mandi_name=opt_h.mandi_name,
                  highest_typical_price=typ_h, highest_distance_km=opt_h.distance_km, scenarios=rows,
                  wins_in=sum(1 for r in rows if r.gap_rs > EPS_RS), loses_in=sum(1 for r in rows if r.loses),
                  of=len(rows))
