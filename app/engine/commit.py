"""
B6 - Commitment points (PLAN 7.5). APPROXIMATE by design.

For plan A on route r and alternative B (mandi m_b), waypoint i, scenario s with delay d_s:
    eta_B(i, s) = depart + (elapsed_i + to_dest_i[m_b]) x (1 + d_s)
    feasible     iff B's latest arrival is known and eta_B <= deadline_B
    last_feasible_waypoint[s] = max{ i : feasible(i, s) }  (None = never)

Deadline: B's "HH:MM" on the local date of departure; if that is earlier than departure,
the next day. Using one absolute deadline keeps the result monotone in delay (a pure
clock-time comparison would wrap at midnight).
"""
from __future__ import annotations

from datetime import datetime, timedelta, time

from app.contracts import CandidateInputs, CommitInfo, DispatchRequest, PlanOption, RouteOption
from app.engine.units import delayed_arrival, parse_hhmm, to_local

NOTE = "approximate (<= 8 sampled waypoints; assumes the driver can divert at that waypoint)"


def closing_deadline(depart: datetime, latest_hhmm: str, tz_name: str) -> datetime:
    """Absolute local deadline for an "HH:MM" closing time, on or after departure."""
    hour, minute = parse_hhmm(latest_hhmm)
    local_depart = to_local(depart, tz_name)
    deadline = datetime.combine(local_depart.date(), time(hour, minute), tzinfo=local_depart.tzinfo)
    if deadline < local_depart:
        deadline += timedelta(days=1)
    return deadline


def last_feasible_waypoint(depart: datetime, route: RouteOption, mandi_id: str,
                           deadline: datetime, delay: float) -> int | None:
    """Highest waypoint index from which mandi_id is still reachable by deadline (None = never)."""
    best: int | None = None
    for wp in route.waypoints:
        to_dest = wp.to_dest_s.get(mandi_id)
        if to_dest is None:
            continue
        if delayed_arrival(depart, wp.elapsed_nominal_s + to_dest, delay) <= deadline:
            best = wp.idx if best is None else max(best, wp.idx)
    return best


def compute_commit_points(req: DispatchRequest, plan_a: PlanOption, route_a: RouteOption, alt: PlanOption,
                          alt_cand: CandidateInputs, cfg) -> CommitInfo:
    closing = alt_cand.latest_arrival_local or req.mandi_latest_arrival.get(alt.mandi_id)
    if closing is None:
        return CommitInfo(computed=False, note=f"{NOTE}; not computed: {alt.mandi_name} closing time unknown")
    depart = datetime.fromisoformat(plan_a.depart_iso)
    deadline = closing_deadline(depart, closing, cfg.timezone_name)
    points = {name: last_feasible_waypoint(depart, route_a, alt.mandi_id, deadline, delay)
              for name, delay in cfg.delay_mult.items()}
    return CommitInfo(computed=True, last_feasible_waypoint=points, note=NOTE)
