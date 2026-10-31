"""
Watchdog and ticking engine for active dispatches (Stream C / C4).

Coordinates:
- refreshing monitoring data (refresh_for_monitoring)
- trigger evaluation and replan computation (evaluate_triggers_and_recompute)
- persistence to watch_events, watch_state, plan_versions
- notification dispatch (notify)
- per-plan synchronization lock
- background polling daemon
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import threading
from typing import Any, Optional

from fastapi import HTTPException

from app.config import get_settings
from app.contracts import DispatchProgress, DispatchRequest, Evidence, PlanDiff, PlanSet
from app.data.assemble import refresh_for_monitoring
from app.db.db import get_conn, get_evidence
from app.engine import WatchState, evaluate_triggers_and_recompute
from app.explain import render_diff
from app.integrations.notify import notify

logger = logging.getLogger(__name__)

_PLAN_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_LOCK = threading.Lock()
_WATCHDOG_STOP = threading.Event()
_WATCHDOG_THREAD: Optional[threading.Thread] = None


def get_plan_lock(plan_id: str) -> threading.Lock:
    """Get or create a per-plan synchronization lock."""
    with _GLOBAL_LOCK:
        if plan_id not in _PLAN_LOCKS:
            _PLAN_LOCKS[plan_id] = threading.Lock()
        return _PLAN_LOCKS[plan_id]


def tick_plan(
    plan_id: str,
    *,
    force_fresh: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Perform a single monitoring tick for a plan.
    Thread-safe per plan.
    """
    lock = get_plan_lock(plan_id)
    with lock:
        return _tick_plan_unlocked(plan_id, force_fresh=force_fresh, now=now)


def _tick_plan_unlocked(
    plan_id: str,
    *,
    force_fresh: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    cfg = get_settings()

    # 1. Load plan, latest version, watch_state, and assembly metadata
    with get_conn() as conn:
        plan_row = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")

        version_row = conn.execute(
            "SELECT * FROM plan_versions WHERE plan_id = ? ORDER BY version DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        if not version_row:
            raise HTTPException(status_code=404, detail=f"No plan versions found for '{plan_id}'")

        ws_row = conn.execute("SELECT * FROM watch_state WHERE plan_id = ?", (plan_id,)).fetchone()

        meta_row = conn.execute(
            "SELECT result_summary FROM agent_trace WHERE plan_id = ? AND node = 'assembly_meta' ORDER BY id DESC LIMIT 1",
            (plan_id,),
        ).fetchone()

    req = DispatchRequest.model_validate(json.loads(plan_row["request_json"]))
    planset = PlanSet.model_validate(json.loads(version_row["planset_json"]))
    active_option_ids = [p.option.option_id for p in planset.plans]

    stored_grade = None
    stored_variety = None
    if meta_row and meta_row["result_summary"]:
        try:
            meta = json.loads(meta_row["result_summary"])
            stored_grade = meta.get("selected_grade")
            stored_variety = meta.get("selected_variety")
        except Exception:
            pass

    # 2. Reconstruct WatchState
    if ws_row and ws_row["trigger_counts_json"]:
        try:
            ws_data = json.loads(ws_row["trigger_counts_json"])
            if isinstance(ws_data, dict) and "trigger_counts" in ws_data:
                state = WatchState.model_validate(ws_data)
            else:
                cooldown_dt = (
                    datetime.fromisoformat(ws_row["cooldown_until"])
                    if ws_row["cooldown_until"]
                    else None
                )
                state = WatchState(
                    trigger_counts=ws_data if isinstance(ws_data, dict) else {},
                    cooldown_until=cooldown_dt,
                )
        except Exception:
            state = WatchState()
    else:
        state = WatchState()

    # 3. Reconstruct DispatchProgress
    if ws_row and ws_row["last_progress_json"]:
        try:
            progress = DispatchProgress.model_validate(json.loads(ws_row["last_progress_json"]))
        except Exception:
            progress = DispatchProgress(plan_id=plan_id, dispatched_at=now_dt.isoformat())
    else:
        progress = DispatchProgress(plan_id=plan_id, dispatched_at=now_dt.isoformat())

    # 4. Refresh candidates & evidence
    try:
        refreshed = refresh_for_monitoring(
            req=req,
            plan_option_ids=active_option_ids,
            now=now_dt,
            variety=stored_variety,
            grade=stored_grade,
            force=force_fresh,
        )
    except Exception as exc:
        logger.warning("refresh_for_monitoring failed for plan %s: %s", plan_id, exc)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO watch_events(plan_id, ts, kind, detail_json) VALUES (?, ?, ?, ?)",
                (
                    plan_id,
                    now_dt.isoformat(),
                    "degraded",
                    json.dumps({"reason": "no fresh data", "error": str(exc)}),
                ),
            )
        return {
            "triggers": [],
            "persisted": [],
            "decision": "degraded: no fresh data",
            "new_version": None,
            "diff": None,
            "diff_text": None,
        }

    # 5. Evaluate triggers and recompute
    decision = evaluate_triggers_and_recompute(
        prev=planset,
        req=req,
        fresh_cands=refreshed.candidates,
        fresh_evidence=refreshed.evidence,
        progress=progress,
        state=state,
        now=now_dt,
        cfg=cfg,
    )

    new_state = decision.new_state
    new_version_num: Optional[int] = None
    diff_text: Optional[str] = None

    with get_conn() as conn:
        # Write watch_events
        conn.execute(
            "INSERT INTO watch_events(plan_id, ts, kind, detail_json) VALUES (?, ?, ?, ?)",
            (
                plan_id,
                now_dt.isoformat(),
                "replan_proposed" if decision.propose_replan else "tick",
                json.dumps({
                    "fired": decision.fired,
                    "persisted": decision.persisted,
                    "propose_replan": decision.propose_replan,
                    "reason": decision.reason,
                }),
            ),
        )

        # Update watch_state
        conn.execute(
            """
            INSERT INTO watch_state(plan_id, last_tick_ts, trigger_counts_json, cooldown_until, last_progress_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                last_tick_ts = excluded.last_tick_ts,
                trigger_counts_json = excluded.trigger_counts_json,
                cooldown_until = excluded.cooldown_until
            """,
            (
                plan_id,
                now_dt.isoformat(),
                new_state.model_dump_json(),
                new_state.cooldown_until.isoformat() if new_state.cooldown_until else None,
                progress.model_dump_json(),
            ),
        )

        # If decision carries new_plan_set: save plan_versions & set status="replan_pending"
        if decision.new_plan_set:
            new_version_num = decision.new_plan_set.version
            diff_json_str = decision.diff.model_dump_json() if decision.diff else None
            conn.execute(
                """
                INSERT INTO plan_versions(plan_id, version, planset_json, diff_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, version) DO UPDATE SET
                    planset_json = excluded.planset_json,
                    diff_json = excluded.diff_json
                """,
                (
                    plan_id,
                    new_version_num,
                    decision.new_plan_set.model_dump_json(),
                    diff_json_str,
                    now_dt.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE plans SET status = 'replan_pending', updated_at = ? WHERE plan_id = ?",
                (now_dt.isoformat(), plan_id),
            )

    # 6. Render diff text and notify if replan proposed
    if decision.diff:
        all_eids = set(decision.new_plan_set.evidence_ids) if decision.new_plan_set else set()
        if decision.new_plan_set:
            for p in decision.new_plan_set.plans:
                all_eids.update(p.option.evidence_ids)
        ev_index = {e.id: e for e in refreshed.evidence}
        for eid in all_eids:
            if eid not in ev_index:
                row = get_evidence(eid)
                if row:
                    try:
                        ev_index[eid] = Evidence.model_validate(row)
                    except Exception:
                        pass
        try:
            diff_text = render_diff(decision.diff, ev_index)
        except Exception as e:
            logger.warning("render_diff failed: %s", e)
            diff_text = f"Plan diff updated: {decision.reason}"

    if decision.propose_replan:
        from app.integrations.telegram import build_callback_data

        replan_text = f"Re-plan proposed for plan {plan_id}:\n{diff_text or decision.reason}"
        replan_buttons = [
            {"text": "Approve replan", "callback_data": build_callback_data("replan_ok", plan_id, new_version_num or 2)},
            {"text": "Keep current plan", "callback_data": build_callback_data("replan_no", plan_id, planset.version)},
        ]
        notify(
            plan_id=plan_id,
            kind="replan_proposed",
            text=replan_text,
            buttons=replan_buttons,
        )

    return {
        "triggers": decision.fired,
        "persisted": decision.persisted,
        "decision": decision.reason,
        "new_version": new_version_num,
        "diff": decision.diff.model_dump() if decision.diff else None,
        "diff_text": diff_text,
    }


def _watchdog_loop(stop_event: threading.Event) -> None:
    """Daemon thread loop ticking every plan in monitoring status."""
    logger.info("Watchdog background loop started")
    cfg = get_settings()

    while not stop_event.is_set():
        interval = (
            float(cfg.poll_interval_demo_s)
            if os.environ.get("MG_DEMO") == "1"
            else float(cfg.poll_interval_s)
        )
        try:
            with get_conn() as conn:
                rows = conn.execute("SELECT plan_id FROM plans WHERE status = 'monitoring'").fetchall()
            for r in rows:
                p_id = r["plan_id"]
                p_lock = get_plan_lock(p_id)
                if p_lock.acquire(blocking=False):
                    try:
                        tick_plan(p_id)
                    except Exception as err:
                        logger.exception("Watchdog error ticking plan %s: %s", p_id, err)
                    finally:
                        p_lock.release()
        except Exception as exc:
            logger.exception("Watchdog loop iteration error: %s", exc)

        stop_event.wait(interval)


def start_watchdog_thread() -> Optional[threading.Thread]:
    """Start background watchdog daemon if MG_WATCHDOG=1."""
    global _WATCHDOG_THREAD
    if os.environ.get("MG_WATCHDOG") == "1":
        _WATCHDOG_STOP.clear()
        _WATCHDOG_THREAD = threading.Thread(
            target=_watchdog_loop,
            args=(_WATCHDOG_STOP,),
            daemon=True,
            name="MandiGuardWatchdog",
        )
        _WATCHDOG_THREAD.start()
        logger.info("Watchdog thread launched successfully")
        return _WATCHDOG_THREAD
    return None


def stop_watchdog_thread() -> None:
    """Signal watchdog thread to stop."""
    _WATCHDOG_STOP.set()
