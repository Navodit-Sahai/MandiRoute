"""A4 tests (FIXTURE responses; no real geocodes)."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import requests
import responses

from app.data import geocode
from app.data.geo import mandi_id
from app.db import db


def _nom(lat, lon, display, state="Maharashtra"):
    return {"lat": str(lat), "lon": str(lon), "display_name": display, "address": {"state": state}}


def _om(name, lat, lon, admin1="Maharashtra", admin2="Nashik", cc="IN"):
    return {"name": name, "latitude": lat, "longitude": lon, "admin1": admin1, "admin2": admin2, "country_code": cc}


def _csv(tmp_path, monkeypatch, rows):
    p = tmp_path / "c.csv"
    p.write_text("mandi_id,market,district,state,lat,lon,precision,verified,source,note\n" + "\n".join(rows) + "\n")
    monkeypatch.setenv("MG_COORDS_CSV", str(p))


def test_manual_csv_verified_rows_only_and_bbox_check(tmp_path, monkeypatch):
    _csv(tmp_path, monkeypatch, [
        ",Lasalgaon,Nashik,Maharashtra,20.14,74.23,market,1,manual,ok",
        ",Pune,Pune,Maharashtra,18.52,73.85,market,0,manual,NOT verified",
        ",Swapped,Nashik,Maharashtra,74.23,20.14,market,1,manual,swapped lat/lon",
    ])
    g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra", allow_network=False)
    assert g.resolved and g.precision == "market" and g.source == "manual_csv" and (g.lat, g.lon) == (20.14, 74.23)
    assert not geocode.geocode_mandi("Pune", "Pune", "Maharashtra", allow_network=False).resolved
    assert not geocode.geocode_mandi("Swapped", "Nashik", "Maharashtra", allow_network=False).resolved


def test_mandi_settings_table_is_used_as_manual_source():
    mid = mandi_id("Maharashtra", "Nashik", "Niphad")
    db.upsert_mandi_setting(mid, lat=20.08, lon=74.11, precision="market")
    g = geocode.geocode_mandi("Niphad", "Nashik", "Maharashtra", allow_network=False)
    assert g.resolved and g.source == "mandi_settings"


@responses.activate
def test_nominatim_skipped_without_contact_then_open_meteo_used():
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [_om("Lasalgaon", 20.14, 74.23)]})
    with __import__("app.data.http", fromlist=["x"]).collect() as col:
        g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert g.resolved and g.precision == "market" and g.source == "open_meteo_geocoding"
    assert not any("nominatim" in c.request.url for c in responses.calls)
    assert any("MG_CONTACT_EMAIL" in w for w in col.warnings)


@responses.activate
def test_nominatim_sends_identifying_user_agent(monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "team@example.org")
    responses.add(responses.GET, geocode.NOMINATIM_URL,
                  json=[_nom(20.14, 74.23, "Lasalgaon, Niphad, Nashik, Maharashtra, India")])
    g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert g.resolved and g.source == "nominatim" and g.precision == "market"
    ua = responses.calls[0].request.headers["User-Agent"]
    assert "MandiGuard" in ua and "team@example.org" in ua


@responses.activate
def test_wrong_state_or_foreign_hit_is_discarded(monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    responses.add(responses.GET, geocode.NOMINATIM_URL, json=[
        _nom(12.97, 77.59, "Lasalgaon, Bengaluru, Karnataka, India", state="Karnataka"),        # other state
        _nom(40.0, 10.0, "Lasalgaon, Nashik, Maharashtra, Europe", state="Maharashtra")])       # outside India
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [
        _om("Lasalgaon", 12.9, 77.5, admin1="Karnataka", admin2="Bengaluru"),
        _om("Lasalgaon", 41.0, 12.0, cc="IT")]})
    # district fallback also finds nothing valid
    g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert g.precision == "unknown" and g.lat is None and g.lon is None      # never invented


@responses.activate
def test_same_state_but_wrong_district_is_not_market_precision(monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    responses.add(responses.GET, geocode.NOMINATIM_URL, json=[
        _nom(18.5, 73.8, "Lasalgaon, Pune, Maharashtra, India")])                  # same state, other district
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [
        _om("Nashik", 19.99, 73.79, admin2="Nashik")]})
    g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert g.precision == "district_centroid" and g.display_name.startswith("Nashik")


@responses.activate
def test_precision_tags_market_vs_district_vs_unknown():
    def cb(request):
        name = parse_qs(urlparse(request.url).query)["name"][0]
        if name == "Lasalgaon":
            return 200, {}, json.dumps({"results": [_om("Lasalgaon", 20.14, 74.23)]})
        if name == "Nashik":
            return 200, {}, json.dumps({"results": [_om("Nashik", 19.99, 73.79)]})
        return 200, {}, json.dumps({})
    responses.add_callback(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, callback=cb)
    assert geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra").precision == "market"
    assert geocode.geocode_mandi("Tiny Village", "Nashik", "Maharashtra").precision == "district_centroid"
    assert geocode.geocode_mandi("Whatever", "Atlantis", "Maharashtra").precision == "unknown"


@responses.activate
def test_cache_means_second_lookup_needs_no_network():
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [_om("Lasalgaon", 20.14, 74.23)]})
    geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    n = len(responses.calls)
    g2 = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert len(responses.calls) == n and g2.cached and g2.resolved


@responses.activate
def test_network_outage_is_not_cached_as_not_found():
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, body=requests.ConnectionError("down"))
    g1 = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert g1.precision == "unknown" and g1.had_error
    responses.replace(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [_om("Lasalgaon", 20.14, 74.23)]})
    assert geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra").resolved       # retried, not stuck on "unknown"


@responses.activate
def test_genuine_not_found_is_negative_cached_then_expires():
    from datetime import datetime, timedelta, timezone
    t = [datetime(2026, 10, 30, tzinfo=timezone.utc)]
    __import__("app.data.http", fromlist=["x"]).set_time_hooks(utcnow=lambda: t[0])
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={})
    geocode.geocode_mandi("Ghost", "Nashik", "Maharashtra")
    n = len(responses.calls)
    geocode.geocode_mandi("Ghost", "Nashik", "Maharashtra")
    assert len(responses.calls) == n                                   # negative cache hit
    t[0] += timedelta(days=8)
    geocode.geocode_mandi("Ghost", "Nashik", "Maharashtra")
    assert len(responses.calls) > n                                    # expired -> retried


@responses.activate
def test_nominatim_calls_are_spaced_by_the_limiter(clock, monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    stamps = []

    def cb(request):
        stamps.append(clock.t)
        return 200, {}, json.dumps([_nom(20.14, 74.23, "X, Nashik, Maharashtra, India")])
    responses.add_callback(responses.GET, geocode.NOMINATIM_URL, callback=cb)
    for m in ("A", "B", "C"):
        geocode.geocode_mandi(m, "Nashik", "Maharashtra")
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:])) and len(stamps) == 3


def test_state_aliases_match():
    from app.data.geo import same_state, state_in_text
    assert same_state("Chattisgarh", "Chhattisgarh") and same_state("NCT of Delhi", "Delhi")
    assert same_state("Keralam", "Kerala") and same_state("kerala", "KERALAM")
    assert not same_state("Goa", "Karnataka") and not same_state("", "")
    assert state_in_text("Maharashtra", "Pune, Maharashtra, India") and not state_in_text("Goa", "Panaji, Gao")


def test_clean_market_name():
    from app.data.geo import clean_market_name
    assert clean_market_name("Pimpalgaon Baswant(Niphad)") == "Pimpalgaon Baswant"
    assert clean_market_name("Lasalgaon APMC") == "Lasalgaon"


@responses.activate
def test_bracket_hint_is_queried_first_and_generic_brackets_are_not(monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    qs = []

    def cb(request):
        q = parse_qs(urlparse(request.url).query)["q"][0]
        qs.append(q)
        return 200, {}, json.dumps([_nom(18.67, 73.85, "Moshi, Pimpri-Chinchwad, Pune, Maharashtra, India")])
    responses.add_callback(responses.GET, geocode.NOMINATIM_URL, callback=cb)
    g = geocode.geocode_mandi("Pune(Moshi) ", "Pune", "Maharashtra")
    assert qs[0].startswith("Moshi,") and g.precision == "market" and abs(g.lat - 18.67) < 1e-9
    from app.data.geo import bracket_hint
    assert bracket_hint("Perundurai(Uzhavar Sandhai )") is None and bracket_hint("Khed(Chakan)") == "Chakan"


@responses.activate
def test_market_name_equal_to_district_is_downgraded_to_district_centroid(monkeypatch):
    monkeypatch.setenv("MG_CONTACT_EMAIL", "t@e.org")
    responses.add(responses.GET, geocode.NOMINATIM_URL, json=[_nom(18.52, 73.85, "Pune, Pune District, Maharashtra, India")])
    g = geocode.geocode_mandi("Pune", "Pune", "Maharashtra")
    assert g.precision == "district_centroid" and "not the yard" in g.display_name


@responses.activate
def test_refresh_ignores_cache_but_not_manual_rows(tmp_path, monkeypatch):
    responses.add(responses.GET, geocode.OPEN_METEO_GEOCODE_URL, json={"results": [_om("Lasalgaon", 20.14, 74.23)]})
    geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    n = len(responses.calls)
    geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra")
    assert len(responses.calls) == n
    geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra", refresh=True)
    assert len(responses.calls) > n
    _csv(tmp_path, monkeypatch, [",Lasalgaon,Nashik,Maharashtra,20.5,74.5,market,1,manual,"])
    g = geocode.geocode_mandi("Lasalgaon", "Nashik", "Maharashtra", refresh=True)
    assert g.source == "manual_csv"
