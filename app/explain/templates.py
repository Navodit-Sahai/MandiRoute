"""B9b - deterministic template explainer + number check (BLUEPRINT B9b, AGENT 7).

Template text only (no LLM). Every number printed comes from the PlanSet / PlanDiff / Evidence values
(or engine-generated text inside them). Dates, clock times and waypoint/version labels are not "numbers".
Ripening text is never rendered while settings.ripening_enabled is False (SR-1).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.contracts.evidence import Evidence
from app.contracts.plans import Plan, PlanDiff, PlanOption, PlanSet, ScenarioResult
from app.engine.whynot import WhyNot, WhyNotReason, WhyNotScenario

FORBIDDEN_PHRASES: tuple[str, ...] = (
    "real-time prices", "predicted price range", "road flooded", "spoilage %", "quality loss",
    "confidence 87%", "guaranteed", "ai-optimised profit", "unique / no competitor",
)

WINDOW_TEXT: dict[str, str] = {
    "met": "closing time met",
    "missed": "closing time missed",
    "unknown": "Closing time unknown — window not checked",
}

SCENARIO_TEXT: dict[str, str] = {"none": "no delay", "moderate": "moderate delay", "severe": "severe delay"}

CONDITION_TEXT: dict[str, str] = {
    "eta_slip_h": "arrival slips by {op} {v} h",
    "price_drop_pct": "typical price at A drops by {op} {v}%",
    "rain_mm_remaining": "rain on the remaining route {op} {v} mm",
    "window_miss_scenarios": "A misses the closing time in {op} {v} tested scenarios",
    "ripening_pct": "ripening window consumed {op} {v}%",
}

# Dates/times are labels, removed before number extraction.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?")
_TIME_RE = re.compile(r"(?<![\w.])\d{1,2}:\d{2}(?![\w])")
# A number token: not glued to a word/id (m1, ev_ab12, v2, T3 are labels, not numbers).
_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


# ---------------------------------------------------------------- formatting
def _money(v: float) -> str:
    return f"Rs {v:,.0f}"


def _num(v: float, nd: int = 1) -> str:
    return f"{v:,.{nd}f}".rstrip("0").rstrip(".") if nd else f"{v:,.0f}"


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _nominal(opt: PlanOption) -> ScenarioResult | None:
    return next((s for s in opt.scenario_results if s.price_level == "typical" and s.delay_level == "none"), None)


def _value(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return "n/a" if v is None else str(v)
    if isinstance(v, (int, float)):
        return _num(float(v), 2)
    return _when(v) if _DATE_RE.fullmatch(v) else v


def _evidence_lines(ids: Iterable[str], evidence_index: dict[str, Evidence]) -> list[str]:
    out: list[str] = []
    for eid in dict.fromkeys(ids):
        ev: Evidence | None = evidence_index.get(eid)
        if ev is None:
            continue
        val: str = _value(ev.value) + (f" {ev.unit}" if ev.unit else "")
        date: str = f" (record date: {ev.as_of})" if ev.as_of else ""
        stale: str = " - stale" if ev.stale else ""
        out.append(f"  - {ev.label}: {val} [{ev.kind.value}]{date}{stale} <{ev.id}>")
    return out


# ---------------------------------------------------------------- plan summary
def _plan_lines(plan: Plan, ripening_on: bool) -> list[str]:
    opt: PlanOption = plan.option
    lines: list[str] = [f"Plan {plan.label}: {opt.mandi_name} - depart {_when(opt.depart_iso)}"]
    lines.append(f"  Distance {_num(opt.distance_km)} km; Estimated travel time (OSRM, no traffic) "
                 f"{_num(opt.duration_h_nominal, 2)} h [computed]")
    lines.append(f"  Risk-adjusted value {_money(opt.score)}; average of tested scenarios {_money(opt.mean_net)} [computed]")
    nom: ScenarioResult | None = _nominal(opt)
    if nom is not None:
        lines.append(f"  Typical price, no delay: net {_money(nom.net_value)}, arrival {_when(nom.arrival_iso)}, "
                     f"{WINDOW_TEXT[nom.window_status]} [computed]")
    if any(s.window_status == "unknown" for s in opt.scenario_results) and (nom is None or nom.window_status != "unknown"):
        lines.append(f"  {WINDOW_TEXT['unknown']}")
    if opt.rain_exposure_mm is None:
        lines.append("  Rain along the route: not available [forecast]")
    else:
        lines.append(f"  Rain along the route: {_num(opt.rain_exposure_mm)} mm [forecast]")
    if plan.label == "A":
        if plan.flip.delay_flip_pct is not None:
            lines.append(f"  Assumed delay stress (adjustable): B beats A from {_num(plan.flip.delay_flip_pct)}% "
                         f"of normal travel time [computed]")
        else:
            lines.append("  Delay flip: no flip within tested range")
        if plan.flip.price_flip_per_qtl is not None:
            lines.append(f"  Price flip: below {_money(plan.flip.price_flip_per_qtl)}/qtl typical price at A, "
                         f"B is better [computed]")
        else:
            lines.append("  Price flip: no flip within tested range")
        if plan.commit.computed:
            for scen, wp in plan.commit.last_feasible_waypoint.items():
                where: str = f"waypoint #{wp}" if wp is not None else "none (diversion not feasible)"
                lines.append(f"  Last feasible diversion waypoint under {SCENARIO_TEXT.get(scen, scen)} (approximate): {where}")
        elif plan.commit.note:
            lines.append(f"  Diversion point not computed: {plan.commit.note}")
        conds = [c for c in plan.reconsider_if if ripening_on or c.kind != "ripening_pct"]
        if conds:
            lines.append("  Reconsider this plan if:")
            for c in conds:
                lines.append("    - " + CONDITION_TEXT[c.kind].format(op=c.comparison, v=_num(c.threshold, 2)))
    return lines


def render_plan_summary(ps: PlanSet, evidence_index: dict[str, Evidence]) -> str:
    ripening_on: bool = get_settings().ripening_enabled
    cov = ps.coverage
    lines: list[str] = [f"Plan set {ps.plan_id} v{ps.version} - data coverage: {cov.grade}"]
    if not ps.plans or cov.grade == "ABSTAIN":
        lines.append("No recommendation: the available data is not enough to compare mandis.")
        lines.append("Reasons:")
        lines.extend(f"  - {r}" for r in cov.reasons)
        lines.append("Fix the data gaps above and plan again.")
        return "\n".join(lines)
    lines.append(f"A is best in {ps.robustness_a_best_in} of {ps.robustness_of} tested scenarios")
    for plan in ps.plans:
        lines.extend(_plan_lines(plan, ripening_on))
    if cov.reasons:
        lines.append("Caveats:")
        lines.extend(f"  - {r}" for r in cov.reasons)
    ev_ids: list[str] = list(ps.evidence_ids) + [e for p in ps.plans for e in p.option.evidence_ids]
    ev_lines: list[str] = _evidence_lines(ev_ids, evidence_index)
    if ev_lines:
        lines.append("Evidence:")
        lines.extend(ev_lines)
    return "\n".join(lines)


# ---------------------------------------------------------------- diff
def render_diff(diff: PlanDiff, evidence_index: dict[str, Evidence]) -> str:
    ripening_on: bool = get_settings().ripening_enabled
    head: str = "Recommendation changed" if diff.recommendation_changed else "Recommendation unchanged"
    lines: list[str] = [f"Replan v{diff.old_version} -> v{diff.new_version}: {head}",
                        f"Main reason: {diff.primary_reason}"]
    for it in diff.items:
        if not ripening_on and "ripening" in it.term:
            continue
        delta: str = f" (change {'+' if it.delta > 0 else ''}{_num(it.delta, 2)})" if it.delta is not None else ""
        effect: str = f" - {it.effect}" if not re.search(r"\d", it.effect) else ""
        lines.append(f"  - {it.term}: {_value(it.old)} -> {_value(it.new)}{delta}{effect}")
    lines.append("Approval required before anything changes.")
    ev_lines: list[str] = _evidence_lines(evidence_index.keys(), evidence_index)
    if ev_lines:
        lines.append("Evidence:")
        lines.extend(ev_lines)
    return "\n".join(lines)


# ---------------------------------------------------------------- why not the highest price
PRICE_TEXT: dict[str, str] = {"low": "low price", "typical": "typical price", "high": "high price"}


def _scen_label(r: WhyNotScenario) -> str:
    return f"{PRICE_TEXT[r.price_level]}, {SCENARIO_TEXT[r.delay_level]}"


def _reason_text(r: WhyNotReason, w: WhyNot) -> str:
    if r.kind == "transport":
        return (f"longer distance/transport cost: Rs {_num(r.amount_rs or 0.0, 0)} more "
                f"({_num(w.highest_distance_km or 0.0)} km vs {_num(w.a_distance_km or 0.0)} km) [computed]")
    if r.kind == "window":
        head: str = "closing time missed" if r.window_status == "missed" else WINDOW_TEXT["unknown"]
        extra: str = f": hold cost Rs {_num(r.amount_rs, 0)} more" if r.amount_rs is not None else ""
        return f"window missed or unknown ({head}){extra} [computed]"
    if r.kind == "hold":
        return f"hold cost: Rs {_num(r.amount_rs or 0.0, 0)} more [computed]"
    return f"lower price at this price level: Rs {_num(r.amount_rs or 0.0, 0)} less after deductions [computed]"


def render_why_not(w: WhyNot) -> str:
    """Template text for WhyNot; every number is a field of w (verify with check_numbers(text, allowed_values(w, {})))."""
    if w.status == "no_plan":
        return "No Plan A to compare with."
    if w.status == "no_alternative":
        return f"No other priced mandi with a usable route to compare with {w.a_mandi_name}."
    if w.status == "a_is_highest":
        return (f"Plan A ({w.a_mandi_name}) already has the highest typical price "
                f"({_money(w.a_typical_price or 0.0)}/qtl, Price reported today) of the compared mandis.")
    lines: list[str] = [
        (f"Why not {w.highest_mandi_name}? Highest typical price {_money(w.highest_typical_price or 0.0)}/qtl vs "
         f"{_money(w.a_typical_price or 0.0)}/qtl at Plan A ({w.a_mandi_name}) [observed]"),
    ]
    wins: list[str] = [_scen_label(r) for r in w.scenarios if r.gap_rs > 0.5]
    if wins:
        lines.append(f"{w.highest_mandi_name} beats A in {w.wins_in} of {w.of} tested scenarios: {'; '.join(wins)}")
    else:
        lines.append(f"{w.highest_mandi_name} beats A in none of the {w.of} tested scenarios")
    lines.append(f"It loses to A in {w.loses_in} of {w.of} tested scenarios" + (":" if w.loses_in else ""))
    for r in w.scenarios:
        if not r.loses:
            continue
        lines.append(f"  - {_scen_label(r)}: net {_money(r.net_highest)} vs {_money(r.net_a)} at A "
                     f"({_money(abs(r.gap_rs))} less) [computed]")
        lines.extend(f"      {_reason_text(x, w)}" for x in r.reasons)
    return "\n".join(lines)


# ---------------------------------------------------------------- number check
def _walk(obj: Any) -> Iterable[float]:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, str):
        yield from (float(t.replace(",", "")) for t in _NUM_RE.findall(_TIME_RE.sub(" ", _DATE_RE.sub(" ", obj))))
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)


def allowed_values(obj: BaseModel, evidence_index: dict[str, Evidence]) -> list[float]:
    """All numbers carried by a PlanSet/PlanDiff (fields and engine text) plus the evidence values/labels."""
    vals: list[float] = list(_walk(obj.model_dump()))
    for ev in evidence_index.values():
        vals.extend(_walk([ev.value, ev.label, ev.note]))
    return vals


def number_tokens(text: str) -> list[str]:
    """Numeric tokens in text; dates, clock times and glued labels (m1, v2, #3 handled by caller) excluded."""
    return _NUM_RE.findall(_TIME_RE.sub(" ", _DATE_RE.sub(" ", text)))


def check_numbers(text: str, allowed_values: list[float], tol: float = 0.01) -> list[str]:
    """Return numeric tokens not traceable to allowed_values.

    A token is accepted if it is a rounding of an allowed value (within half a unit of its last digit)
    or within relative tolerance tol. Waypoint indices written as '#n' are labels and skipped.
    """
    stripped: str = re.sub(r"#\d+", " ", text)
    bad: list[str] = []
    for tok in number_tokens(stripped):
        x: float = float(tok.replace(",", ""))
        decimals: int = len(tok.split(".")[1]) if "." in tok else 0
        half_unit: float = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(x - abs(v)) <= max(half_unit, tol * abs(v)) for v in allowed_values):
            bad.append(tok)
    return bad
