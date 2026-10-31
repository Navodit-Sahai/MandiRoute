"""
B6 - Commitment points (PLAN 7.5). APPROXIMATE by design.

For plan A on route r and alternative B (mandi m_b), waypoint i, scenario s with delay d_s:
    eta_B(i, s) = depart + (elapsed_i + to_dest_i[m_b]) x (1 + d_s)
    feasible     iff B's latest arrival is known and window_status(eta_B) == "met"
    last_feasible_waypoint[s] = max{ i : feasible(i, s) }  (None = never)

Closing rule = B4's `window_status`: the ARRIVAL date's closing time (local clock time of
eta_B <= "HH:MM"), so B4, B6 and B7 always agree on "met". Known simplification shared with
B4: an arrival after midnight is compared with that new day's closing time.
"""
from __future__ import annotations

from datetime import datetime

from app.contracts import CandidateInputs, CommitInfo, DispatchRequest, PlanOption, RouteOption
from app.engine.units import delayed_arrival, window_status

NOTE = "approximate (<= 8 sampled waypoints; assumes the driver can divert at that waypoint)"


def last_feasible_waypoint(depart: datetime, route: RouteOption, mandi_id: str,
                           closing_hhmm: str, delay: float, tz_name: str) -> int | None:
    """Highest waypoint index from which mandi_id is still reached before closing (None = never)."""
    best: int | None = None
    for wp in route.waypoints:
        to_dest = wp.to_dest_s.get(mandi_id)
        if to_dest is None:
            continue
        eta = delayed_arrival(depart, wp.elapsed_nominal_s + to_dest, delay)
        if window_status(eta, closing_hhmm, tz_name) == "met":
            best = wp.idx if best is None else max(best, wp.idx)
    return best


def compute_commit_points(req: DispatchRequest, plan_a: PlanOption, route_a: RouteOption, alt: PlanOption,
                          alt_cand: CandidateInputs, cfg) -> CommitInfo:
    closing = alt_cand.latest_arrival_local or req.mandi_latest_arrival.get(alt.mandi_id)
    if closing is None:
        return CommitInfo(computed=False, note=f"{NOTE}; not computed: {alt.mandi_name} closing time unknown")
    depart = datetime.fromisoformat(plan_a.depart_iso)
    points = {name: last_feasible_waypoint(depart, route_a, alt.mandi_id, closing, delay, cfg.timezone_name)
              for name, delay in cfg.delay_mult.items()}
    return CommitInfo(computed=True, last_feasible_waypoint=points, note=NOTE)
