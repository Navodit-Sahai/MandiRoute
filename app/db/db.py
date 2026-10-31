"""SQLite access (stdlib sqlite3). Stream A.

* One short-lived connection per call (thread-safe by construction; WAL + busy_timeout).
* File databases only: ':memory:' would lose data between connections.
* Schema is applied lazily once per path (idempotent) and by init_db().
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = Path(__file__).with_name("schema.sql")
_READY: set[str] = set()
_LOCK = threading.Lock()


def db_path() -> str:
    return os.environ.get("DB_PATH") or "data/mandiguard.db"


def _apply_schema(path: str) -> None:
    with _LOCK:
        if path in _READY and Path(path).exists():
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        _READY.add(path)


def init_db() -> str:
    """Create the DB file/tables if missing. Returns the path."""
    path = db_path()
    _apply_schema(path)
    return path


def connect() -> sqlite3.Connection:
    path = db_path()
    if path == ":memory:":
        raise RuntimeError("DB_PATH=':memory:' is not supported (one connection per call)")
    _apply_schema(path)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- small helpers used across Stream A ----------------
def row_to_evidence_dict(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["value"] = json.loads(d["value"]) if d.get("value") is not None else None
    d["stale"] = bool(d.get("stale"))
    return d


def get_evidence(ev_id: str) -> dict[str, Any] | None:
    """Evidence row as a dict (value type restored). Used by GET /evidence/{id} (Stream C)."""
    with get_conn() as c:
        r = c.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone()
    return row_to_evidence_dict(r) if r else None


def get_mandi_setting(mandi_id: str) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM mandi_settings WHERE mandi_id=?", (mandi_id,)).fetchone()
    return dict(r) if r else None


def upsert_mandi_setting(mandi_id: str, *, name: str | None = None,
                         latest_arrival_local: str | None = None,
                         lat: float | None = None, lon: float | None = None,
                         precision: str | None = None, source: str = "user") -> None:
    """User-filled mandi settings (closing time / coordinates). Keeps existing values for None args."""
    with get_conn() as c:
        old = c.execute("SELECT * FROM mandi_settings WHERE mandi_id=?", (mandi_id,)).fetchone()
        o = dict(old) if old else {}
        c.execute(
            "INSERT OR REPLACE INTO mandi_settings(mandi_id,name,latest_arrival_local,lat,lon,precision,source)"
            " VALUES(?,?,?,?,?,?,?)",
            (mandi_id,
             name if name is not None else o.get("name"),
             latest_arrival_local if latest_arrival_local is not None else o.get("latest_arrival_local"),
             lat if lat is not None else o.get("lat"),
             lon if lon is not None else o.get("lon"),
             precision if precision is not None else o.get("precision"),
             source))
