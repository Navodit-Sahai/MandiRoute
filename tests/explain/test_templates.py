"""B9b golden tests - template explainer + number check (BLUEPRINT B9b, AGENT 7). All data FIXTURE_."""
from __future__ import annotations

import inspect
from datetime import timedelta

from app.contracts import CoverageReport, Evidence, Kind, PlanSet
from app.engine import WatchState, build_plan_set
from app.explain import (
    FORBIDDEN_PHRASES,
    allowed_values,
    check_numbers,
    render_diff,
    render_plan_summary,
)
from app.explain import templates as T
from tests.engine.test_plans import (
    FIXTURE_CFG,
    FIXTURE_NOW,
    FIXTURE_cand,
    FIXTURE_request,
)
from tests.engine.test_triggers import FIXTURE_NOW as TRIG_NOW
from tests.engine.test_triggers import _tick

FIXTURE_EV = {
    "ev_p_m1": Evidence(id="ev_p_m1", kind=Kind.OBSERVED, label="Price reported today", value=1500.0,
                        unit="Rs/qtl", source="FIXTURE", as_of="2026-10-31", fetched_at=FIXTURE_NOW.isoformat()),
    "ev_route": Evidence(id="ev_route", kind=Kind.COMPUTED, label="Estimated travel time (OSRM, no traffic)",
                         value=2.0, unit="h", source="FIXTURE", fetched_at=FIXTURE_NOW.isoformat()),
}
FIXTURE_CANDS = [FIXTURE_cand("m1", "FIXTURE_M1", 1400, 1500, 1600, closing="14:00"),
                 FIXTURE_cand("m2", "FIXTURE_M2", 1380, 1480, 1580, distance_km=60.0, duration_h=1.2, closing=None)]
FIXTURE_PS = build_plan_set(FIXTURE_request(), FIXTURE_CANDS, list(FIXTURE_EV.values()), FIXTURE_NOW,
                            FIXTURE_CFG, plan_id="FIXTURE_p1")
FIXTURE_ABSTAIN = PlanSet(plan_id="FIXTURE_p0", version=1, created_at=FIXTURE_NOW.isoformat(), plans=[],
                          robustness_a_best_in=0, robustness_of=0,
                          coverage=CoverageReport(grade="ABSTAIN", items=[],
                                                  reasons=["no candidate mandi has a price record"]))


def _diff():
    d1 = _tick(WatchState())
    d2 = _tick(d1.new_state, now=TRIG_NOW + timedelta(minutes=5))
    assert d2.diff is not None
    return d2.diff


def test_summary_numbers_all_traceable():
    assert FIXTURE_PS.plans
    text = render_plan_summary(FIXTURE_PS, FIXTURE_EV)
    assert check_numbers(text, allowed_values(FIXTURE_PS, FIXTURE_EV)) == []
    assert f"A is best in {FIXTURE_PS.robustness_a_best_in} of {FIXTURE_PS.robustness_of} tested scenarios" in text
    assert "[computed]" in text and "[observed]" in text and "record date: 2026-10-31" in text


def test_diff_numbers_all_traceable():
    diff = _diff()
    text = render_diff(diff, FIXTURE_EV)
    assert check_numbers(text, allowed_values(diff, FIXTURE_EV)) == []
    assert "Recommendation changed" in text and "Approval required" in text


def test_invented_number_flagged():
    text = render_plan_summary(FIXTURE_PS, FIXTURE_EV) + "\nExtra profit Rs 987,654 expected; 3.7 h saved"
    bad = check_numbers(text, allowed_values(FIXTURE_PS, FIXTURE_EV))
    assert "987,654" in bad


def test_check_numbers_ignores_dates_times_and_labels():
    assert check_numbers("record date 2026-10-31, arrive 13:45, mandi m1, v2, waypoint #4", []) == []
    assert check_numbers("Rs 1,500 and 2.25 h", [1500.0, 2.249]) == []
    assert check_numbers("Rs 1,750", [1500.0]) == ["1,750"]


def test_abstain_has_no_plan_numbers():
    text = render_plan_summary(FIXTURE_ABSTAIN, FIXTURE_EV)
    assert "ABSTAIN" in text and "no candidate mandi has a price record" in text
    assert "Rs" not in text and "Plan A" not in text
    assert T.number_tokens(text) == []


def test_closing_unknown_and_flip_wording():
    text = render_plan_summary(FIXTURE_PS, FIXTURE_EV)
    a = FIXTURE_PS.plans[0]
    if a.flip.delay_flip_pct is None or a.flip.price_flip_per_qtl is None:
        assert "no flip within tested range" in text
    if a.commit.computed:
        assert "Last feasible diversion waypoint under" in text and "(approximate)" in text
    if any(s.window_status == "unknown" for p in FIXTURE_PS.plans for s in p.option.scenario_results):
        assert "Closing time unknown — window not checked" in text


def test_no_forbidden_words_and_no_ripening():
    texts = [render_plan_summary(FIXTURE_PS, FIXTURE_EV), render_plan_summary(FIXTURE_ABSTAIN, FIXTURE_EV),
             render_diff(_diff(), FIXTURE_EV)]
    src = inspect.getsource(T).split("FORBIDDEN_PHRASES: tuple", 1)[0] + \
        inspect.getsource(T).split("CONDITION_TEXT", 1)[1].replace('"ripening_pct": "ripening window consumed', "")
    for t in texts + [src]:
        low = t.lower()
        assert not [p for p in FORBIDDEN_PHRASES if p in low]
    for t in texts:
        assert "ripen" not in t.lower()


def test_deterministic():
    assert render_plan_summary(FIXTURE_PS, FIXTURE_EV) == render_plan_summary(FIXTURE_PS, FIXTURE_EV)
    d = _diff()
    assert render_diff(d, FIXTURE_EV) == render_diff(d, FIXTURE_EV)
