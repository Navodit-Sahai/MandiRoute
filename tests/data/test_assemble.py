"""A7 + health tests. Everything mocked with FIXTURE_* (not observed data); geocoder is a test double."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import responses

from app.data import assemble, health, mandi, weather
from app.data.assemble import CandidateAssembly
from app.data.geo import mandi_id
from app.data.geocode import GeoResult
from app.db import db
from tests.data.fixtures import (FIXTURE_NOW, FIXTURE_flood_cb, FIXTURE_forecast_cb, FIXTURE_osrm_route_cb,
                                 FIXTURE_osrm_table_cb, FIXTURE_record, FIXTURE_request, km_offset)

ROUTE_RE = re.compile(r"https://router\.project-osrm\.org/route/v1/driving/.*")
TABLE_RE = re.compile(r"https://router\.project-osrm\.org/table/v1/driving/.*")
FB_RE = re.compile(r"https://routing\.openstreetmap\.de/routed-car/.*")
KEY = "SUPERSECRETKEY123"


class FakeGeo:
    """FIXTURE geocoder: coordinates by market name; unknown markets -> unresolved."""
    def __init__(self, table):
        self.table = table

    def _g(self, market):
        c = self.table.get(market)
        return GeoResult(lat=c[0], lon=c[1], precision=(c[2] if len(c) > 2 else "market"), cached=True) if c else \
            GeoResult(precision="unknown", source="none")

    def lookup_local(self, market, district, state):
        return self._g(market) if market in self.table else None

    def geocode_district(self, district, state, allow_network=True):
        return GeoResult(precision="unknown")

    def geocode_mandi(self, market, district, state, allow_network=True):
        return self._g(market)


def _geo():
    o = (20.0, 73.78)
    return FakeGeo({"Near": km_offset(*o, 30, 0), "Mid": km_offset(*o, 60, 20), "Far": km_offset(*o, 90, -30)})


def _datagov(records_by_state):
    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        st = (q.get("filters[state.keyword]") or [""])[0]
        rows = records_by_state.get(st, [])
        return 200, {}, json.dumps({"total": len(rows), "records": rows})
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=cb)


def _mock_all(records=None, weather_ok=True, route_ok=True):
    recs = records if records is not None else {"Maharashtra": [
        FIXTURE_record(market="Near", district="D1"), FIXTURE_record(market="Mid", district="D2", mn="1400", mo="1900", mx="2300"),
        FIXTURE_record(market="Far", district="D3", mn="1600", mo="2200", mx="2700")]}
    _datagov(recs)
    if route_ok:
        responses.add_callback(responses.GET, ROUTE_RE, callback=FIXTURE_osrm_route_cb)
        responses.add_callback(responses.GET, TABLE_RE, callback=FIXTURE_osrm_table_cb)
    else:
        responses.add(responses.GET, ROUTE_RE, status=500)
        responses.add(responses.GET, FB_RE, status=500)
    if weather_ok:
        responses.add_callback(responses.GET, weather.FORECAST_URL, callback=FIXTURE_forecast_cb)
    else:
        responses.add(responses.GET, weather.FORECAST_URL, status=500)
    responses.add_callback(responses.GET, weather.FLOOD_URL, callback=FIXTURE_flood_cb)


def _run(req=None, **kw):
    return assemble._assemble(req or FIXTURE_request(), FIXTURE_NOW, geocoder=kw.pop("geocoder", _geo()), **kw)


def _referenced_ids(a: CandidateAssembly) -> set[str]:
    ids: set[str] = set()
    for c in a.candidates:
        if c.price:
            ids |= set(c.price.evidence_ids)
        ids |= {s.evidence_id for s in c.destination_weather}
        for r in c.routes:
            ids |= set(r.evidence_ids) | {s.evidence_id for s in r.weather}
    return ids


@responses.activate
def test_happy_path_live_shape(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all()
    a = _run()
    assert [c.mandi_name for c in a.candidates] == ["Near, D1", "Mid, D2", "Far, D3"]        # nearest first
    assert a.selected_variety == "Hybrid" and a.selected_grade == "FAQ"
    for c in a.candidates:
        assert c.price and c.price.record_date == "2026-10-29" and c.location_precision == "market"
        assert c.routes and all(len(r.waypoints) <= 8 and r.weather for r in c.routes)
        assert c.destination_weather and c.latest_arrival_local is None                      # unknown stays unknown
        r = c.routes[0]
        assert r.waypoints[0].to_dest_s.keys() == {x.mandi_id for x in a.candidates}         # table filled for ALL candidates
        assert len(r.weather) == len(r.waypoints)
    # evidence closure: every id referenced by candidates exists in the ledger returned AND in the DB
    ledger = {e.id for e in a.evidence}
    assert _referenced_ids(a) <= ledger
    assert all(db.get_evidence(i) for i in ledger)
    names = {s.name for s in a.source_status}
    assert {"datagov_agmarknet", "osrm", "open_meteo_forecast", "open_meteo_flood"} <= names
    assert next(s for s in a.source_status if s.name == "datagov_agmarknet").as_of == "2026-10-29"
    assert any("variety/grade chosen" in w for w in a.warnings)


@responses.activate
def test_secret_key_never_stored_or_returned(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all()
    a = _run()
    with db.get_conn() as c:
        dump = "\n".join(c.iterdump())
    blob = dump + a.model_dump_json()
    assert KEY not in blob


def test_no_key_means_no_data_and_clear_warning():
    a = _run()
    assert a.candidates == [] and any("DATAGOV_KEY" in w for w in a.warnings)
    assert a.source_status[0].ok is False


@responses.activate
def test_mandi_api_down_gives_empty_not_fake(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    responses.add(responses.GET, mandi.RESOURCE_URL, status=500)
    a = _run()
    assert a.candidates == [] and next(s for s in a.source_status if s.name == "datagov_agmarknet").ok is False
    assert not any(c.request.url.startswith("https://router") for c in responses.calls)      # stopped early


@responses.activate
def test_stale_prices_degrade_then_abstain_window(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    old = {"Maharashtra": [FIXTURE_record(market="Near", district="D1", date="25/10/2026")]}      # 5 days old
    _mock_all(old)
    a = _run()
    assert len(a.candidates) == 1 and any("5 days old" in w for w in a.warnings)
    evs = [db.get_evidence(i) for i in a.candidates[0].price.evidence_ids]
    assert all(e["stale"] for e in evs)


@responses.activate
def test_records_older_than_abstain_days_are_ignored(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all({"Maharashtra": [FIXTURE_record(market="Near", district="D1", date="15/10/2026")]})   # 15 days
    a = _run()
    assert a.candidates == [] and any("no usable" in w for w in a.warnings)


@responses.activate
def test_weather_failure_flags_but_keeps_candidate(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all(weather_ok=False)
    a = _run()
    assert a.candidates and all(r.weather == [] for c in a.candidates for r in c.routes)
    assert any("weather unavailable" in w for w in a.warnings)
    assert next(s for s in a.source_status if s.name == "open_meteo_forecast").ok is False


@responses.activate
def test_route_failure_keeps_candidate_with_no_routes_and_no_fake_line(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all(route_ok=False)
    a = _run()
    assert a.candidates and all(c.routes == [] for c in a.candidates)
    assert any("no route" in w for w in a.warnings)


@responses.activate
def test_unlocatable_mandi_dropped_with_warning(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    recs = {"Maharashtra": [FIXTURE_record(market="Near", district="D1"), FIXTURE_record(market="Ghost", district="D9")]}
    _mock_all(recs)
    geo = _geo()
    geo.table["Ghost"] = km_offset(20.0, 73.78, 40, 0)                 # lookup resolves for shortlist...
    orig = geo.geocode_mandi
    geo.geocode_mandi = lambda m, d, s, allow_network=True: (            # ...but Ghost's market-level geocode fails
        GeoResult(precision="unknown") if m == "Ghost" else orig(m, d, s))
    a = _run(geocoder=geo)
    assert [c.mandi_name for c in a.candidates] == ["Near, D1"] and any("location unknown" in w for w in a.warnings)


@responses.activate
def test_other_variety_mandi_dropped_for_comparability(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    recs = {"Maharashtra": [FIXTURE_record(market="Near", district="D1"), FIXTURE_record(market="Mid", district="D2"),
                            FIXTURE_record(market="Far", district="D3", variety="Local")]}
    _mock_all(recs)
    a = _run()
    assert {c.mandi_name for c in a.candidates} == {"Near, D1", "Mid, D2"}
    assert any("Far, D3" in w and "selected variety/grade" in w for w in a.warnings)


@responses.activate
def test_closing_time_from_request_then_settings_and_invalid_ignored(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all()
    db.upsert_mandi_setting(mandi_id("Maharashtra", "D2", "Mid"), latest_arrival_local="13:00")
    req = FIXTURE_request(mandi_latest_arrival={mandi_id("Maharashtra", "D1", "Near"): "15:30",
                                                mandi_id("Maharashtra", "D3", "Far"): "25:99"})
    a = _run(req)
    by = {c.mandi_name: c.latest_arrival_local for c in a.candidates}
    assert by == {"Near, D1": "15:30", "Mid, D2": "13:00", "Far, D3": None}
    assert any("not HH:MM" in w for w in a.warnings)


@responses.activate
def test_district_centroid_location_is_warned(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all({"Maharashtra": [FIXTURE_record(market="Near", district="D1")]})
    geo = FakeGeo({"Near": (*km_offset(20.0, 73.78, 30, 0), "district_centroid")})
    a = _run(geocoder=geo)
    assert a.candidates[0].location_precision == "district_centroid" and any("DISTRICT centroid" in w for w in a.warnings)


@responses.activate
def test_routes_longer_than_max_distance_dropped(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all()
    a = _run(FIXTURE_request(max_distance_km=50))          # Near ~36 km road, Mid ~ 80, Far ~ 130
    by = {c.mandi_name: len(c.routes) for c in a.candidates}
    assert by.get("Near, D1") == 1 and by.get("Far, D3", 0) == 0


@responses.activate
def test_progress_callback_and_broken_callback(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all()
    seen = []
    a = _run(progress=seen.append)
    assert seen[0].startswith("Fetching") and seen[-1] == "Done" and a.candidates
    a2 = _run(progress=lambda m: 1 / 0)                       # must not break assembly
    assert a2.candidates


def test_naive_now_rejected():
    import pytest
    with pytest.raises(ValueError):
        assemble._assemble(FIXTURE_request(), FIXTURE_NOW.replace(tzinfo=None))


def test_option_id_convention_parser():
    assert assemble.mandi_ids_from_option_ids(["m1::r0::t", "m1~r1", "m2", "m1::x", ""]) == ["m1", "m2"]


@responses.activate
def test_refresh_for_monitoring_only_requested_mandis_and_pinned_variety(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    recs = {"Maharashtra": [FIXTURE_record(market="Near", district="D1"), FIXTURE_record(market="Near", district="D1", variety="Local", mo="1000", mn="900", mx="1100"),
                            FIXTURE_record(market="Mid", district="D2", variety="Local", mo="1100", mn="1000", mx="1200")]}
    _mock_all(recs)
    ids = [mandi_id("Maharashtra", "D1", "Near") + "::r0::x"]
    a = assemble._assemble(FIXTURE_request(), FIXTURE_NOW, geocoder=_geo(), only=assemble.mandi_ids_from_option_ids(ids),
                           variety="Local", grade="FAQ")
    assert [c.mandi_name for c in a.candidates] == ["Near, D1"]
    assert a.candidates[0].price.typical == 1000.0 and a.selected_variety == "Local"


@responses.activate
def test_refresh_missing_mandi_is_warned(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all({"Maharashtra": [FIXTURE_record(market="Near", district="D1")]})
    a = assemble._assemble(FIXTURE_request(), FIXTURE_NOW, geocoder=_geo(), only=["maharashtra__x__gone"])
    assert a.candidates == [] and any("no recent price record" in w for w in a.warnings)


def test_refresh_with_unparseable_ids():
    a = assemble.refresh_for_monitoring(FIXTURE_request(), [], FIXTURE_NOW)
    assert a.candidates == [] and any("no mandi ids" in w for w in a.warnings)


@responses.activate
def test_force_refresh_bypasses_price_cache(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    _mock_all({"Maharashtra": [FIXTURE_record(market="Near", district="D1")]})
    _run()
    n = sum(1 for c in responses.calls if "data.gov.in" in c.request.url)
    _run()
    assert sum(1 for c in responses.calls if "data.gov.in" in c.request.url) == n          # cached
    _run(force=True)
    assert sum(1 for c in responses.calls if "data.gov.in" in c.request.url) > n


def test_no_fixture_or_stub_leaks_into_app_code():
    """NN-1: app/ must not contain fixtures or hard-coded sample responses."""
    root = Path(__file__).resolve().parents[2] / "app"
    for p in root.rglob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "FIXTURE_" not in txt, p
        assert "dev_stub" not in txt or p.name == "guards.py", p


# ---------------- health ----------------
@responses.activate
def test_health_probe_marks_each_source(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", KEY)
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    responses.add(responses.GET, mandi.RESOURCE_URL, json={"records": [FIXTURE_record()]})
    responses.add(responses.GET, weather.FORECAST_URL, json={"hourly": {"time": []}})
    responses.add(responses.GET, weather.FLOOD_URL, json={"daily": {}})
    responses.add(responses.GET, re.compile(r"https://router\.project-osrm\.org/nearest/.*"), json={"code": "Ok"})
    responses.add(responses.GET, "https://geocoding-api.open-meteo.com/v1/search", json={})
    responses.add(responses.GET, "https://nominatim.openstreetmap.org/status.php", status=503)
    rows = {s.name: s for s in health.source_health(probe=True)}
    assert set(rows) == set(health.SOURCE_KINDS) - {"osrm_fallback"}
    assert rows["datagov_agmarknet"].ok and rows["osrm"].ok and rows["open_meteo_forecast"].ok
    assert rows["nominatim"].ok is False and "503" in rows["nominatim"].note
    assert rows["datagov_agmarknet"].kind.value == "observed" and rows["open_meteo_forecast"].kind.value == "forecast"
    assert "NOT real-time" in rows["datagov_agmarknet"].note


def test_health_probe_without_keys_is_explicit():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rm:
        rm.add(responses.GET, re.compile(r".*"), body=requests.ConnectionError("offline"))
        rows = {s.name: s for s in health.source_health(probe=True)}
    assert "DATAGOV_KEY" in rows["datagov_agmarknet"].note and rows["nominatim"].ok is False
    assert all(not s.ok for s in rows.values())


def test_health_passive_reads_source_calls_without_network():
    with db.get_conn() as c:
        c.execute("INSERT INTO source_calls(ts,source,url_redacted,http,latency_ms,ok,note) VALUES('t','osrm','u',200,120,1,NULL)")
    rows = {s.name: s for s in health.source_health(probe=False)}
    assert rows["osrm"].ok and rows["osrm"].latency_ms == 120 and rows["nominatim"].note == "no calls yet"
