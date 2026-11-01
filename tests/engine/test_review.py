"""Adversarial-review regression tests for B4-B9 + B9b. All data FIXTURE_."""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.contracts import CandidateInputs, PriceBand
from app.engine import WatchState, build_plan_set, evaluate_option
from app.explain import allowed_values, check_numbers, render_diff, render_plan_summary
from tests.engine.test_plans import (
    FIXTURE_CFG,
    FIXTURE_NOW,
    FIXTURE_cand,
    FIXTURE_costs,
    FIXTURE_request,
    FIXTURE_route,
)
from tests.engine.test_triggers import FIXTURE_NOW as TRIG_NOW
from tests.engine.test_triggers import _tick

FIXTURE_IST_OFFSET = "+05:30"


def _ids_ok(ps) -> None:
    for p in ps.plans:
        o = p.option
        assert o.option_id == f"{o.mandi_id}::{o.route_id}::{o.depart_iso}"


def _text_ok(ps) -> str:
    text = render_plan_summary(ps, {})
    assert check_numbers(text, allowed_values(ps, {})) == []
    assert "ripen" not in text.lower()
    return text


# ---------------------------------------------------------------- (d)(e) IST normalisation
def test_utc_departure_is_shown_in_ist_and_option_id_is_instant_stable():
    """BUG: a UTC departure leaked UTC clock times into depart_iso/arrival_iso and changed option_id."""
    utc_depart = FIXTURE_NOW.astimezone(UTC)
    cand = FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600)
    opt_utc = evaluate_option(FIXTURE_request(preferred_departure=utc_depart), cand, cand.routes[0], utc_depart,
                              FIXTURE_CFG)
    opt_ist = evaluate_option(FIXTURE_request(), cand, cand.routes[0], FIXTURE_NOW, FIXTURE_CFG)
    assert opt_utc.depart_iso.endswith(FIXTURE_IST_OFFSET)
    assert all(s.arrival_iso.endswith(FIXTURE_IST_OFFSET) for s in opt_utc.scenario_results)
    assert opt_utc.option_id == opt_ist.option_id


def test_plan_at_2330_closing_before_departure_no_crash():
    late = FIXTURE_NOW.replace(hour=23, minute=30)
    req = FIXTURE_request(preferred_departure=late, loading_time=late)
    cands = [FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600, closing="14:00"),
             FIXTURE_cand("m2", "FIXTURE_M2", 1380, 1480, 1580, distance_km=60.0, duration_h=1.2, closing="06:00")]
    ps = build_plan_set(req, cands, [], late, FIXTURE_CFG, plan_id="FIXTURE_late")
    assert ps.plans
    _ids_ok(ps)
    plan_c = next(p for p in ps.plans if p.label == "C")
    assert datetime.fromisoformat(plan_c.option.depart_iso).date() > late.date()   # after midnight
    _text_ok(ps)


# ---------------------------------------------------------------- (b) None / degenerate inputs
def test_zero_price_in_band_does_not_crash_and_is_not_used():
    """BUG: low=0 in a price band raised ValueError inside build_plan_set."""
    bad = FIXTURE_cand("m1", "FIXTURE_M1", 0, 1500, 1600)
    good = FIXTURE_cand("m2", "FIXTURE_M2", 1380, 1480, 1580)
    ps = build_plan_set(FIXTURE_request(), [bad, good], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_z")
    assert [p.option.mandi_id for p in ps.plans] == ["m2", "m2"]  # A and C; no B (only one usable mandi)
    only_bad = build_plan_set(FIXTURE_request(), [bad], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_z2")
    assert only_bad.plans == [] and only_bad.coverage.grade == "ABSTAIN" and only_bad.coverage.reasons


def test_flat_band_missing_weather_closing_deductions():
    costs = FIXTURE_costs(commission_pct=None, market_fee_pct=None, hamali_per_qtl=None, hold_cost_per_hour=None)
    req = FIXTURE_request(costs=costs)
    cands = [FIXTURE_cand("m1", "FIXTURE_M1", 1500, 1500, 1500, closing=None),
             FIXTURE_cand("m2", "FIXTURE_M2", 1480, 1480, 1480, distance_km=60.0, duration_h=1.2, closing=None)]
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_flat")
    assert ps.plans and ps.coverage.grade != "ABSTAIN"
    assert all(s.window_status == "unknown" for p in ps.plans for s in p.option.scenario_results)
    assert not ps.plans[0].commit.computed
    _ids_ok(ps)
    _text_ok(ps)


def test_empty_routes_abstain_with_plain_reasons():
    cand = FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600).model_copy(update={"routes": []})
    ps = build_plan_set(FIXTURE_request(), [cand], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_e")
    assert ps.plans == [] and ps.coverage.grade == "ABSTAIN" and ps.coverage.reasons
    assert "No recommendation" in _text_ok(ps)


def test_bad_geometry_only_abstains():
    cand = FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600)
    cand = cand.model_copy(update={"routes": [FIXTURE_route("m1~r0").model_copy(update={"geometry_ok": False})]})
    ps = build_plan_set(FIXTURE_request(), [cand], [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_g")
    assert ps.plans == [] and ps.coverage.grade == "ABSTAIN" and ps.coverage.reasons


# ---------------------------------------------------------------- (a) determinism
def test_same_inputs_any_order_identical_planset():
    cands: list[CandidateInputs] = [
        FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600),
        FIXTURE_cand("m2", "FIXTURE_M2", 1400, 1500, 1600),            # exact tie with m1
        FIXTURE_cand("m3", "FIXTURE_M3", 1380, 1480, 1580, distance_km=60.0, duration_h=1.2),
    ]
    ref = build_plan_set(FIXTURE_request(), cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_d").model_dump()
    rng = random.Random(7)
    for _ in range(5):
        shuffled = cands[:]
        rng.shuffle(shuffled)
        got = build_plan_set(FIXTURE_request(), shuffled, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_d")
        assert got.model_dump() == ref


# ---------------------------------------------------------------- (f) no ripening
def test_no_ripening_anywhere_when_disabled():
    assert FIXTURE_CFG.ripening_enabled is False
    ps = build_plan_set(FIXTURE_request(), [FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600),
                                            FIXTURE_cand("m2", "FIXTURE_M2", 1380, 1480, 1580)],
                        [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_r")
    assert all(p.option.ripening is None for p in ps.plans)
    assert all(c.kind != "ripening_pct" for p in ps.plans for c in p.reconsider_if)
    assert all(i.dimension != "ripening" for i in ps.coverage.items)


# ---------------------------------------------------------------- (g)(h) triggers
def test_tick_never_mutates_state_and_diff_numbers_traceable():
    state = WatchState(trigger_counts={"T1": 1})
    before = state.model_dump()
    d1 = _tick(state)
    assert state.model_dump() == before and d1.new_state is not state
    assert d1.new_state.trigger_counts is not state.trigger_counts
    d2 = _tick(d1.new_state, now=TRIG_NOW + timedelta(minutes=5))
    assert d2.diff is not None and d2.new_plan_set is not None
    _ids_ok(d2.new_plan_set)
    text = render_diff(d2.diff, {})
    assert check_numbers(text, allowed_values(d2.diff, {})) == []
    assert "ripen" not in text.lower()


def test_price_band_helper_accepts_only_positive():
    from app.engine.coverage import usable_price
    ok = PriceBand(low=1, typical=2, high=3, record_date="2026-10-31")
    assert usable_price(ok)
    assert not usable_price(ok.model_copy(update={"low": 0.0}))
    assert not usable_price(None)
