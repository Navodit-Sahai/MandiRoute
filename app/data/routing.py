"""Routing via OSRM (BLUEPRINT A5).

* `route` call per (origin, mandi) with alternatives, geometry + steps; waypoints sampled every
  WAYPOINT_SPACING_KM (<= MAX_WAYPOINTS incl. first & last); `elapsed_nominal_s` from cumulative STEP
  durations (proportional-to-distance only if steps are absent -- stated in the evidence note).
* ONE `table` call per route: sources = waypoints, destinations = candidate mandis -> Waypoint.to_dest_s.
* OSRM demo server policy: <= 1 req/s (shared limiter), no uptime guarantee, no traffic => label
  "Estimated travel time (OSRM, no traffic)".
* Fallback server (routing.openstreetmap.de, UNVERIFIED) only on transport failure. If both fail we return a
  failure -- NEVER a straight-line fake route (B treats a missing route as "no candidate").
"""
from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.contracts import Kind, LatLon, RouteOption, Waypoint
from app.data import http
from app.data.geo import haversine_km

log = logging.getLogger("mandiguard.routing")

OSRM_PRIMARY = ("osrm", "https://router.project-osrm.org")
OSRM_FALLBACK = ("osrm_fallback", "https://routing.openstreetmap.de/routed-car")   # UNVERIFIED (from memory)
MAX_ROUTES_PER_MANDI = 3
ROUTE_TIMEOUT_S = 15.0
PRIMARY_COOLDOWN_S = 60.0           # after a transport failure skip the primary for a minute (avoids 16 x timeout)
_down_until: dict[str, float] = {}


def reset_breaker() -> None:
    _down_until.clear()


def _coord(p: LatLon) -> str:
    return f"{p.lon:.4f},{p.lat:.4f}"           # OSRM order is lon,lat


def _osrm_check(data: Any) -> str | None:
    if not isinstance(data, dict):
        return "OSRM: response is not an object"
    return None if data.get("code") == "Ok" else f"OSRM code {data.get('code')}: {data.get('message', '')}".strip()


def _osrm_call(path: str, params: dict[str, Any], cache_key: str) -> http.HttpResult:
    """Primary then fallback. Cache key is shared across servers (same road graph for our purposes)."""
    s = get_settings()
    last: http.HttpResult | None = None
    for name, base in (OSRM_PRIMARY, OSRM_FALLBACK):
        if _down_until.get(name, 0.0) > http._clock():
            continue
        res = http.request_json(name, "GET", base + path, params, ttl=s.cache_ttl_s["routes"],
                                rate_limiter=http.get_limiter(name), cache_key=cache_key,
                                cache_table="route_cache", timeout=ROUTE_TIMEOUT_S, check=_osrm_check)
        last = res
        if res.ok:
            return res
        if res.error and res.error.startswith("rejected: OSRM code"):
            return res                                   # server answered: e.g. NoRoute -> definitive
        if res.http is not None and 400 <= res.http < 500 and res.http != 429:
            return res                                   # our request is bad; another server will not help
        _down_until[name] = http._clock() + PRIMARY_COOLDOWN_S
    return last or http.HttpResult(False, error="all routing servers are in cool-down", fetched_at=http.iso_utc(),
                                   source="osrm")


# ---------------- pure sampling ----------------
def _interp_time(d_m: float, bps: list[tuple[float, float]] | None, total_d: float, total_t: float) -> float:
    if not bps or len(bps) < 2 or bps[-1][0] <= 0 or bps[-1][1] <= 0:
        return total_t * (d_m / total_d) if total_d > 0 else 0.0           # proportional fallback
    sd, st = total_d / bps[-1][0], total_t / bps[-1][1]                  # normalise to route totals
    xs = [b[0] * sd for b in bps]
    i = min(max(bisect.bisect_right(xs, d_m) - 1, 0), len(bps) - 2)
    x0, x1 = xs[i], xs[i + 1]
    t0, t1 = bps[i][1] * st, bps[i + 1][1] * st
    return t0 if x1 <= x0 else t0 + (t1 - t0) * (min(max(d_m, x0), x1) - x0) / (x1 - x0)


def sample_waypoints(coords: list[tuple[float, float]], total_dist_m: float, total_dur_s: float,
                     breakpoints: list[tuple[float, float]] | None, spacing_km: float,
                     max_points: int) -> list[tuple[float, float, float]]:
    """coords = [(lat, lon)...] along the route. Returns [(lat, lon, elapsed_s)], first=start, last=end.
    breakpoints = [(cum_distance_m, cum_duration_s)...] from OSRM steps, starting (0, 0)."""
    if not coords:
        return []
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_km(a[0], a[1], b[0], b[1]) * 1000.0)
    path_len = cum[-1]
    if len(coords) < 2 or path_len < 1.0:
        return [(coords[0][0], coords[0][1], 0.0)]                       # origin == destination
    n = max(1, min(max(1, max_points) - 1, math.ceil((total_dist_m / 1000.0) / max(spacing_km, 0.1))))
    out: list[tuple[float, float, float]] = []
    last_t = 0.0
    for j in range(n + 1):
        target = path_len * j / n
        i = min(max(bisect.bisect_right(cum, target) - 1, 0), len(coords) - 2)
        seg = cum[i + 1] - cum[i]
        f = 0.0 if seg <= 0 else (target - cum[i]) / seg
        lat = coords[i][0] + (coords[i + 1][0] - coords[i][0]) * f
        lon = coords[i][1] + (coords[i + 1][1] - coords[i][1]) * f
        t = total_dur_s if j == n else _interp_time(total_dist_m * j / n, breakpoints, total_dist_m, total_dur_s)
        t = max(t, last_t)                                               # monotone
        out.append((lat, lon, t))
        last_t = t
    return out


def _breakpoints(route: dict[str, Any]) -> list[tuple[float, float]] | None:
    pts = [(0.0, 0.0)]
    for leg in route.get("legs") or []:
        for st in leg.get("steps") or []:
            try:
                pts.append((pts[-1][0] + float(st["distance"]), pts[-1][1] + float(st["duration"])))
            except (KeyError, TypeError, ValueError):
                return None
    return pts if len(pts) > 1 else None


# ---------------- public API ----------------
@dataclass
class RoutesResult:
    routes: list[RouteOption] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def get_routes_ex(o: LatLon, d: LatLon, alternatives: bool = True, mandi_id: str = "dest") -> RoutesResult:
    """Routes + failure info. route_id = '<mandi_id>~r<i>'. Evidence ids are attached to each route."""
    cfg = get_settings()
    path = f"/route/v1/driving/{_coord(o)};{_coord(d)}"
    params = {"alternatives": "true" if alternatives else "false", "overview": "full",
              "geometries": "geojson", "steps": "true"}
    res = _osrm_call(path, params, f"route|{_coord(o)}|{_coord(d)}|{int(alternatives)}")
    if not res.ok:
        return RoutesResult(error=res.error or "routing failed")
    out = RoutesResult()
    raw_routes = (res.data or {}).get("routes") or []
    if not raw_routes:
        return RoutesResult(error="OSRM returned no routes")
    for i, rt in enumerate(raw_routes[:MAX_ROUTES_PER_MANDI]):
        try:
            dist_m, dur_s = float(rt["distance"]), float(rt["duration"])
        except (KeyError, TypeError, ValueError):
            out.warnings.append(f"route {i}: missing distance/duration, skipped")
            continue
        raw_coords = ((rt.get("geometry") or {}).get("coordinates")) or []
        coords = [(float(c[1]), float(c[0])) for c in raw_coords if isinstance(c, (list, tuple)) and len(c) >= 2]
        bps = _breakpoints(rt)
        geometry_ok = len(coords) >= 2
        if geometry_ok:
            pts = sample_waypoints(coords, dist_m, dur_s, bps, cfg.waypoint_spacing_km, cfg.max_waypoints)
        else:
            pts = [(o.lat, o.lon, 0.0), (d.lat, d.lon, dur_s)]
            out.warnings.append(f"route {i}: no geometry returned; only start/end waypoints (geometry_ok=False)")
        wps = [Waypoint(idx=k, point=LatLon(lat=p[0], lon=p[1]), elapsed_nominal_s=p[2]) for k, p in enumerate(pts)]
        basis = "OSRM step durations" if (geometry_ok and bps) else "distance-proportional split (no steps)"
        e_d = http.make_evidence(Kind.COMPUTED, "Road distance (OSRM)", round(dist_m / 1000.0, 3), "km", res.source,
                                 None, res.fetched_at, "OSRM public demo server; shortest-time route", res.stale)
        e_t = http.make_evidence(Kind.COMPUTED, "Estimated travel time (OSRM, no traffic)", round(dur_s / 3600.0, 4),
                                 "h", res.source, None, res.fetched_at,
                                 f"free-flow estimate, no live traffic; waypoint times from {basis}", res.stale)
        out.routes.append(RouteOption(route_id=f"{mandi_id}~r{i}", distance_km=dist_m / 1000.0,
                                      duration_h_nominal=dur_s / 3600.0, waypoints=wps, geometry_ok=geometry_ok,
                                      evidence_ids=[e_d.id, e_t.id]))
    if not out.routes:
        out.error = "no usable route in OSRM response"
    return out


def get_routes(o: LatLon, d: LatLon, alternatives: bool = True, mandi_id: str = "dest") -> list[RouteOption]:
    """BLUEPRINT A5 signature (+ optional mandi_id for unique route ids). [] on failure."""
    r = get_routes_ex(o, d, alternatives, mandi_id)
    if r.error:
        log.warning("get_routes failed: %s", r.error)
    return r.routes


def get_route_table(route: RouteOption, mandis: dict[str, LatLon]) -> str | None:
    """One OSRM table call: waypoints -> every candidate mandi. Fills Waypoint.to_dest_s in place.
    Returns an error string (waypoints keep to_dest_s = {} / None values) or None on success."""
    if not mandis:
        return None
    ids = sorted(mandis)
    src = [w.point for w in route.waypoints]
    dst = [mandis[m] for m in ids]
    coords = ";".join(_coord(p) for p in src + dst)
    params = {"sources": ";".join(str(i) for i in range(len(src))),
              "destinations": ";".join(str(len(src) + j) for j in range(len(dst))), "annotations": "duration"}
    res = _osrm_call(f"/table/v1/driving/{coords}", params, f"table|{coords}")
    if not res.ok:
        return res.error or "table failed"
    rows = (res.data or {}).get("durations")
    if not (isinstance(rows, list) and len(rows) == len(src) and all(isinstance(r, list) and len(r) == len(dst) for r in rows)):
        return "OSRM table: unexpected durations shape"
    for w, row in zip(route.waypoints, rows, strict=True):
        for mid, v in zip(ids, row, strict=True):
            try:
                w.to_dest_s[mid] = float(v) if v is not None else None      # None = unreachable
            except (TypeError, ValueError):
                w.to_dest_s[mid] = None
    ev = http.make_evidence(Kind.COMPUTED, "Estimated travel time from route waypoints to candidate mandis (OSRM table, no traffic)",
                            None, "s", res.source, None, res.fetched_at,
                            f"{len(src)} waypoints x {len(dst)} mandis; free-flow, no traffic", res.stale)
    route.evidence_ids.append(ev.id)
    return None
