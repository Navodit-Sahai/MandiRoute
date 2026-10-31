"""A2 tests: cache, limiter, failure object, retry, redaction, evidence. All HTTP mocked (FIXTURE)."""
from __future__ import annotations

import re
import threading
from datetime import timedelta

import pytest
import requests
import responses

from app.contracts import Kind
from app.data import http
from app.db import db

URL = "https://example.test/api"


def _dump_db() -> str:
    with db.get_conn() as c:
        return "\n".join(c.iterdump())


@responses.activate
def test_cache_hit_avoids_network():
    responses.add(responses.GET, URL, json={"a": 1})
    r1 = http.request_json("t", "GET", URL, {"x": 1}, ttl=60, cache_key="k")
    r2 = http.request_json("t", "GET", URL, {"x": 1}, ttl=60, cache_key="k")
    assert len(responses.calls) == 1
    assert r1.ok and not r1.from_cache and r2.ok and r2.from_cache and r2.data == {"a": 1}
    assert r2.fetched_at == r1.fetched_at            # cached data keeps its ORIGINAL fetch time


@responses.activate
def test_expired_cache_refetches():
    from datetime import datetime, timezone
    t = [datetime(2026, 10, 30, tzinfo=timezone.utc)]
    http.set_time_hooks(utcnow=lambda: t[0])
    responses.add(responses.GET, URL, json={"n": 1})
    http.request_json("t", "GET", URL, ttl=60, cache_key="k")
    t[0] += timedelta(seconds=120)
    responses.replace(responses.GET, URL, json={"n": 2})
    r = http.request_json("t", "GET", URL, ttl=60, cache_key="k")
    assert r.data == {"n": 2} and not r.from_cache and len(responses.calls) == 2


@responses.activate
def test_force_bypasses_cache():
    responses.add(responses.GET, URL, json={"a": 1})
    http.request_json("t", "GET", URL, ttl=3600, cache_key="k")
    r = http.request_json("t", "GET", URL, ttl=3600, cache_key="k", force=True)
    assert not r.from_cache and len(responses.calls) == 2


def test_limiter_spacing_at_least_one_second(clock):
    lim = http.RateLimiter(1.0)
    stamps = []
    for _ in range(4):
        lim.wait()
        stamps.append(clock.t)
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))


@responses.activate
def test_request_json_respects_limiter(clock):
    stamps = []

    def cb(req):
        stamps.append(clock.t)
        return 200, {}, "{}"
    responses.add_callback(responses.GET, URL, callback=cb)
    lim = http.RateLimiter(1.0)
    for i in range(3):
        http.request_json("t", "GET", URL, {"i": i}, rate_limiter=lim)
    assert [round(b - a, 6) for a, b in zip(stamps, stamps[1:])] == [1.0, 1.0]


@responses.activate
def test_failure_returns_error_object_after_one_retry(clock):
    responses.add(responses.GET, URL, status=503, body="busy")
    r = http.request_json("t", "GET", URL)
    assert r.ok is False and r.data is None and "503" in r.error and len(responses.calls) == 2
    assert clock.sleeps == [http.BACKOFF_S]
    with db.get_conn() as c:
        rows = c.execute("SELECT ok,http FROM source_calls").fetchall()
    assert [(x["ok"], x["http"]) for x in rows] == [(0, 503), (0, 503)]


@responses.activate
def test_4xx_is_not_retried():
    responses.add(responses.GET, URL, status=404, body="nope")
    r = http.request_json("t", "GET", URL)
    assert not r.ok and len(responses.calls) == 1


@responses.activate
def test_invalid_json_is_failure_not_exception():
    responses.add(responses.GET, URL, body="<html>down</html>", status=200)
    r = http.request_json("t", "GET", URL)
    assert not r.ok and "not valid JSON" in r.error


@responses.activate
def test_check_rejection_is_failure_and_not_cached():
    responses.add(responses.GET, URL, json={"code": "NoRoute"})
    chk = lambda d: None if d.get("code") == "Ok" else "bad code"      # noqa: E731
    r1 = http.request_json("t", "GET", URL, cache_key="k", ttl=60, check=chk)
    r2 = http.request_json("t", "GET", URL, cache_key="k", ttl=60, check=chk)
    assert not r1.ok and not r2.ok and len(responses.calls) == 2


@responses.activate
def test_stale_fallback_only_when_allowed():
    from datetime import datetime, timezone
    t = [datetime(2026, 10, 30, tzinfo=timezone.utc)]
    http.set_time_hooks(utcnow=lambda: t[0])
    responses.add(responses.GET, URL, json={"v": 1})
    first = http.request_json("t", "GET", URL, ttl=60, cache_key="k")
    t[0] += timedelta(hours=2)
    responses.replace(responses.GET, URL, status=500)
    r_no = http.request_json("t", "GET", URL, ttl=60, cache_key="k")
    r_yes = http.request_json("t", "GET", URL, ttl=60, cache_key="k", max_stale_s=3 * 3600)
    r_old = http.request_json("t", "GET", URL, ttl=60, cache_key="k", max_stale_s=3600)
    assert not r_no.ok
    assert r_yes.ok and r_yes.stale and r_yes.data == {"v": 1} and r_yes.fetched_at == first.fetched_at
    assert not r_old.ok                                   # older than max_stale_s


@responses.activate
def test_secrets_never_reach_db_or_error_text(monkeypatch):
    monkeypatch.setenv("DATAGOV_KEY", "ENVSECRET99999")
    responses.add(responses.GET, URL, body=requests.ConnectionError(
        "HTTPSConnectionPool: Max retries exceeded with url: /x?api-key=PARAMSECRET1234&format=json ENVSECRET99999"))
    r = http.request_json("t", "GET", URL, {"api-key": "PARAMSECRET1234", "format": "json"})
    assert not r.ok
    blob = r.error + r.url_redacted + _dump_db()
    assert "PARAMSECRET1234" not in blob and "ENVSECRET99999" not in blob
    assert "api-key=***" in r.url_redacted


@responses.activate
def test_secret_in_response_snippet_is_redacted():
    responses.add(responses.GET, URL, status=400, body="bad request key=TOPSECRETVALUE end")
    r = http.request_json("t", "GET", URL, {"key": "TOPSECRETVALUE"})
    assert "TOPSECRETVALUE" not in r.error


def test_evidence_persisted_and_roundtrips():
    ev = http.make_evidence(Kind.OBSERVED, "Low price", 1500.0, "INR/quintal", "datagov", "2026-10-29",
                            http.iso_utc(), "note")
    assert re.fullmatch(r"ev_[0-9a-f]{8}", ev.id)
    row = db.get_evidence(ev.id)
    assert row["value"] == 1500.0 and row["kind"] == "observed" and row["stale"] is False
    ev2 = http.make_evidence(Kind.FORECAST, "x", None, None, "s", None, http.iso_utc())
    assert db.get_evidence(ev2.id)["value"] is None
    ev3 = http.make_evidence(Kind.PROXY, "nan", float("nan"), None, "s", None, http.iso_utc())
    assert ev3.value is None and "non-finite" in ev3.note


def test_collector_gathers_evidence_and_warnings():
    with http.collect() as col:
        http.make_evidence(Kind.COMPUTED, "a", 1.0, None, "s", None, http.iso_utc())
        http.add_warning("w1")
        http.add_warning("w1")
    assert len(col.evidence) == 1 and col.warnings == ["w1"]
    http.make_evidence(Kind.COMPUTED, "outside", 1.0, None, "s", None, http.iso_utc())   # no collector: no error


def test_db_init_is_idempotent_and_threadsafe():
    db.init_db(); db.init_db()
    errs = []

    def work():
        try:
            for _ in range(25):
                http.make_evidence(Kind.COMPUTED, "t", 1.0, None, "s", None, http.iso_utc())
        except Exception as e:           # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    with db.get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert not errs and n == 200


def test_mandi_settings_upsert_keeps_other_fields():
    db.upsert_mandi_setting("m1", latest_arrival_local="14:30")
    db.upsert_mandi_setting("m1", lat=19.0, lon=73.0)
    s = db.get_mandi_setting("m1")
    assert s["latest_arrival_local"] == "14:30" and s["lat"] == 19.0


def test_memory_db_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    with pytest.raises(RuntimeError):
        db.connect()


@responses.activate
def test_identifying_user_agent_is_sent_and_caller_can_override(monkeypatch):
    responses.add(responses.GET, URL, json={})
    http.request_json("t", "GET", URL)
    assert responses.calls[0].request.headers["User-Agent"] == "MandiGuard/0.1"
    monkeypatch.setenv("MG_CONTACT_EMAIL", "a@b.org")
    http.request_json("t", "GET", URL)
    assert responses.calls[1].request.headers["User-Agent"] == "MandiGuard/0.1 (a@b.org)"
    http.request_json("t", "GET", URL, headers={"User-Agent": "custom"})
    assert responses.calls[2].request.headers["User-Agent"] == "custom"
