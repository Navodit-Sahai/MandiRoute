"""
Telegram integration (Stream C).

Provides:
- send(chat_id, text, buttons) with inline keyboard, retry, timeout, token redaction
- callback_data building and parsing (strictly <= 64 bytes)
- background long-polling daemon thread for button taps (lifespan managed)
- security checks (from.id and chat.id matching TG_CHAT_ID)
- version checks, idempotency, internal dispatch without HTTP self-calls
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
import re
import threading
import time
from typing import Any

import requests

from app.db.db import get_conn

logger = logging.getLogger(__name__)

_STOP_EVENT = threading.Event()
_POLLING_THREAD: threading.Thread | None = None
_GLOBAL_LOCK = threading.Lock()


def get_token() -> str:
    """Return TG_TOKEN from environment, or empty string."""
    return os.environ.get("TG_TOKEN", "").strip()


def get_chat_id() -> str:
    """Return TG_CHAT_ID from environment, or empty string."""
    return os.environ.get("TG_CHAT_ID", "").strip()


def redact_token(text: str) -> str:
    """Redact Telegram bot token from any error message, URL, or string."""
    if not text:
        return ""
    token = get_token()
    s = str(text)
    if token and token in s:
        s = s.replace(token, "[REDACTED_TG_TOKEN]")
    # Also sanitize any bot<token> pattern
    s = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED_TG_TOKEN]", s)
    return s


def is_telegram_enabled() -> bool:
    """Check if Telegram integration is enabled by config and credentials."""
    return (
        os.environ.get("MG_TELEGRAM") == "1"
        and bool(get_token())
        and bool(get_chat_id())
    )


def build_callback_data(action: str, plan_id: str, version: int, arg: str = "") -> str:
    """
    Format callback_data: '<action>|<plan_id>|<version>|<arg>'.
    Must be <= 64 bytes per Telegram API specifications.
    """
    allowed_actions = {"approve", "reject", "checkin", "replan_ok", "replan_no"}
    if action not in allowed_actions:
        raise ValueError(f"Invalid telegram action: {action}")

    data = f"{action}|{plan_id}|{version}|{arg}"
    data_bytes = data.encode("utf-8")
    assert len(data_bytes) <= 64, f"callback_data exceeds 64 bytes: {len(data_bytes)} ({data})"
    return data


def parse_callback_data(data: str) -> tuple[str, str, int, str]:
    """Parse callback_data into (action, plan_id, version, arg)."""
    parts = data.split("|")
    if len(parts) == 3:
        action, plan_id, v_str = parts
        arg = ""
    elif len(parts) >= 4:
        action = parts[0]
        plan_id = parts[1]
        v_str = parts[2]
        arg = "|".join(parts[3:])
    else:
        raise ValueError(f"Malformed callback_data: {data}")

    try:
        version = int(v_str)
    except ValueError:
        version = 1

    return action, plan_id, version, arg


def _format_inline_keyboard(
    buttons: list[list[dict[str, str]]] | list[dict[str, str]] | None,
) -> list[list[dict[str, str]]]:
    """Normalize button inputs into Telegram inline_keyboard structure."""
    if not buttons:
        return []

    if isinstance(buttons[0], list):
        return buttons  # type: ignore[return-value]

    # 1D list of button dicts: layout side-by-side if <= 2, else stacked
    flat_btns = buttons  # type: ignore[assignment]
    if len(flat_btns) <= 2:
        return [flat_btns]  # type: ignore[list-item]

    # Group into pairs
    rows: list[list[dict[str, str]]] = []
    current_row: list[dict[str, str]] = []
    for b in flat_btns:
        current_row.append(b)  # type: ignore[arg-type]
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    return rows


def send(
    chat_id: int | str,
    text: str,
    buttons: list[list[dict[str, str]]] | list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Send a message via Telegram sendMessage with inline keyboard.
    Timeout 20s, one retry, never raises.
    Returns: {"ok": bool, "message_id": int | None, "error": str | None}
    """
    token = get_token()
    if not token:
        return {"ok": False, "message_id": None, "error": "TG_TOKEN not configured"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": str(chat_id),
        "text": text,
    }

    ik = _format_inline_keyboard(buttons)
    if ik:
        payload["reply_markup"] = {"inline_keyboard": ik}

    last_err: str | None = None
    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    msg_id = data.get("result", {}).get("message_id")
                    return {"ok": True, "message_id": msg_id, "error": None}
                last_err = redact_token(data.get("description", resp.text))
            else:
                last_err = redact_token(f"HTTP {resp.status_code}: {resp.text}")
        except Exception as exc:
            last_err = redact_token(str(exc))

        if attempt == 0:
            time.sleep(1.0)

    logger.warning("Telegram send failed after retry: %s", last_err)
    return {"ok": False, "message_id": None, "error": last_err}


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    """Answer an incoming callback query to remove loading spinner. Never raises."""
    token = get_token()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10)
        return True
    except Exception as exc:
        logger.debug("answerCallbackQuery failed: %s", redact_token(str(exc)))
        return False


def edit_message_reply_markup(chat_id: int | str, message_id: int, reply_markup: dict[str, Any]) -> bool:
    """Edit message reply markup (e.g. remove buttons after use). Never raises."""
    token = get_token()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    try:
        requests.post(
            url,
            json={"chat_id": str(chat_id), "message_id": message_id, "reply_markup": reply_markup},
            timeout=10,
        )
        return True
    except Exception as exc:
        logger.debug("editMessageReplyMarkup failed: %s", redact_token(str(exc)))
        return False


def _get_persisted_offset() -> int | None:
    """Read latest Telegram update offset from actions table."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM actions WHERE type = 'tg_offset' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["payload_json"]:
                data = json.loads(row["payload_json"])
                return data.get("offset")
    except Exception as exc:
        logger.warning("Failed to read tg_offset from DB: %s", exc)
    return None


def _persist_offset(offset: int) -> None:
    """Persist Telegram update offset to actions table."""
    now_iso = datetime.now(UTC).isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO actions(plan_id, type, payload_json, receipt_json, ok, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("system", "tg_offset", json.dumps({"offset": offset}), "{}", 1, now_iso),
            )
    except Exception as exc:
        logger.warning("Failed to persist tg_offset: %s", exc)


def handle_callback_update(update: dict[str, Any]) -> bool:
    """
    Process an incoming Telegram callback_query update.
    Returns True if handled, False if ignored or skipped.
    """
    cb = update.get("callback_query")
    if not cb:
        return False

    cb_id = cb.get("id")
    from_user = cb.get("from") or {}
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    cb_data = cb.get("data", "")

    # SECURITY CHECK: sender from.id and message.chat.id MUST match int(TG_CHAT_ID)
    expected_chat_str = get_chat_id()
    if not expected_chat_str:
        return False

    try:
        expected_chat_id = int(expected_chat_str)
    except ValueError:
        return False

    from_id = from_user.get("id")
    chat_id = chat.get("id")

    if from_id != expected_chat_id or chat_id != expected_chat_id:
        # Silently ignore unauthorized attempts, log only a security counter
        logger.warning("Ignored Telegram callback from unauthorized sender (security violation)")
        return False

    if not cb_data:
        return False

    try:
        action, plan_id, version, arg = parse_callback_data(cb_data)
    except Exception:
        answer_callback_query(cb_id, text="Invalid button data")
        return False

    # Check plan existence and current version
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not plan_row:
            answer_callback_query(cb_id, text="Plan not found")
            return False

        current_status = plan_row["status"]
        v_row = conn.execute("SELECT MAX(version) FROM plan_versions WHERE plan_id = ?", (plan_id,)).fetchone()
        latest_version = v_row[0] if (v_row and v_row[0] is not None) else 1

    msg_id = msg.get("message_id")

    # Stale version check
    if version != latest_version:
        answer_callback_query(cb_id, text="this button is out of date")
        if msg_id:
            edit_message_reply_markup(chat_id, msg_id, {"inline_keyboard": []})
        return False

    # Idempotency / double-tap check
    if action in ("approve", "reject") and current_status != "awaiting_approval":
        answer_callback_query(cb_id, text="already done")
        if msg_id:
            edit_message_reply_markup(chat_id, msg_id, {"inline_keyboard": []})
        return False

    if action in ("replan_ok", "replan_no") and current_status != "replan_pending":
        answer_callback_query(cb_id, text="already done")
        if msg_id:
            edit_message_reply_markup(chat_id, msg_id, {"inline_keyboard": []})
        return False

    # Execute action via internal API functions (NO HTTP self-calls)
    from app.api.main import apply_plan_approval, apply_plan_progress

    try:
        if action == "approve":
            apply_plan_approval(plan_id=plan_id, version=version, decision="approve", kind="dispatch")
            answer_callback_query(cb_id, text="Dispatch approved")
        elif action == "reject":
            apply_plan_approval(plan_id=plan_id, version=version, decision="reject", kind="dispatch")
            answer_callback_query(cb_id, text="Dispatch rejected")
        elif action == "replan_ok":
            apply_plan_approval(plan_id=plan_id, version=version, decision="approve", kind="replan")
            answer_callback_query(cb_id, text="Re-plan approved")
        elif action == "replan_no":
            apply_plan_approval(plan_id=plan_id, version=version, decision="reject", kind="replan")
            answer_callback_query(cb_id, text="Current plan kept")
        elif action == "checkin":
            if arg == "arrived":
                apply_plan_progress(plan_id=plan_id, arrived=True, source="checkin_button")
            elif arg.startswith("delay:"):
                apply_plan_progress(plan_id=plan_id, delay=arg, source="checkin_button")
            elif arg.startswith("passed:"):
                apply_plan_progress(plan_id=plan_id, passed=arg, source="checkin_button")
            answer_callback_query(cb_id, text="Check-in recorded")
    except Exception as exc:
        logger.warning("Error executing callback action '%s' for plan %s: %s", action, plan_id, exc)
        answer_callback_query(cb_id, text="Action failed")
        return False

    # Remove buttons after successful use
    if msg_id:
        edit_message_reply_markup(chat_id, msg_id, {"inline_keyboard": []})

    return True


def _telegram_polling_loop(stop_event: threading.Event) -> None:
    """Long-polling thread loop. Survives unexpected exceptions and stops on shutdown."""
    token = get_token()
    if not token:
        logger.info("Telegram polling loop exiting: no TG_TOKEN")
        return

    logger.info("Starting Telegram long-polling loop...")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    offset = _get_persisted_offset()

    while not stop_event.is_set():
        params: dict[str, Any] = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset + 1

        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    for u in updates:
                        u_id = u.get("update_id")
                        if u_id is not None:
                            offset = u_id
                            _persist_offset(offset)
                        handle_callback_update(u)
            else:
                logger.warning("Telegram getUpdates returned HTTP %s: %s", resp.status_code, redact_token(resp.text))
                time.sleep(2.0)
        except Exception as exc:
            # Polling thread must survive exceptions
            logger.warning("Telegram polling exception (survived): %s", redact_token(str(exc)))
            time.sleep(2.0)

    logger.info("Telegram polling thread stopped.")


def start_telegram_polling() -> threading.Thread | None:
    """Start background Telegram polling daemon if MG_TELEGRAM=1 and credentials are set."""
    global _POLLING_THREAD
    with _GLOBAL_LOCK:
        if not is_telegram_enabled():
            return None

        if _POLLING_THREAD is not None and _POLLING_THREAD.is_alive():
            return _POLLING_THREAD

        _STOP_EVENT.clear()
        t = threading.Thread(target=_telegram_polling_loop, args=(_STOP_EVENT,), daemon=True)
        t.start()
        _POLLING_THREAD = t
        return t


def stop_telegram_polling() -> None:
    """Signal Telegram polling thread to terminate."""
    global _POLLING_THREAD
    with _GLOBAL_LOCK:
        _STOP_EVENT.set()
        if _POLLING_THREAD is not None and _POLLING_THREAD.is_alive():
            _POLLING_THREAD.join(timeout=2.0)
        _POLLING_THREAD = None
