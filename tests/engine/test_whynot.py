"""why_not_highest + render_why_not golden tests. All data FIXTURE_."""
from __future__ import annotations

from app.engine import build_plan_set, why_not_highest
from app.explain import allowed_values, check_numbers, render_why_not
from tests.engine.test_plans import (
    FIXTURE_CFG,
    FIXTURE_NOW,
    FIXTURE_cand,
    FIXTURE_costs,
    FIXTURE_request,
)


def _run(cands, req=None):
    req = req or FIXTURE_request()
    ps = build_plan_set(req, cands, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_wn")
    w = why_not_highest(req, cands, ps, FIXTURE_CFG)
    text = render_why_not(w)
    assert check_numbers(text, allowed_values(w, {})) == []
    assert "ripen" not in text.lower()
    return ps, w, text


def test_highest_price_loses_because_of_distance():
    near = FIXTURE_cand("m1", "FIXTURE_Near", 1400, 1500, 1600, distance_km=100.0, closing="23:00")
    far = FIXTURE_cand("m2", "FIXTURE_Far", 1450, 1550, 1650, distance_km=400.0, duration_h=6.0, closing="23:00")
    ps, w, text = _run([near, far])
    assert ps.plans[0].option.mandi_id == "m1"
    assert w.status == "compared" and w.highest_mandi_name == "FIXTURE_Far"
    assert len(w.scenarios) == 9 and w.of == 9 and w.wins_in == 0 and w.loses_in == 9
    for r in w.scenarios:
        assert [x.kind for x in r.reasons] == ["transport"]
        assert abs(r.reasons[0].amount_rs - 20.0 * 300.0) < 1e-6     # 300 km more x Rs 20/km
        assert abs(r.gap_rs - (r.net_highest - r.net_a)) < 1e-9
    assert "longer distance/transport cost" in text and "none of the 9 tested scenarios" in text


def test_highest_price_wins_in_some_scenarios_says_which():
    steady = FIXTURE_cand("m1", "FIXTURE_Steady", 1400, 1500, 1600, closing="23:00")
    volatile = FIXTURE_cand("m2", "FIXTURE_Volatile", 1000, 1600, 2600, distance_km=150.0, closing="23:00")
    ps, w, text = _run([steady, volatile])
    assert ps.plans[0].option.mandi_id == "m1"        # CVaR picks the steady mandi
    assert w.status == "compared" and w.wins_in == 6 and w.loses_in == 3
    assert {r.price_level for r in w.scenarios if r.loses} == {"low"}
    for r in w.scenarios:
        if r.loses:
            assert {x.kind for x in r.reasons} == {"transport", "price"}
    assert "beats A in 6 of 9 tested scenarios" in text and "typical price, no delay" in text
    assert "loses to A in 3 of 9" in text and "low price, severe delay" in text


def test_window_missed_is_a_reason():
    a = FIXTURE_cand("m1", "FIXTURE_A", 1400, 1500, 1600, closing="23:00")
    late = FIXTURE_cand("m2", "FIXTURE_Late", 1450, 1550, 1650, closing="06:00")   # 05:00 + 2 h -> missed
    _, w, text = _run([a, late], FIXTURE_request(costs=FIXTURE_costs(hold_cost_per_hour=300.0)))
    assert w.status == "compared"
    kinds = {x.kind for r in w.scenarios if r.loses for x in r.reasons}
    assert "window" in kinds and "window missed or unknown (closing time missed)" in text


def test_a_is_highest_says_so():
    a = FIXTURE_cand("m1", "FIXTURE_Top", 1400, 1500, 1600, closing="23:00")
    b = FIXTURE_cand("m2", "FIXTURE_Low", 1300, 1400, 1500, distance_km=60.0, closing="23:00")
    _, w, text = _run([a, b])
    assert w.status == "a_is_highest" and w.scenarios == []
    assert "already has the highest typical price" in text and "1,500" in text


def test_no_plan_and_no_alternative():
    req = FIXTURE_request()
    only = [FIXTURE_cand("m1", "FIXTURE_Only", 1400, 1500, 1600)]
    ps = build_plan_set(req, only, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="FIXTURE_wn1")
    assert why_not_highest(req, only, ps, FIXTURE_CFG).status == "no_alternative"
    empty = ps.model_copy(update={"plans": []})
    assert render_why_not(why_not_highest(req, only, empty, FIXTURE_CFG)) == "No Plan A to compare with."
