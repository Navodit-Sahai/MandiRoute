"""Weather along a route (BLUEPRINT A6) -- Open-Meteo forecast (CC BY 4.0, free non-commercial tier).

* ONE multi-location call per route (hourly temperature_2m + precipitation, timezone=auto, forecast_days=3).
  Open-Meteo returns a LIST for several locations but a single OBJECT for one -> both handled.
* Each waypoint is aligned to the LOCAL hour nearest to (depart + elapsed_nominal_s). India is UTC+05:30, so
  local hour boundaries are NOT UTC hour boundaries -> alignment uses the response's utc_offset_seconds.
* Missing values -> None, NEVER 0. ETA outside the 3-day horizon -> None (+ warning), never extrapolated.
* Evidence kind=forecast. as_of = time WE fetched (the API does not expose the model run time) -- stated in
  the evidence note. One Evidence row per sample (value = temperature; precipitation is stated in the note:
  WeatherSample carries a single evidence_id by contract).
* River-flow (Flood API) is a PROXY, advisory only: never road flooding (NN-5).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.contracts import Evidence, Kind, LatLon, RouteOption, WeatherSample
from app.data import http

log = logging.getLogger("mandiguard.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
SRC_FORECAST = "open_meteo_forecast"
SRC_FLOOD = "open_meteo_flood"
FORECAST_DAYS = 3
WEATHER_MAX_STALE_S = 3 * 3600
MAX_DEST_HOURS = 48
_TFMT = "%Y-%m-%dT%H:%M"


@dataclass
class WeatherResult:
    samples: list[WeatherSample] = field(default_factory=list)
    error: str | None = None


def _r2(x: float) -> float:
    return round(x, 2)


def _check_locations(n: int):
    def _check(data: Any) -> str | None:
        items = data if isinstance(data, list) else [data]
        if len(items) != n:
            return f"expected {n} locations, got {len(items)}"
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("hourly"), dict) or "time" not in it["hourly"]:
                return "response has no hourly block"
        return None
    return _check


def _fetch_hourly(points: list[LatLon], force: bool = False) -> tuple[list[dict[str, Any]] | None, http.HttpResult]:
    lat = ",".join(f"{_r2(p.lat)}" for p in points)
    lon = ",".join(f"{_r2(p.lon)}" for p in points)
    params = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m,precipitation",
              "timezone": "auto", "forecast_days": FORECAST_DAYS}
    res = http.request_json(SRC_FORECAST, "GET", FORECAST_URL, params, ttl=get_settings().cache_ttl_s["weather"],
                            rate_limiter=http.get_limiter("open_meteo"), cache_key=f"wx|{lat}|{lon}|{FORECAST_DAYS}",
                            cache_table="weather_cache", max_stale_s=WEATHER_MAX_STALE_S, force=force,
                            check=_check_locations(len(points)))
    if not res.ok:
        return None, res
    return (res.data if isinstance(res.data, list) else [res.data]), res


def _local_hour_key(eta: datetime, offset_s: int) -> tuple[str, str]:
    """(key matching Open-Meteo 'time' strings, tz-aware ISO) for the local hour nearest to eta."""
    tz = timezone(timedelta(seconds=offset_s))
    local = eta.astimezone(tz)
    rounded = (local + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    return rounded.strftime(_TFMT), rounded.isoformat()


def _val(arr: Any, idx: int | None) -> float | None:
    if idx is None or not isinstance(arr, list) or idx >= len(arr):
        return None
    v = arr[idx]
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _sample(loc: dict[str, Any], eta: datetime, wp_idx: int, res: http.HttpResult) -> WeatherSample:
    hourly = loc["hourly"]
    offset = loc.get("utc_offset_seconds")
    if not isinstance(offset, (int, float)):
        offset = 19800                              # documented fallback: India is UTC+05:30 (no DST)
    key, iso = _local_hour_key(eta, int(offset))
    times = hourly.get("time") or []
    idx = times.index(key) if key in times else None
    temp, precip = _val(hourly.get("temperature_2m"), idx), _val(hourly.get("precipitation"), idx)
    label = (f"Forecast temperature at waypoint {wp_idx}, hour {iso}" if wp_idx >= 0
             else f"Forecast temperature at destination, hour {iso}")
    note = (f"precip_mm={precip}; hourly Open-Meteo forecast; as_of = time we fetched it "
            f"(API does not expose the model run time)")
    if idx is None:
        note += f"; hour {iso} is outside the {FORECAST_DAYS}-day forecast horizon -> values unavailable"
    ev = http.make_evidence(Kind.FORECAST, label, temp, "\u00b0C", SRC_FORECAST, res.fetched_at, res.fetched_at, note, res.stale)
    return WeatherSample(waypoint_idx=wp_idx, time_iso=iso, temp_c=temp, precip_mm=precip, evidence_id=ev.id)


def get_weather_along_ex(route: RouteOption, depart: datetime, now: datetime, force: bool = False) -> WeatherResult:
    if depart.tzinfo is None:
        raise ValueError("depart must be timezone-aware")
    if depart < now - timedelta(hours=1):
        http.add_warning("departure time is in the past; forecast alignment uses it as given")
    if not route.waypoints:
        return WeatherResult(error="route has no waypoints")
    locs, res = _fetch_hourly([w.point for w in route.waypoints], force)
    if locs is None:
        return WeatherResult(error=res.error or "weather fetch failed")
    if res.stale:
        http.add_warning(f"weather: live fetch failed, using CACHED forecast from {res.fetched_at}")
    out = WeatherResult()
    beyond = 0
    d0 = depart.astimezone(timezone.utc)
    for w, loc in zip(route.waypoints, locs, strict=True):
        s = _sample(loc, d0 + timedelta(seconds=w.elapsed_nominal_s), w.idx, res)
        beyond += s.temp_c is None and s.precip_mm is None
        out.samples.append(s)
    if beyond:
        http.add_warning(f"route {route.route_id}: {beyond} waypoint(s) have no forecast values (null or beyond horizon)")
    return out


def get_weather_along(route: RouteOption, depart: datetime, now: datetime) -> list[WeatherSample]:
    """BLUEPRINT A6 signature. [] when the forecast could not be fetched (flagged by the assembler)."""
    r = get_weather_along_ex(route, depart, now)
    if r.error:
        log.warning("weather unavailable for %s: %s", route.route_id, r.error)
    return r.samples


def get_destination_weather(loc: LatLon, window: tuple[datetime, datetime], force: bool = False) -> list[WeatherSample]:
    """Hourly samples at a destination from window[0] to window[1] inclusive (max 48 h). waypoint_idx = -1."""
    start, end = (w.astimezone(timezone.utc) for w in window)
    if end < start:
        return []
    locs, res = _fetch_hourly([loc], force)
    if locs is None:
        log.warning("destination weather unavailable: %s", res.error)
        return []
    out: list[WeatherSample] = []
    t, seen = start, set()
    while t <= end + timedelta(minutes=30) and len(out) < MAX_DEST_HOURS:
        s = _sample(locs[0], t, -1, res)
        if s.time_iso not in seen:
            seen.add(s.time_iso)
            out.append(s)
        t += timedelta(hours=1)
    return out


def get_river_flow_proxy(route: RouteOption, now: datetime, force: bool = False) -> Evidence | None:
    """Peak forecast river discharge near the route midpoint. PROXY ONLY: not road flooding (NN-5),
    advisory, never triggers a replan. None when unavailable."""
    if not route.waypoints:
        return None
    mid = route.waypoints[len(route.waypoints) // 2].point
    lat, lon = _r2(mid.lat), _r2(mid.lon)
    res = http.request_json(SRC_FLOOD, "GET", FLOOD_URL,
                            {"latitude": lat, "longitude": lon, "daily": "river_discharge", "forecast_days": FORECAST_DAYS},
                            ttl=get_settings().cache_ttl_s["weather"], rate_limiter=http.get_limiter("open_meteo"),
                            cache_key=f"flood|{lat}|{lon}|{FORECAST_DAYS}", cache_table="weather_cache",
                            max_stale_s=WEATHER_MAX_STALE_S, force=force)
    if not res.ok or not isinstance(res.data, dict):
        return None
    daily = res.data.get("daily") or {}
    vals = [float(v) for v in (daily.get("river_discharge") or []) if v is not None]
    if not vals:
        return None
    return http.make_evidence(
        Kind.PROXY, "River-flow alert (proxy, not road flooding): peak forecast discharge near route midpoint", round(max(vals), 3), "m3/s",
        SRC_FLOOD, res.fetched_at, res.fetched_at,
        "peak daily discharge over the forecast window; GloFAS ~5 km grid cell = largest river in the cell; "
        "ADVISORY ONLY; alert threshold/percentile NOT computed (no climatology baseline fetched)", res.stale)
