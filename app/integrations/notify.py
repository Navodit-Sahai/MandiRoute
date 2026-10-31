"""
Notification integration (Stream C).

Logged no-op for now (Telegram plugs in later); logs each call to the actions table.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional

from app.db.db import get_conn

logger = logging.getLogger(__name__)


def notify(
    plan_id: str,
    kind: str,
    text: str,
    buttons: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """
    Send notification to driver/operator. Currently a logged no-op.
    Records every notification to the actions SQLite table.
    """
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"kind": kind, "text": text, "buttons": buttons or []}
    receipt = {"status": "no_op", "channel": "log"}

    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO actions(plan_id, type, payload_json, receipt_json, ok, ts)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (plan_id, kind, json.dumps(payload), json.dumps(receipt), ts),
            )
        logger.info("[NOTIFY] plan=%s kind=%s text=%s", plan_id, kind, text[:80])
        return True
    except Exception as exc:
        logger.error("[NOTIFY_FAILED] plan=%s error=%s", plan_id, exc)
        return False
