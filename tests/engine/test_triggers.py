"""B9 golden tests - triggers, hysteresis, recompute, diff (BLUEPRINT B9, PLAN 7.9)."""
from __future__ import annotations

from datetime import timedelta

from app.contracts import CandidateInputs, DispatchProgress, Evidence, Kind, LatLon, PriceBand, RouteOption, Waypoint
from app.engine import WatchState, build_plan_set, evaluate_triggers_and_recompute
from tests.engine.test_plans import FIXTURE_CFG, FIXTURE_NOW, FIXTURE_costs, FIXTURE_request

FIXTURE_REQ = FIXTURE_request(costs=FIXTURE_costs(hold_cost_per_hour=300.0))


def FIXTURE_cand(mid: str, typical: float, closing: str, record_date: str = "2026-10-31") -> CandidateInputs:
    wps = [
        Waypoint(idx=0, point=LatLon(lat=13.0, lon=77.5), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=13.2, lon=77.7), elapsed_nominal_s=3600.0, to_dest_s={"m2": 3600.0}),
        Waypoint(idx=2, point=LatLon(lat=13.4, lon=77.9), elapsed_nominal_s=7200.0),
    ]
    route = RouteOption(route_id=f"{mid}~r0", distance_km=100.0, duration_h_nominal=2.0, waypoints=wps,
                        evidence_ids=["ev_route"])
    return CandidateInputs(
        mandi_id=mid, mandi_name=f"FIXTURE_{mid}", location=LatLon(lat=13.4, lon=77.9), location_precision="market",
        price=PriceBand(low=typical - 100, typical=typical, high=typical + 100, record_date=record_date,
                        unit_verified=True, evidence_ids=[f"ev_p_{mid}"]),
        routes=[route], latest_arrival_local=closing)


FIXTURE_CANDS = [FIXTURE_cand("m1", 1500, "08:30"), FIXTURE_cand("m2", 1480, "20:00")]
FIXTURE_PREV = build_plan_set(FIXTURE_REQ, FIXTURE_CANDS, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p1")


def _prog(delay_min: float = 0.0, wp: int | None = None) -> DispatchProgress:
    return DispatchProgress(plan_id="p1", dispatched_at=FIXTURE_NOW.isoformat(), last_waypoint_idx=wp,
                            reported_delay_min=delay_min, source="checkin_button")


def _tick(state: WatchState, delay_min: float = 90.0, cands=None, now=FIXTURE_NOW, wp=None, ev=None):
    return evaluate_triggers_and_recompute(FIXTURE_PREV, FIXTURE_REQ, FIXTURE_CANDS if cands is None else cands,
                                           ev or [], _prog(delay_min, wp), state, now, FIXTURE_CFG)


def test_prev_plan_a_is_m1():
    assert FIXTURE_PREV.plans[0].option.mandi_id == "m1"


def test_single_spike_does_not_replan():
    d = _tick(WatchState())
    assert "T2" in d.fired and d.persisted == [] and not d.propose_replan and d.diff is None


def test_n_consecutive_ticks_replan():
    d1 = _tick(WatchState())
    d2 = _tick(d1.new_state, now=FIXTURE_NOW + timedelta(minutes=5))
    assert "T2" in d2.persisted and d2.propose_replan
    assert d2.new_plan_set is not None and d2.new_plan_set.version == 2
    new_a = d2.new_plan_set.plans[0].option
    assert new_a.mandi_id == "m2" and new_a.option_id == f"m2::{new_a.route_id}::{new_a.depart_iso}"
    assert d2.diff is not None and d2.diff.recommendation_changed and d2.diff.primary_reason.startswith("T2")
    terms = {i.term for i in d2.diff.items}
    assert "destination" in terms and "score_rs" in terms and not any("ripen" in t for t in terms)
    assert all(i.old != i.new for i in d2.diff.items)
    assert d2.new_state.cooldown_until is not None and "T2" not in d2.new_state.trigger_counts


def test_clear_resets_count():
    d1 = _tick(WatchState())
    d2 = _tick(d1.new_state, delay_min=0.0)
    assert d2.new_state.trigger_counts == {}
    d3 = _tick(d2.new_state)
    assert not d3.persisted and not d3.propose_replan


def test_cooldown_suppresses():
    st = WatchState(trigger_counts={"T2": 5}, cooldown_until=(FIXTURE_NOW + timedelta(minutes=10)).isoformat())
    d = _tick(st)
    assert d.persisted and not d.propose_replan and "cooldown" in d.reason and d.new_plan_set is None
    d_after = _tick(st, now=FIXTURE_NOW + timedelta(minutes=11))
    assert d_after.propose_replan


def test_fired_but_no_better_option():
    cands = [FIXTURE_cand("m1", 1500, "08:30"), FIXTURE_cand("m2", 1300, "20:00")]
    d = _tick(WatchState(trigger_counts={"T2": 1}), cands=cands)
    assert "T2" in d.persisted and not d.propose_replan and "no better option" in d.reason


def test_t5_fires_once_per_record():
    flip = FIXTURE_PREV.plans[0].flip.price_flip_per_qtl
    assert flip is not None
    cands = [FIXTURE_cand("m1", flip - 10, "20:00", record_date="2026-11-01"), FIXTURE_cand("m2", 1480, "20:00")]
    d1 = _tick(WatchState(last_price_record_date="2026-10-31"), delay_min=0.0, cands=cands)
    assert "T5" in d1.fired and "T5" in d1.persisted and d1.new_state.last_price_record_date == "2026-11-01"
    d2 = _tick(d1.new_state.model_copy(update={"cooldown_until": None}), delay_min=0.0, cands=cands)
    assert "T5" not in d2.fired


def test_recompute_is_deterministic():
    st = WatchState(trigger_counts={"T2": 1})
    a, b = _tick(st), _tick(st)
    assert a.diff is not None and a.diff.model_dump() == b.diff.model_dump()
    assert a.new_plan_set.model_dump() == b.new_plan_set.model_dump()


def test_missing_fresh_data_degrades():
    d = _tick(WatchState(trigger_counts={"T2": 3}), cands=[])
    assert not d.propose_replan and "missing" in d.reason
    d2 = _tick(WatchState(), cands=[FIXTURE_cand("m2", 1480, "20:00")])
    assert not d2.propose_replan


def test_state_not_mutated():
    st = WatchState(trigger_counts={"T2": 1})
    before = st.model_dump()
    d = _tick(st)
    assert st.model_dump() == before and d.new_state is not st
    assert FIXTURE_PREV.version == 1


def test_dispatched_recomputes_remaining_route_only():
    now = FIXTURE_NOW + timedelta(hours=1)
    st = WatchState(trigger_counts={"T2": 1})
    d = _tick(st, now=now, wp=1)
    assert d.propose_replan and d.new_plan_set is not None
    labels = [p.label for p in d.new_plan_set.plans]
    assert "C" not in labels
    new_a = d.new_plan_set.plans[0].option
    assert new_a.mandi_id == "m2" and new_a.distance_km < 100.0 and "::" not in new_a.route_id


def test_river_flow_is_advisory_and_t4_disabled():
    ev = [Evidence(id="ev_00000001", kind=Kind.PROXY,
                   label="river flow proxy percentile", value=99.0, source="FIXTURE", fetched_at=FIXTURE_NOW.isoformat())]
    d = _tick(WatchState(), delay_min=0.0, ev=ev)
    assert "advisory" in d.reason and "T6" not in d.fired and "T4" not in d.fired and not d.propose_replan


def test_cr6_rain_trigger_delta_from_settings():
    """CR-6: T3 threshold comes from cfg.rain_trigger_delta_mm (default 5.0), not a module constant."""
    from app.config import Settings
    from tests.engine.test_plans import FIXTURE_with_weather

    assert Settings().rain_trigger_delta_mm == 5.0
    base = [FIXTURE_with_weather(FIXTURE_cand("m1", 1500, "20:00")), FIXTURE_cand("m2", 1480, "20:00")]
    prev = build_plan_set(FIXTURE_REQ, base, [], FIXTURE_NOW, FIXTURE_CFG, plan_id="p1")
    wet_route = base[0].routes[0]
    wet = base[0].model_copy(update={"routes": [wet_route.model_copy(update={
        "weather": [w.model_copy(update={"precip_mm": 4.0}) for w in wet_route.weather]})]})
    fresh = [wet, base[1]]   # rain on A's route rises from 2.0 to 4.0 mm per point
    rise = sum(4.0 - 2.0 for _ in wet_route.weather)
    for delta, expect in ((rise - 0.5, True), (rise + 0.5, False)):
        cfg = FIXTURE_CFG.model_copy(update={"rain_trigger_delta_mm": delta})
        d = evaluate_triggers_and_recompute(prev, FIXTURE_REQ, fresh, [], _prog(0.0), WatchState(), FIXTURE_NOW, cfg)
        assert ("T3" in d.fired) is expect
