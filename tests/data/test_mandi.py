"""A3 tests (FIXTURE data via `responses`; none of this is observed data)."""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import responses

from app.contracts import LatLon
from app.data import mandi
from app.data.geo import mandi_id
from app.data.geocode import GeoResult
from app.db import db
from tests.data.fixtures import FIXTURE_NOW, FIXTURE_record

TODAY = date(2026, 10, 30)


def _norm(**kw):
    return mandi.normalize_record(FIXTURE_record(**kw), "2026-10-30T04:00:00+00:00", TODAY)


def test_keys_lowercased_and_aliases_and_date_parsed():
    raw = {"State": "Maharashtra", "District": "Nashik", "Market": "Lasalgaon", "Commodity": "Tomato",
           "Variety": "Hybrid", "Grade": "FAQ", "Arrival_Date": "29/10/2026",
           "Min_x0020_Price": "1,500", "Max_x0020_Price": "2500", "Modal_x0020_Price": "2000"}
    r = mandi.normalize_record(raw, "f", TODAY)
    assert r and not r.suspect and r.arrival_date == date(2026, 10, 29)
    assert (r.min_price, r.modal_price, r.max_price) == (1500.0, 2000.0, 2500.0)
    assert r.mandi_id == mandi_id("Maharashtra", "Nashik", "Lasalgaon")


def test_date_formats():
    assert mandi.parse_date("01/02/2026") == date(2026, 2, 1)          # dd/mm, never mm/dd
    assert mandi.parse_date("2026-02-01") == date(2026, 2, 1)
    assert mandi.parse_date("garbage") is None


def test_suspect_records_are_flagged():
    assert _norm(mn="3000", mo="2000", mx="2500").suspect                # min > modal
    assert _norm(mn="0").suspect and _norm(mo="NA").suspect
    assert _norm(date="31/12/2026").suspect                              # future
    assert _norm(date="not a date").suspect
    assert not _norm().suspect


def test_unidentified_rows_dropped():
    assert mandi.normalize_record({"state": "X", "market": "Y"}, "f", TODAY) is None


def test_select_variety_grade_is_deterministic_and_alphabetical_on_tie():
    recs = [_norm(market="A", variety="Local", grade="FAQ"), _norm(market="B", variety="Hybrid", grade="FAQ"),
            _norm(market="C", variety="Hybrid", grade="FAQ"), _norm(market="D", variety="Local", grade="FAQ")]
    # tie (2 mandis each) -> alphabetical -> Hybrid
    assert mandi.select_variety_grade(recs) == ("Hybrid", "FAQ")
    assert mandi.select_variety_grade(list(reversed(recs))) == ("Hybrid", "FAQ")
    recs.append(_norm(market="E", variety="Local", grade="FAQ"))         # now Local has 3
    assert mandi.select_variety_grade(recs) == ("Local", "FAQ")
    # same mandi repeated does not count twice
    assert mandi.select_variety_grade([_norm(market="A", variety="X"), _norm(market="A", variety="X"),
                                       _norm(market="B", variety="Y"), _norm(market="C", variety="Y")]) == ("Y", "FAQ")
    assert mandi.select_variety_grade([]) == (None, None)


def test_select_ignores_suspect_records():
    recs = [_norm(market="A", variety="Bad", mn="0"), _norm(market="B", variety="Bad", mn="0"),
            _norm(market="C", variety="Good")]
    assert mandi.select_variety_grade(recs)[0] == "Good"


def test_price_band_uses_one_newest_record_and_creates_observed_evidence():
    old = _norm(date="27/10/2026", mn="900", mo="1000", mx="1100")
    new = _norm(date="29/10/2026", mn="1500", mo="2000", mx="2500")
    pb = mandi.build_price_band([old, new], FIXTURE_NOW)
    assert (pb.low, pb.typical, pb.high) == (1500.0, 2000.0, 2500.0) and pb.record_date == "2026-10-29"
    assert pb.unit_verified is False and len(pb.evidence_ids) == 3
    evs = [db.get_evidence(i) for i in pb.evidence_ids]
    assert all(e["kind"] == "observed" and e["as_of"] == "2026-10-29" for e in evs)
    assert [e["value"] for e in evs] == [1500.0, 2000.0, 2500.0]
    assert all("UNVERIFIED" in e["unit"] for e in evs) and not any(e["stale"] for e in evs)
    assert mandi.build_price_band([_norm(mn="0")], FIXTURE_NOW) is None


def test_price_band_stale_flag_beyond_degrade_days_and_unit_verified_env(monkeypatch):
    monkeypatch.setenv("MG_PRICE_UNIT_VERIFIED", "1")
    pb = mandi.build_price_band([_norm(date="25/10/2026")], FIXTURE_NOW)          # 5 days old > 3
    evs = [db.get_evidence(i) for i in pb.evidence_ids]
    assert pb.unit_verified and all(e["stale"] and e["unit"] == "INR/quintal" for e in evs)


def _cb_factory(per_state: dict, calls: list):
    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        calls.append(q)
        st = (q.get("filters[state.keyword]") or q.get("filters[state]") or [None])[0]
        rows = per_state.get(st, [])
        return 200, {}, json.dumps({"total": len(rows), "count": len(rows), "records": rows})
    return cb


@responses.activate
def test_fetch_normalises_persists_and_reports_status():
    calls: list = []
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=_cb_factory(
        {"Maharashtra": [FIXTURE_record(), FIXTURE_record(market="Pune", mn="3000")],      # 2nd is suspect
         "Gujarat": [FIXTURE_record(state="Gujarat", district="Surat", market="Surat")]}, calls))
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra", "Gujarat"], "KEY123456", FIXTURE_NOW)
    assert mf.status.ok and mf.status.as_of == "2026-10-29" and mf.status.kind.value == "observed"
    assert len(mf.records) == 3 and sum(r.suspect for r in mf.records) == 1
    assert any("suspect" in w for w in mf.warnings)
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM price_records").fetchone()[0] == 3
        assert c.execute("SELECT unit_verified FROM price_records LIMIT 1").fetchone()[0] == 0
    # raw key never stored
    with db.get_conn() as c:
        assert "KEY123456" not in "\n".join(c.iterdump())


@responses.activate
def test_cached_fetch_is_not_persisted_twice():
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=_cb_factory({"Maharashtra": [FIXTURE_record()]}, []))
    mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    n_calls = len(responses.calls)
    mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert len(responses.calls) == n_calls
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM price_records").fetchone()[0] == 1


@responses.activate
def test_state_filter_style_falls_back_and_is_remembered():
    calls: list = []

    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        calls.append(q)
        rows = [FIXTURE_record()] if "filters[state]" in q else []          # only the plain style works
        return 200, {}, json.dumps({"total": len(rows), "records": rows})
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=cb)
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert len(mf.records) == 1 and mandi._sticky_filter_style == "state"
    assert "filters[state.keyword]" in calls[0] and "filters[state]" in calls[1]


@responses.activate
def test_ignored_server_filter_is_caught_client_side():
    rows = [FIXTURE_record(), FIXTURE_record(commodity="Onion", market="X"),
            FIXTURE_record(state="Kerala", district="K", market="K")]
    responses.add(responses.GET, mandi.RESOURCE_URL, json={"total": 3, "records": rows})
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert [r.market for r in mf.records] == ["Nashik"]
    assert any("did not fully apply" in w for w in mf.warnings)


@responses.activate
def test_pagination_follows_rows_actually_returned():
    pages = {0: [FIXTURE_record(market=f"M{i}") for i in range(2)], 2: [FIXTURE_record(market=f"M{i}") for i in range(2, 3)]}

    def cb(request):
        off = int(parse_qs(urlparse(request.url).query)["offset"][0])
        return 200, {}, json.dumps({"total": 3, "records": pages.get(off, [])})   # server caps page at 2 < limit 500
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=cb)
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert len(mf.records) == 3


@responses.activate
def test_failure_returns_empty_and_status_not_ok():
    responses.add(responses.GET, mandi.RESOURCE_URL, status=500)
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert mf.records == [] and mf.status.ok is False and "500" in mf.status.note


@responses.activate
def test_html_or_wrong_shape_is_failure():
    responses.add(responses.GET, mandi.RESOURCE_URL, json={"status": "error", "message": "limit"})
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert mf.records == [] and not mf.status.ok and "unexpected response shape" in mf.status.note


@responses.activate
def test_partial_state_failure_keeps_other_states():
    def cb(request):
        q = parse_qs(urlparse(request.url).query)
        st = (q.get("filters[state.keyword]") or [""])[0]
        if st == "Gujarat":
            return 500, {}, "x"
        return 200, {}, json.dumps({"total": 1, "records": [FIXTURE_record()]})
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=cb)
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra", "Gujarat"], "K", FIXTURE_NOW)
    assert len(mf.records) == 1 and not mf.status.ok and any("Gujarat" in w for w in mf.warnings)


class FakeGeo:
    """FIXTURE geocoder: returns preset coordinates; counts network-style district calls."""
    def __init__(self, local=None, district=None):
        self.local, self.district, self.district_calls = local or {}, district or {}, 0

    def lookup_local(self, market, district, state):
        return self.local.get(market)

    def geocode_district(self, district, state, allow_network=True):
        self.district_calls += 1
        g = self.district.get(district)
        if g is None or (not allow_network and not g.cached):      # mimic real geocoder: no network => cache only
            return GeoResult(precision="unknown", source="local-miss")
        return g


def _g(lat, lon, prec="market", cached=True):
    return GeoResult(lat=lat, lon=lon, precision=prec, cached=cached)


def test_shortlist_nearest_first_filters_distance_and_skips_unlocated(monkeypatch):
    recs = [_norm(market="Near", district="D1"), _norm(market="Mid", district="D2"),
            _norm(market="Far", district="D3"), _norm(market="Nowhere", district="D4")]
    geo = FakeGeo(local={"Near": _g(20.05, 73.78), "Mid": _g(20.60, 73.78), "Far": _g(25.0, 80.0)})
    ids = mandi.shortlist(recs, LatLon(lat=20.0, lon=73.78), 150, 8, geo)
    assert ids == [mandi_id("Maharashtra", "D1", "Near"), mandi_id("Maharashtra", "D2", "Mid")]
    assert mandi.shortlist(recs, LatLon(lat=20.0, lon=73.78), 150, 1, geo) == ids[:1]


def test_shortlist_uses_district_centroid_with_budget():
    recs = [_norm(market="M1", district="DA"), _norm(market="M2", district="DB"), _norm(market="M3", district="DA")]
    geo = FakeGeo(district={"DA": _g(20.1, 73.8, "district_centroid", cached=False),
                            "DB": _g(20.2, 73.9, "district_centroid", cached=False)})
    ids = mandi.shortlist(recs, LatLon(lat=20.0, lon=73.78), 200, 8, geo, net_budget=1)
    # budget 1: DA resolved over network (M1, M3 share it), DB not allowed -> unknown -> skipped
    assert sorted(ids) == sorted([mandi_id("Maharashtra", "DA", "M1"), mandi_id("Maharashtra", "DA", "M3")])
    assert geo.district_calls == 2        # DA once (cached per call) + DB once


def test_shortlist_is_deterministic_on_ties():
    recs = [_norm(market="B", district="D"), _norm(market="A", district="D")]
    geo = FakeGeo(local={"A": _g(20.1, 73.8), "B": _g(20.1, 73.8)})
    assert mandi.shortlist(recs, LatLon(lat=20.0, lon=73.78), 200, 8, geo) == [
        mandi_id("Maharashtra", "D", "A"), mandi_id("Maharashtra", "D", "B")]


@responses.activate
def test_stale_cache_after_failed_fetch_marks_price_evidence_stale():
    from datetime import datetime, timedelta, timezone
    from app.data import http
    t = [datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)]
    http.set_time_hooks(utcnow=lambda: t[0])
    responses.add_callback(responses.GET, mandi.RESOURCE_URL, callback=_cb_factory({"Maharashtra": [FIXTURE_record()]}, []))
    mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    t[0] += timedelta(hours=3)                                      # past 60-min TTL, inside 7-day stale cap
    responses.replace(responses.GET, mandi.RESOURCE_URL, status=500)
    mf = mandi.fetch_mandi_records("Tomato", ["Maharashtra"], "K", FIXTURE_NOW)
    assert len(mf.records) == 1 and mf.records[0].served_stale and any("CACHED" in w for w in mf.warnings)
    pb = mandi.build_price_band(mf.records, FIXTURE_NOW)             # record is only 1 day old, yet flagged stale
    assert all(db.get_evidence(i)["stale"] for i in pb.evidence_ids)


def test_select_grade_most_mandis_then_alphabetical_and_wildcard_grouping():
    recs = [_norm(market="A", grade="Local", variety="Other"), _norm(market="B", grade="Local", variety="Local"),
            _norm(market="C", grade="FAQ"), _norm(market="D", grade="Medium")]
    assert mandi.select_grade(recs) == "Local"
    tie = [_norm(market="A", grade="Medium"), _norm(market="B", grade="FAQ")]
    assert mandi.select_grade(tie) == "FAQ" and mandi.select_grade([]) is None
    g = mandi.group_by_mandi(recs, None, "Local")                      # variety wildcard
    assert set(g) == {mandi_id("Maharashtra", "Nashik", "A"), mandi_id("Maharashtra", "Nashik", "B")}
    assert len(mandi.group_by_mandi(recs, "Other", "Local")) == 1      # exact pin still works
