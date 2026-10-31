"""A6 tests (FIXTURE Open-Meteo responses)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
import responses

from app.contracts import LatLon, RouteOption, Waypoint
from app.data import weather
from app.data.geo import IST
from app.db import db
from tests.data.fixtures import FIXTURE_NOW, FIXTURE_flood_cb, FIXTURE_forecast_cb

FC_URL = weather.FORECAST_URL


def _route(elapsed=(0, 3600, 7200)):
    return RouteOption(route_id="r", distance_km=100, duration_h_nominal=2.0, geometry_ok=True,
                       waypoints=[Waypoint(idx=i, point=LatLon(lat=19 + i * 0.1, lon=73), elapsed_nominal_s=e)
                                  for i, e in enumerate(elapsed)])


def _hourly(temps, precs, start="2026-10-30T00:00"):
    t0 = datetime.fromisoformat(start)
    return {"utc_offset_seconds": 19800,
            "hourly": {"time": [(t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(len(temps))],
                       "temperature_2m": temps, "precipitation": precs}}


@responses.activate
def test_hour_alignment_uses_local_hour_with_half_hour_offset():
    # departure 14:20 IST = 08:50 UTC. Local nearest hour = 14:00; +40 min later (15:00 IST) -> 15:00 ... etc.
    responses.add(responses.GET, FC_URL, json=_hourly([float(h) for h in range(48)], [0.1 * h for h in range(48)]))
    r = _route(elapsed=(0,))
    depart = datetime(2026, 10, 30, 14, 20, tzinfo=IST)
    s = weather.get_weather_along(r, depart, FIXTURE_NOW)[0]
    assert s.time_iso == "2026-10-30T14:00:00+05:30" and s.temp_c == 14.0
    depart2 = datetime(2026, 10, 30, 14, 40, tzinfo=IST)                      # rounds UP to 15:00
    s2 = weather.get_weather_along(_route(elapsed=(0,)), depart2, FIXTURE_NOW)[0]
    assert s2.time_iso == "2026-10-30T15:00:00+05:30" and s2.temp_c == 15.0


@responses.activate
def test_alignment_adds_elapsed_time_and_accepts_utc_departure():
    h = _hourly([float(h) for h in range(48)], [0.0] * 48)
    responses.add(responses.GET, FC_URL, json=[h, h])                           # 2 waypoints -> list of 2
    dep_utc = datetime(2026, 10, 30, 8, 30, tzinfo=timezone.utc)                # = 14:00 IST
    out = weather.get_weather_along(_route(elapsed=(0, 5400)), dep_utc, FIXTURE_NOW)   # +1.5 h -> 15:30 -> 16:00
    assert [o.temp_c for o in out] == [14.0, 16.0]


@responses.activate
def test_single_location_object_and_list_both_parsed():
    responses.add(responses.GET, FC_URL, json=_hourly([1.0] * 48, [0.0] * 48))                 # dict (1 waypoint)
    one = weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW)
    assert len(one) == 1
    responses.replace(responses.GET, FC_URL, json=[_hourly([1.0] * 48, [0.0] * 48), _hourly([2.0] * 48, [0.0] * 48)])
    two = weather.get_weather_along(_route(elapsed=(0, 60)), FIXTURE_NOW, FIXTURE_NOW)         # list (2 waypoints)
    assert [s.temp_c for s in two] == [1.0, 2.0]


@responses.activate
def test_one_call_per_route_with_all_waypoints():
    responses.add_callback(responses.GET, FC_URL, callback=FIXTURE_forecast_cb)
    weather.get_weather_along(_route(), FIXTURE_NOW, FIXTURE_NOW)
    assert len(responses.calls) == 1
    q = responses.calls[0].request.url
    assert "timezone=auto" in q and "forecast_days=3" in q and "temperature_2m%2Cprecipitation" in q


@responses.activate
def test_missing_values_are_none_never_zero():
    temps = [20.0] * 48
    precs = [0.0] * 48
    temps[14] = None
    precs[14] = None
    responses.add(responses.GET, FC_URL, json=_hourly(temps, precs))
    s = weather.get_weather_along(_route(elapsed=(0,)), datetime(2026, 10, 30, 14, 0, tzinfo=IST), FIXTURE_NOW)[0]
    assert s.temp_c is None and s.precip_mm is None
    ev = db.get_evidence(s.evidence_id)
    assert ev["value"] is None and ev["kind"] == "forecast"


@responses.activate
def test_eta_beyond_horizon_is_none_not_extrapolated():
    responses.add(responses.GET, FC_URL, json=_hourly([20.0] * 24, [0.0] * 24))              # only 24 h of data
    late = datetime(2026, 10, 31, 20, 0, tzinfo=IST)
    s = weather.get_weather_along(_route(elapsed=(0,)), late, FIXTURE_NOW)[0]
    assert s.temp_c is None and s.precip_mm is None
    assert "horizon" in db.get_evidence(s.evidence_id)["note"]


@responses.activate
def test_failure_returns_empty_with_error():
    responses.add(responses.GET, FC_URL, status=500)
    r = weather.get_weather_along_ex(_route(), FIXTURE_NOW, FIXTURE_NOW)
    assert r.samples == [] and r.error
    assert weather.get_weather_along(_route(), FIXTURE_NOW, FIXTURE_NOW) == []


@responses.activate
def test_wrong_number_of_locations_is_rejected():
    responses.add(responses.GET, FC_URL, json=[_hourly([1.0] * 48, [0.0] * 48)])              # 1 returned for 3 asked
    assert weather.get_weather_along_ex(_route(), FIXTURE_NOW, FIXTURE_NOW).error


@responses.activate
def test_evidence_is_forecast_with_fetch_time_as_of_and_precip_in_note():
    responses.add(responses.GET, FC_URL, json=_hourly([21.5] * 48, [0.7] * 48))
    s = weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW.replace(hour=14), FIXTURE_NOW)[0]
    ev = db.get_evidence(s.evidence_id)
    assert ev["kind"] == "forecast" and ev["value"] == 21.5 and ev["unit"] == "\u00b0C"
    assert ev["as_of"] == ev["fetched_at"] and "precip_mm=0.7" in ev["note"] and "model run time" in ev["note"]


@responses.activate
def test_stale_cache_after_failure_marks_evidence_stale():
    t = [datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)]
    __import__("app.data.http", fromlist=["x"]).set_time_hooks(utcnow=lambda: t[0])
    responses.add(responses.GET, FC_URL, json=_hourly([20.0] * 48, [0.0] * 48))
    weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW)
    t[0] += timedelta(hours=1)                                                               # > 30 min TTL, < 3 h stale cap
    responses.replace(responses.GET, FC_URL, body=requests.ConnectionError("down"))
    s = weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW)[0]
    assert s.temp_c == 20.0 and db.get_evidence(s.evidence_id)["stale"] is True


@responses.activate
def test_naive_departure_rejected():
    import pytest
    with pytest.raises(ValueError):
        weather.get_weather_along(_route(), datetime(2026, 10, 30, 14, 0), FIXTURE_NOW)


@responses.activate
def test_destination_weather_hourly_window():
    responses.add_callback(responses.GET, FC_URL, callback=FIXTURE_forecast_cb)
    start = datetime(2026, 10, 30, 16, 0, tzinfo=IST)
    out = weather.get_destination_weather(LatLon(lat=19.5, lon=73), (start, start + timedelta(hours=5)))
    assert [s.waypoint_idx for s in out] == [-1] * 6
    assert [s.time_iso[11:16] for s in out] == ["16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    assert weather.get_destination_weather(LatLon(lat=19.5, lon=73), (start, start - timedelta(hours=1))) == []


@responses.activate
def test_river_flow_proxy_is_labelled_proxy_and_uses_peak():
    responses.add_callback(responses.GET, weather.FLOOD_URL, callback=FIXTURE_flood_cb)
    ev = weather.get_river_flow_proxy(_route(), FIXTURE_NOW)
    assert ev.kind.value == "proxy" and ev.value == 30.25 and "not road flooding" in ev.label
    assert "ADVISORY" in ev.note
    responses.replace(responses.GET, weather.FLOOD_URL, json={"daily": {"river_discharge": [None, None]}})
    r2 = _route(); r2.waypoints[1].point = LatLon(lat=25, lon=80)
    assert weather.get_river_flow_proxy(r2, FIXTURE_NOW) is None            # no data -> None, not 0


@responses.activate
def test_river_flow_failure_is_none():
    responses.add(responses.GET, weather.FLOOD_URL, status=500)
    assert weather.get_river_flow_proxy(_route(), FIXTURE_NOW) is None


@responses.activate
def test_force_bypasses_weather_cache():
    responses.add(responses.GET, FC_URL, json=_hourly([20.0] * 48, [0.0] * 48))
    weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW)
    weather.get_weather_along(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW)
    assert len(responses.calls) == 1
    weather.get_weather_along_ex(_route(elapsed=(0,)), FIXTURE_NOW, FIXTURE_NOW, force=True)
    assert len(responses.calls) == 2
