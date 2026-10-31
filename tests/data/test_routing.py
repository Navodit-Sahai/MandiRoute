"""A5 tests (FIXTURE OSRM responses)."""
from __future__ import annotations

import json
import re

import requests
import responses

from app.contracts import LatLon, RouteOption, Waypoint
from app.data import routing
from app.data.geo import haversine_km
from app.db import db
from tests.data.fixtures import FIXTURE_osrm_route_cb, FIXTURE_osrm_table_cb

ROUTE_RE = re.compile(r"https://router\.project-osrm\.org/route/v1/driving/.*")
TABLE_RE = re.compile(r"https://router\.project-osrm\.org/table/v1/driving/.*")
FB_RE = re.compile(r"https://routing\.openstreetmap\.de/routed-car/.*")
O, D = LatLon(lat=19.0, lon=73.0), LatLon(lat=20.0, lon=73.0)        # ~111 km due north


def _line(n=50, lat0=19.0, lat1=20.0, lon=73.0):
    return [(lat0 + (lat1 - lat0) * i / n, lon) for i in range(n + 1)]


# ---------------- pure sampling ----------------
def test_waypoint_count_first_last_and_monotone():
    pts = routing.sample_waypoints(_line(), 111_000, 4000, None, 25, 8)
    assert 2 <= len(pts) <= 8 and len(pts) == 6                 # ceil(111/25)=5 intervals -> 6 points
    assert (pts[0][0], pts[0][2]) == (19.0, 0.0) and abs(pts[-1][0] - 20.0) < 1e-9 and pts[-1][2] == 4000
    assert all(b[2] >= a[2] for a, b in zip(pts, pts[1:]))


def test_waypoints_capped_at_max_and_spacing_widens():
    pts = routing.sample_waypoints(_line(), 111_000, 4000, None, 1, 8)     # 1 km spacing would be 112 points
    assert len(pts) == 8
    assert len(routing.sample_waypoints(_line(), 111_000, 4000, None, 25, 3)) == 3


def test_short_route_gets_start_and_end():
    pts = routing.sample_waypoints(_line(lat1=19.05), 5_500, 400, None, 25, 8)
    assert len(pts) == 2


def test_origin_equals_destination_single_waypoint():
    assert routing.sample_waypoints([(19.0, 73.0), (19.0, 73.0)], 0, 0, None, 25, 8) == [(19.0, 73.0, 0.0)]
    assert routing.sample_waypoints([], 0, 0, None, 25, 8) == []


def test_elapsed_uses_step_durations_not_proportional():
    # first half of distance takes 1000 s, second half 3000 s (total 4000)
    bps = [(0.0, 0.0), (55_500.0, 1000.0), (111_000.0, 4000.0)]
    mid = routing.sample_waypoints(_line(), 111_000, 4000, bps, 55.5, 3)[1]        # n=2 -> middle point
    assert abs(mid[2] - 1000.0) < 5                      # proportional split would say 2000
    prop = routing.sample_waypoints(_line(), 111_000, 4000, None, 55.5, 3)[1]
    assert abs(prop[2] - 2000.0) < 5


def test_step_totals_are_normalised_to_route_totals():
    bps = [(0.0, 0.0), (50_000.0, 500.0), (100_000.0, 1000.0)]                      # steps sum to 100 km/1000 s
    pts = routing.sample_waypoints(_line(), 111_000, 4400, bps, 25, 8)
    assert pts[-1][2] == 4400 and all(b[2] >= a[2] for a, b in zip(pts, pts[1:]))


# ---------------- get_routes / table ----------------
@responses.activate
def test_get_routes_builds_waypoints_and_evidence():
    responses.add_callback(responses.GET, ROUTE_RE, callback=FIXTURE_osrm_route_cb)
    r = routing.get_routes_ex(O, D, True, "m1")
    assert not r.error and len(r.routes) == 1
    rt = r.routes[0]
    assert rt.route_id == "m1~r0" and rt.geometry_ok and 111 < rt.distance_km < 140
    assert len(rt.waypoints) <= 8 and rt.waypoints[0].elapsed_nominal_s == 0
    assert abs(rt.waypoints[-1].elapsed_nominal_s - rt.duration_h_nominal * 3600) < 1e-6
    e_dist, e_time = (db.get_evidence(i) for i in rt.evidence_ids)
    assert e_dist["unit"] == "km" and e_dist["kind"] == "computed"
    assert "no traffic" in e_time["label"] and "step durations" in e_time["note"]
    # slower second half => first waypoint after halfway is reached later than a proportional split would say
    half = rt.waypoints[len(rt.waypoints) // 2]
    assert half.elapsed_nominal_s < rt.duration_h_nominal * 3600 * 0.5 + 1


@responses.activate
def test_route_cache_hit_no_second_call():
    responses.add_callback(responses.GET, ROUTE_RE, callback=FIXTURE_osrm_route_cb)
    routing.get_routes(O, D)
    n = len(responses.calls)
    routing.get_routes(O, D)
    assert len(responses.calls) == n


@responses.activate
def test_table_mapping_and_unreachable_none():
    responses.add_callback(responses.GET, ROUTE_RE, callback=FIXTURE_osrm_route_cb)
    rt = routing.get_routes(O, D, mandi_id="m1")[0]
    dest_b = LatLon(lat=19.5, lon=73.1)

    def cb(request):
        code, h, body = FIXTURE_osrm_table_cb(request)
        data = json.loads(body)
        for row in data["durations"]:
            row[0] = None                                # first destination (m_a) unreachable everywhere
        return code, h, json.dumps(data)
    responses.add_callback(responses.GET, TABLE_RE, callback=cb)
    err = routing.get_route_table(rt, {"m_b": dest_b, "m_a": D})
    assert err is None
    assert all(set(w.to_dest_s) == {"m_a", "m_b"} for w in rt.waypoints)
    assert all(w.to_dest_s["m_a"] is None for w in rt.waypoints)                 # sorted ids: m_a first
    w0 = rt.waypoints[0]
    expected = haversine_km(w0.point.lat, w0.point.lon, dest_b.lat, dest_b.lon) * 1.2 / 40 * 3600
    assert abs(w0.to_dest_s["m_b"] - expected) / expected < 0.02          # value really comes from the table row/col
    assert len(rt.evidence_ids) == 3 and "table" in db.get_evidence(rt.evidence_ids[-1])["label"]
    # exactly ONE table call for the whole route
    assert sum(1 for c in responses.calls if "/table/" in c.request.url) == 1


@responses.activate
def test_table_failure_returns_error_not_exception():
    rt = RouteOption(route_id="r", distance_km=1, duration_h_nominal=1,
                     waypoints=[Waypoint(idx=0, point=O, elapsed_nominal_s=0)])
    responses.add(responses.GET, TABLE_RE, status=500)
    responses.add(responses.GET, FB_RE, status=500)
    err = routing.get_route_table(rt, {"m": D})
    assert err and rt.waypoints[0].to_dest_s == {}


@responses.activate
def test_table_bad_shape_is_error():
    rt = RouteOption(route_id="r", distance_km=1, duration_h_nominal=1,
                     waypoints=[Waypoint(idx=0, point=O, elapsed_nominal_s=0)])
    responses.add(responses.GET, TABLE_RE, json={"code": "Ok", "durations": [[1, 2, 3]]})
    assert "shape" in routing.get_route_table(rt, {"m": D})


@responses.activate
def test_both_servers_fail_returns_failure_and_never_a_straight_line():
    responses.add(responses.GET, ROUTE_RE, status=502)
    responses.add(responses.GET, FB_RE, body=requests.ConnectionError("down"))
    r = routing.get_routes_ex(O, D)
    assert r.routes == [] and r.error
    assert routing.get_routes(O, D) == []


@responses.activate
def test_fallback_server_used_when_primary_down_and_breaker_skips_primary():
    responses.add(responses.GET, ROUTE_RE, status=503)
    responses.add_callback(responses.GET, FB_RE, callback=FIXTURE_osrm_route_cb)
    assert len(routing.get_routes(O, D, mandi_id="a")) == 1
    n_primary = sum(1 for c in responses.calls if "router.project-osrm.org" in c.request.url)
    other = LatLon(lat=20.2, lon=73.1)
    assert len(routing.get_routes(O, other, mandi_id="b")) == 1
    assert sum(1 for c in responses.calls if "router.project-osrm.org" in c.request.url) == n_primary   # breaker: no new primary calls


@responses.activate
def test_no_route_is_definitive_no_fallback_call():
    responses.add(responses.GET, ROUTE_RE, json={"code": "NoRoute", "message": "Impossible route"})
    r = routing.get_routes_ex(O, D)
    assert r.error and "NoRoute" in r.error
    assert not any("openstreetmap.de" in c.request.url for c in responses.calls)


@responses.activate
def test_missing_geometry_is_flagged():
    body = {"code": "Ok", "routes": [{"distance": 111000, "duration": 4000}]}
    responses.add(responses.GET, ROUTE_RE, json=body)
    r = routing.get_routes_ex(O, D, mandi_id="m")
    assert r.routes[0].geometry_ok is False and len(r.routes[0].waypoints) == 2 and r.warnings


@responses.activate
def test_osrm_uses_lon_lat_order():
    responses.add_callback(responses.GET, ROUTE_RE, callback=FIXTURE_osrm_route_cb)
    routing.get_routes(O, D)
    assert "/driving/73.0000,19.0000;73.0000,20.0000" in responses.calls[0].request.url


def test_mutable_default_not_shared_between_waypoints():
    a = Waypoint(idx=0, point=O, elapsed_nominal_s=0)
    b = Waypoint(idx=1, point=O, elapsed_nominal_s=0)
    a.to_dest_s["x"] = 1.0
    assert b.to_dest_s == {} and haversine_km(19, 73, 20, 73) > 100
