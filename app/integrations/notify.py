"""
Notification integration (Stream C).

Sends notifications via Telegram when enabled (MG_TELEGRAM=1 and credentials set);
otherwise logs a no-op.
Records every notification and its receipt in the actions SQLite table.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from typing import Any

from app.db.db import get_conn
from app.integrations.telegram import get_chat_id, is_telegram_enabled, redact_token, send

logger = logging.getLogger(__name__)


def notify(
    plan_id: str,
    kind: str,
    text: str,
    buttons: list[dict[str, Any]] | list[list[dict[str, Any]]] | None = None,
) -> bool:
    """
    Send notification to driver/operator via Telegram or logged no-op.
    Records every notification to the actions SQLite table.
    A Telegram failure will never fail the calling operation.
    """
    ts = datetime.now(UTC).isoformat()
    clean_text = redact_token(text)
    payload = {"kind": kind, "text": clean_text, "buttons": buttons or []}

    ok = 1
    receipt: dict[str, Any] = {"status": "no_op", "channel": "log"}

    if is_telegram_enabled():
        chat_id = get_chat_id()
        try:
            res = send(chat_id=chat_id, text=clean_text, buttons=buttons)  # type: ignore[arg-type]
            if res.get("ok"):
                ok = 1
                receipt = {
                    "status": "sent",
                    "channel": "telegram",
                    "message_id": res.get("message_id"),
                }
            else:
                ok = 0
                receipt = {
                    "status": "failed",
                    "channel": "telegram",
                    "error": redact_token(str(res.get("error"))),
                }
        except Exception as exc:
            ok = 0
            receipt = {
                "status": "failed",
                "channel": "telegram",
                "error": redact_token(str(exc)),
            }

    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO actions(plan_id, type, payload_json, receipt_json, ok, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    kind,
                    json.dumps(payload),
                    json.dumps(receipt),
                    ok,
                    ts,
                ),
            )
        logger.info("[NOTIFY] plan=%s kind=%s ok=%s channel=%s", plan_id, kind, ok, receipt.get("channel"))
        return ok == 1
    except Exception as exc:
        logger.error("[NOTIFY_FAILED] plan=%s error=%s", plan_id, redact_token(str(exc)))
        return False
