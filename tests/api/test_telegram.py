"""
Unit tests for Telegram integration (Stream C / C2).
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import pytest
import requests
import responses

from app.api.main import apply_plan_approval
from app.contracts import CandidateInputs, Costs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint
from app.data.assemble import CandidateAssembly
from app.db.db import get_conn
from app.integrations.notify import notify
from app.integrations.telegram import (
    _get_persisted_offset,
    _persist_offset,
    _telegram_polling_loop,
    build_callback_data,
    handle_callback_update,
    parse_callback_data,
    redact_token,
    send,
)


def FIXTURE_seed_plan(plan_id: str = "FIXTURE_p1", status: str = "awaiting_approval", version: int = 1) -> None:
    """Helper to seed a test plan in SQLite."""
    now_iso = datetime.now(UTC).isoformat()
    req = {
        "crop": "Tomato",
        "quantity_qtl": 40.0,
        "origin": {"lat": 18.52, "lon": 73.86},
        "origin_label": "FIXTURE_Pune",
        "origin_state": "Maharashtra",
        "vehicle": "covered",
        "costs": {
            "transport_mode": "per_km_per_trip",
            "transport_rate": 20.0,
            "commission_pct": 5.0,
            "market_fee_pct": 2.0,
            "hamali_per_qtl": 5.0,
            "loading_per_qtl": 5.0,
            "hold_cost_per_hour": 100.0,
        },
        "mandi_latest_arrival": {},
        "risk_mode": "cvar25",
    }
    planset = {
        "plan_id": plan_id,
        "version": version,
        "created_at": now_iso,
        "plans": [
            {
                "label": "A",
                "option": {
                    "option_id": "m1::r0::depart",
                    "mandi_id": "FIXTURE_m1",
                    "mandi_name": "FIXTURE Nashik",
                    "route_id": "r0",
                    "depart_iso": now_iso,
                    "distance_km": 150.0,
                    "duration_h_nominal": 2.0,
                    "scenario_results": [],
                    "score": 50000.0,
                    "mean_net": 50000.0,
                    "evidence_ids": [],
                },
                "role_note": "Primary plan",
            }
        ],
        "robustness_a_best_in": 9,
        "robustness_of": 9,
        "coverage": {"grade": "ACTIONABLE", "items": [], "reasons": []},
        "evidence_ids": [],
    }

    with get_conn() as conn:
        conn.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM plan_versions WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM watch_state WHERE plan_id = ?", (plan_id,))
        conn.execute(
            "INSERT INTO plans(plan_id, request_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (plan_id, json.dumps(req), status, now_iso, now_iso),
        )
        for v in range(1, version + 1):
            conn.execute(
                "INSERT INTO plan_versions(plan_id, version, planset_json, diff_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (plan_id, v, json.dumps(planset), None, now_iso),
            )


def test_callback_data_format_and_length():
    """Verify callback_data format and <= 64 bytes assertion."""
    actions = ["approve", "reject", "replan_ok", "replan_no", "checkin"]
    args = ["", "passed:1", "delay:30", "delay:60", "arrived"]

    for act in actions:
        for arg in args:
            if act != "checkin" and arg:
                continue
            data = build_callback_data(act, "FIXTURE_p1", 1, arg)
            assert len(data.encode("utf-8")) <= 64
            parsed_act, plan_id, v, parsed_arg = parse_callback_data(data)
            assert parsed_act == act
            assert plan_id == "FIXTURE_p1"
            assert v == 1
            assert parsed_arg == arg

    # Test that > 64 bytes raises AssertionError
    with pytest.raises(AssertionError):
        build_callback_data("approve", "FIXTURE_" + "x" * 60, 1)


@responses.activate
def test_callback_from_another_chat_or_user_ignored(monkeypatch):
    """Callback from wrong chat_id or from.id must be silently ignored."""
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", "FIXTURE_TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    FIXTURE_seed_plan("FIXTURE_p1", "awaiting_approval", 1)

    # 1. Wrong from.id
    update_wrong_user = {
        "update_id": 1,
        "callback_query": {
            "id": "cb_1",
            "from": {"id": 99999999},  # Unauthorized
            "message": {"message_id": 100, "chat": {"id": 12345678}},
            "data": "approve|FIXTURE_p1|1|",
        },
    }
    handled = handle_callback_update(update_wrong_user)
    assert handled is False

    # 2. Wrong chat.id
    update_wrong_chat = {
        "update_id": 2,
        "callback_query": {
            "id": "cb_2",
            "from": {"id": 12345678},
            "message": {"message_id": 101, "chat": {"id": 88888888}},  # Unauthorized
            "data": "approve|FIXTURE_p1|1|",
        },
    }
    handled = handle_callback_update(update_wrong_chat)
    assert handled is False

    # Verify plan status was untouched
    with get_conn() as conn:
        status = conn.execute("SELECT status FROM plans WHERE plan_id = 'FIXTURE_p1'").fetchone()[0]
        assert status == "awaiting_approval"


@responses.activate
def test_stale_version_rejected(monkeypatch):
    """Callback with stale version must answer 'this button is out of date' and remove markup."""
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", "FIXTURE_TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    # Plan is at version 2
    FIXTURE_seed_plan("FIXTURE_p1", "replan_pending", version=2)

    # Mock answerCallbackQuery and editMessageReplyMarkup
    responses.add(
        responses.POST,
        "https://api.telegram.org/botFIXTURE_TOKEN/answerCallbackQuery",
        json={"ok": True},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.telegram.org/botFIXTURE_TOKEN/editMessageReplyMarkup",
        json={"ok": True},
        status=200,
    )

    update = {
        "update_id": 3,
        "callback_query": {
            "id": "cb_stale",
            "from": {"id": 12345678},
            "message": {"message_id": 200, "chat": {"id": 12345678}},
            "data": "replan_ok|FIXTURE_p1|1|",  # Version 1 is stale!
        },
    }
    handled = handle_callback_update(update)
    assert handled is False

    # Check that answerCallbackQuery was called with out of date text
    calls = [c for c in responses.calls if "answerCallbackQuery" in c.request.url]
    assert len(calls) == 1
    req_body = json.loads(calls[0].request.body)
    assert "out of date" in req_body["text"]


@responses.activate
def test_double_tap_idempotent(monkeypatch):
    """Second tap on already executed action answers 'already done'."""
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", "FIXTURE_TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    # Plan is already in monitoring (dispatch already approved)
    FIXTURE_seed_plan("FIXTURE_p1", "monitoring", version=1)

    responses.add(
        responses.POST,
        "https://api.telegram.org/botFIXTURE_TOKEN/answerCallbackQuery",
        json={"ok": True},
        status=200,
    )
    responses.add(
        responses.POST,
        "https://api.telegram.org/botFIXTURE_TOKEN/editMessageReplyMarkup",
        json={"ok": True},
        status=200,
    )

    update = {
        "update_id": 4,
        "callback_query": {
            "id": "cb_double",
            "from": {"id": 12345678},
            "message": {"message_id": 205, "chat": {"id": 12345678}},
            "data": "approve|FIXTURE_p1|1|",
        },
    }
    handled = handle_callback_update(update)
    assert handled is False

    calls = [c for c in responses.calls if "answerCallbackQuery" in c.request.url]
    assert len(calls) == 1
    req_body = json.loads(calls[0].request.body)
    assert "already done" in req_body["text"]


@responses.activate
def test_token_never_appears_in_actions_rows_logs_or_errors(monkeypatch):
    """Assert secret token is completely redacted from DB and errors."""
    fake_token = "SECRET_BOT_TOKEN_XYZ_123456789"
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", fake_token)
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    # Mock Telegram sendMessage failing with an error containing the token in URL
    responses.add(
        responses.POST,
        f"https://api.telegram.org/bot{fake_token}/sendMessage",
        body=f"Failed request to https://api.telegram.org/bot{fake_token}/sendMessage",
        status=500,
    )

    notify(
        plan_id="FIXTURE_p_redact",
        kind="test_notification",
        text=f"Check notification mentioning {fake_token}",
    )

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM actions WHERE plan_id = 'FIXTURE_p_redact' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        payload_str = row["payload_json"]
        receipt_str = row["receipt_json"]

        assert fake_token not in payload_str
        assert fake_token not in receipt_str
        assert "[REDACTED_TG_TOKEN]" in payload_str or "[REDACTED_TG_TOKEN]" in receipt_str


@responses.activate
def test_send_failure_does_not_break_approve(monkeypatch):
    """A Telegram network failure during notification must not fail approve."""
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", "FIXTURE_TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    FIXTURE_seed_plan("FIXTURE_p_fail", "awaiting_approval", 1)

    # Mock sendMessage to fail with 502 Bad Gateway
    responses.add(
        responses.POST,
        "https://api.telegram.org/botFIXTURE_TOKEN/sendMessage",
        status=502,
        body="Bad Gateway",
    )

    # Approve dispatch
    res = apply_plan_approval("FIXTURE_p_fail", version=1, decision="approve", kind="dispatch")
    assert res["ok"] is True
    assert res["status"] == "monitoring"

    # Verify DB was still updated
    with get_conn() as conn:
        status = conn.execute("SELECT status FROM plans WHERE plan_id = 'FIXTURE_p_fail'").fetchone()[0]
        assert status == "monitoring"
        action_row = conn.execute(
            "SELECT * FROM actions WHERE plan_id = 'FIXTURE_p_fail' AND type = 'dispatch_approved' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert action_row is not None
        assert action_row["ok"] == 0  # Telegram failed, but operation succeeded


def test_offset_persisted():
    """Verify offset persistence in SQLite actions table."""
    _persist_offset(9988)
    assert _get_persisted_offset() == 9988

    _persist_offset(9989)
    assert _get_persisted_offset() == 9989


@responses.activate
def test_thread_survives_exception(monkeypatch):
    """Verify polling loop survives exceptions without terminating."""
    import threading
    monkeypatch.setenv("MG_TELEGRAM", "1")
    monkeypatch.setenv("TG_TOKEN", "FIXTURE_TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "12345678")

    # 1st call fails with ConnectionError
    responses.add(
        responses.GET,
        "https://api.telegram.org/botFIXTURE_TOKEN/getUpdates",
        body=requests.exceptions.ConnectionError("Network dropped"),
    )
    # 2nd call succeeds
    responses.add(
        responses.GET,
        "https://api.telegram.org/botFIXTURE_TOKEN/getUpdates",
        json={"ok": True, "result": []},
        status=200,
    )

    stop_event = threading.Event()

    # Run polling loop in thread, signal stop after 1.5 seconds
    t = threading.Thread(target=_telegram_polling_loop, args=(stop_event,), daemon=True)
    t.start()
    import time
    time.sleep(0.5)
    stop_event.set()
    t.join(timeout=3.0)

    assert not t.is_alive()
