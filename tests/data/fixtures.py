"""FIXTURE_* builders -- FIXTURE: not observed data. Used only by tests (AGENT NN-1)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

from app.contracts import Costs, DispatchRequest, LatLon, RipenessStage, VehicleType
from app.data.geo import IST, haversine_km

FIXTURE_NOW = datetime(2026, 10, 30, 10, 0, tzinfo=IST)


def FIXTURE_record(state="Maharashtra", district="Nashik", market="Nashik", variety="Hybrid", grade="FAQ",
                   date="29/10/2026", mn="1500", mo="2000", mx="2500", commodity="Tomato"):
    """FIXTURE — not observed data."""
    return {"state": state, "district": district, "market": market, "commodity": commodity, "variety": variety,
            "grade": grade, "arrival_date": date, "min_price": mn, "max_price": mx, "modal_price": mo}


def FIXTURE_request(**kw) -> DispatchRequest:
    """FIXTURE — not observed data. Origin ~ Nashik-ish coordinates."""
    base = dict(quantity_qtl=40, origin=LatLon(lat=20.00, lon=73.78), origin_label="FIXTURE origin",
                origin_state="Maharashtra", ripeness=RipenessStage.BREAKER, vehicle=VehicleType.OPEN,
                loading_time=FIXTURE_NOW, preferred_departure=FIXTURE_NOW.replace(hour=14),
                costs=Costs(transport_mode="per_km_per_trip", transport_rate=30))
    base.update(kw)
    return DispatchRequest(**base)


def FIXTURE_osrm_route_cb(request):
    """FIXTURE OSRM /route: straight line, 1.2x road factor, 40 km/h, 2 steps (second one slower)."""
    path = unquote(urlsplit(request.url).path)
    a, b = path.split("/driving/")[1].split(";")
    (lo1, la1), (lo2, la2) = [tuple(map(float, x.split(","))) for x in (a, b)]
    dist_km = haversine_km(la1, lo1, la2, lo2) * 1.2
    n = 12
    coords = [[lo1 + (lo2 - lo1) * i / n, la1 + (la2 - la1) * i / n] for i in range(n + 1)]
    half = dist_km * 1000 / 2
    steps = [{"distance": half, "duration": half / 1000 / 60 * 3600},      # 60 km/h
             {"distance": half, "duration": half / 1000 / 30 * 3600}]      # 30 km/h
    dur = sum(s["duration"] for s in steps)
    body = {"code": "Ok", "routes": [{"distance": dist_km * 1000, "duration": dur,
                                      "geometry": {"type": "LineString", "coordinates": coords},
                                      "legs": [{"steps": steps}]}]}
    return 200, {}, json.dumps(body)


def FIXTURE_osrm_table_cb(request):
    """FIXTURE OSRM /table: haversine*1.2 at 40 km/h."""
    u = urlsplit(request.url)
    pts = [tuple(map(float, x.split(","))) for x in unquote(u.path).split("/driving/")[1].split(";")]
    q = parse_qs(u.query)
    src = [int(i) for i in q["sources"][0].split(";")]
    dst = [int(i) for i in q["destinations"][0].split(";")]
    rows = [[haversine_km(pts[i][1], pts[i][0], pts[j][1], pts[j][0]) * 1.2 / 40 * 3600 for j in dst] for i in src]
    return 200, {}, json.dumps({"code": "Ok", "durations": rows})


def FIXTURE_forecast_cb(request):
    """FIXTURE Open-Meteo forecast: 72 local hours from 2026-10-30T00:00, UTC+05:30; temp 20+0.1*h, rain 0."""
    q = parse_qs(urlsplit(request.url).query)
    n = len(q["latitude"][0].split(","))
    times = [(datetime(2026, 10, 30) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(72)]
    loc = {"utc_offset_seconds": 19800, "timezone": "Asia/Kolkata",
           "hourly": {"time": times, "temperature_2m": [20 + 0.1 * h for h in range(72)], "precipitation": [0.0] * 72}}
    return 200, {}, json.dumps([loc] * n if n > 1 else loc)


def FIXTURE_flood_cb(request):
    return 200, {}, json.dumps({"daily": {"time": ["2026-10-30", "2026-10-31"], "river_discharge": [12.5, 30.25]}})


def km_offset(lat, lon, north_km, east_km):
    return lat + north_km / 111.0, lon + east_km / (111.0 * math.cos(math.radians(lat)))
