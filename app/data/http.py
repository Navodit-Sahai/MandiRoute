"""HTTP core for Stream A (BLUEPRINT A2).

Every external call in the app goes through `request_json`:
  timeout <= 20 s, 1 retry with backoff, SQLite TTL cache, per-host rate limiter,
  every attempt logged to `source_calls` (URL redacted), NEVER raises on HTTP errors.

Also hosts the evidence ledger (`make_evidence`, `new_evidence_id`) and a per-request
`collect()` context that gathers evidence / call summaries / warnings for the assembler.

Secrets (NN-12): query params named like keys/tokens and any secret found in the environment
are masked in logs, in the `source_calls` table, and inside error strings.
"""
from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlencode

import requests

from app.config import get_settings
from app.contracts import Evidence, Kind
from app.db.db import get_conn

log = logging.getLogger("mandiguard.http")

DEFAULT_TIMEOUT_S = 20.0        # AGENT.md section 6: <= 20 s
RETRIES = 1                     # AGENT.md section 6: 1 retry
BACKOFF_S = 1.0                 # doubled per further retry
SECRET_PARAMS = frozenset({"api-key", "api_key", "apikey", "key", "token", "access_token", "access-token"})
_SECRET_ENV_NAMES = ("DATAGOV_KEY", "TG_TOKEN", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "ORS_KEY")
_CACHE_TABLES = frozenset({"http_cache", "route_cache", "weather_cache"})
_SECRET_RE = re.compile(r"(?i)\b(api[-_]?key|apikey|key|token|access[-_]?token)=([^&\s\"']+)")

def default_user_agent() -> str:
    """Identifying UA for every call. data.gov.in stalls the stock 'python-requests' UA (verified by a human
    test 2026-10-31: default UA -> ReadTimeout, any custom UA -> 200 in <1 s). Honest, not a browser fake."""
    contact = os.environ.get("MG_CONTACT_EMAIL")
    return f"MandiGuard/0.1 ({contact})" if contact else "MandiGuard/0.1"


# ---------------- time hooks (tests replace these; production uses real time) ----------------
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep
_utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731


def set_time_hooks(*, clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None,
                   utcnow: Callable[[], datetime] | None = None) -> None:
    global _clock, _sleep, _utcnow
    if clock is not None:
        _clock = clock
    if sleep is not None:
        _sleep = sleep
    if utcnow is not None:
        _utcnow = utcnow


def reset_time_hooks() -> None:
    global _clock, _sleep, _utcnow
    _clock, _sleep = time.monotonic, time.sleep
    _utcnow = lambda: datetime.now(timezone.utc)  # noqa: E731


def utcnow() -> datetime:
    return _utcnow()


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------- redaction ----------------
def _known_secrets(params: dict[str, Any] | None = None) -> list[str]:
    vals: list[str] = []
    for name in _SECRET_ENV_NAMES:
        v = os.environ.get(name)
        if v and len(v) >= 6:
            vals.append(v)
    for k, v in (params or {}).items():
        if k.lower() in SECRET_PARAMS and v not in (None, ""):
            vals.append(str(v))
    out: list[str] = []
    for v in vals:
        out.extend([v, quote(v, safe=""), quote(v)])
    return sorted(set(out), key=len, reverse=True)


def redact(text: str, params: dict[str, Any] | None = None) -> str:
    """Mask secrets in any string (URLs, exception messages, response snippets)."""
    if not text:
        return text
    for s in _known_secrets(params):
        text = text.replace(s, "***")
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


def redact_url(url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return redact(url)
    safe = {k: ("***" if k.lower() in SECRET_PARAMS else v) for k, v in params.items() if v is not None}
    return redact(f"{url}?{urlencode(sorted(safe.items()), doseq=True)}", params)


# ---------------- rate limiter (min interval between calls to one host) ----------------
class RateLimiter:
    """Blocks so that successive `wait()` returns are >= min_interval_s apart. Thread-safe."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = float(min_interval_s)
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = _clock()
            delay = self._next_allowed - now
            if delay > 0:
                _sleep(delay)
                now = _clock()
            self._next_allowed = now + self.min_interval_s


_LIMITERS: dict[str, RateLimiter] = {}
_LIM_LOCK = threading.Lock()
_DEFAULT_INTERVALS = {"datagov": 0.5, "open_meteo": 0.2, "osrm_fallback": 1.0}


def get_limiter(name: str, min_interval_s: float | None = None) -> RateLimiter:
    """Shared limiter per host key. OSRM/Nominatim intervals come from the policy registry."""
    with _LIM_LOCK:
        if name not in _LIMITERS:
            if min_interval_s is None:
                min_interval_s = get_settings().rate_limit_interval_s.get(name, _DEFAULT_INTERVALS.get(name, 0.0))
            _LIMITERS[name] = RateLimiter(min_interval_s)
        return _LIMITERS[name]


def reset_limiters() -> None:
    with _LIM_LOCK:
        _LIMITERS.clear()


# ---------------- per-request collector ----------------
@dataclass
class Collector:
    evidence: list[Evidence] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_COLLECTOR: ContextVar[Collector | None] = ContextVar("mg_collector", default=None)


@contextmanager
def collect() -> Iterator[Collector]:
    """Gather evidence/calls/warnings produced inside the block (same thread/context only)."""
    col = Collector()
    tok = _COLLECTOR.set(col)
    try:
        yield col
    finally:
        _COLLECTOR.reset(tok)


def add_warning(msg: str) -> None:
    log.warning(msg)
    col = _COLLECTOR.get()
    if col is not None and msg not in col.warnings:
        col.warnings.append(msg)


# ---------------- result type ----------------
@dataclass
class HttpResult:
    ok: bool
    data: Any = None
    http: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    from_cache: bool = False
    stale: bool = False               # True: served from an EXPIRED cache entry after a failure
    fetched_at: str = ""              # when the data was really fetched (cache entry time if cached)
    source: str = ""
    url_redacted: str = ""

    @property
    def meta(self) -> dict[str, Any]:
        return {"source": self.source, "http": self.http, "latency_ms": self.latency_ms,
                "fetched_at": self.fetched_at, "from_cache": self.from_cache, "stale": self.stale,
                "url_redacted": self.url_redacted}

    def __iter__(self) -> Iterator[Any]:      # BLUEPRINT form: (data|None, meta, error|None)
        yield self.data
        yield self.meta
        yield self.error


# ---------------- cache ----------------
def _cache_read(table: str, key: str) -> tuple[Any, datetime] | None:
    if table not in _CACHE_TABLES:
        raise ValueError(f"unknown cache table {table!r}")
    import json
    try:
        with get_conn() as c:
            r = c.execute(f"SELECT json, fetched_at FROM {table} WHERE key=?", (key,)).fetchone()
    except sqlite3.Error as e:
        log.warning("cache read failed: %s", e)
        return None
    if not r:
        return None
    try:
        return json.loads(r["json"]), datetime.fromisoformat(r["fetched_at"])
    except (ValueError, TypeError):
        return None


def _cache_write(table: str, key: str, data: Any, fetched_at: str) -> None:
    if table not in _CACHE_TABLES:
        raise ValueError(f"unknown cache table {table!r}")
    import json
    try:
        with get_conn() as c:
            c.execute(f"INSERT OR REPLACE INTO {table}(key,json,fetched_at) VALUES(?,?,?)",
                      (key, json.dumps(data), fetched_at))
    except sqlite3.Error as e:
        log.warning("cache write failed: %s", e)


def _log_call(source: str, url_red: str, http: int | None, latency_ms: int | None, ok: bool, note: str | None) -> None:
    try:
        with get_conn() as c:
            c.execute("INSERT INTO source_calls(ts,source,url_redacted,http,latency_ms,ok,note) VALUES(?,?,?,?,?,?,?)",
                      (iso_utc(), source, url_red, http, latency_ms, int(ok), note))
    except sqlite3.Error as e:
        log.warning("source_calls write failed: %s", e)


def _note_in_collector(res: HttpResult, error: str | None) -> None:
    col = _COLLECTOR.get()
    if col is not None:
        col.calls.append({"source": res.source, "ok": res.ok, "http": res.http, "latency_ms": res.latency_ms,
                          "fetched_at": res.fetched_at, "from_cache": res.from_cache, "stale": res.stale,
                          "error": error})


# ---------------- the one entry point ----------------
def request_json(source: str, method: str, url: str, params: dict[str, Any] | None = None,
                 ttl: float | None = None, rate_limiter: RateLimiter | None = None,
                 cache_key: str | None = None, *, headers: dict[str, str] | None = None,
                 timeout: float = DEFAULT_TIMEOUT_S, cache_table: str = "http_cache",
                 max_stale_s: float | None = None, force: bool = False, retries: int = RETRIES,
                 check: Callable[[Any], str | None] | None = None) -> HttpResult:
    """Fetch JSON. Never raises on network/HTTP problems; inspect `.ok` / `.error`.

    cache_key given  -> cache enabled in `cache_table`; entry is fresh if age <= ttl (ttl None = never expires).
    force            -> skip the cache read (still writes the new response).
    max_stale_s      -> on failure, serve an EXPIRED entry no older than this (result.stale=True, ok=True).
    check(data)      -> returns an error string to reject a 200 response (not retried, not cached), else None.
    """
    timeout = min(float(timeout), DEFAULT_TIMEOUT_S)
    url_red = redact_url(url, params)
    cached = _cache_read(cache_table, cache_key) if cache_key is not None else None
    now = utcnow()

    if cached is not None and not force:
        age = (now - cached[1]).total_seconds()
        if ttl is None or age <= ttl:
            res = HttpResult(True, cached[0], None, 0, None, True, False, iso_utc(cached[1]), source, url_red)
            _note_in_collector(res, None)
            return res

    err: str | None = None
    http: int | None = None
    latency_ms: int | None = None
    attempts = max(0, retries) + 1
    for i in range(attempts):
        if rate_limiter is not None:
            rate_limiter.wait()
        retryable = False
        t0 = time.perf_counter()
        try:
            hdrs = {"User-Agent": default_user_agent(), **(headers or {})}      # caller headers win
            resp = requests.request(method.upper(), url, params=params, headers=hdrs, timeout=timeout)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            http = resp.status_code
            if http == 200:
                try:
                    data = resp.json()
                except ValueError:
                    err = "HTTP 200 but body is not valid JSON"
                else:
                    bad = check(data) if check else None
                    if bad is None:
                        fetched = iso_utc()
                        if cache_key is not None:
                            _cache_write(cache_table, cache_key, data, fetched)
                        _log_call(source, url_red, http, latency_ms, True, None)
                        res = HttpResult(True, data, http, latency_ms, None, False, False, fetched, source, url_red)
                        _note_in_collector(res, None)
                        return res
                    err = redact(f"rejected: {bad}", params)
            else:
                snippet = redact((resp.text or "")[:160].replace("\n", " "), params)
                err = f"HTTP {http}" + (f": {snippet}" if snippet else "")
                retryable = http == 429 or http >= 500
        except requests.RequestException as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            http = None
            err = redact(f"{type(e).__name__}: {e}", params)
            retryable = True
        _log_call(source, url_red, http, latency_ms, False, err)
        if retryable and i < attempts - 1:
            _sleep(BACKOFF_S * (2 ** i))
        elif not retryable:
            break

    # ---- failure: optional stale fallback (clearly flagged) ----
    if cached is not None and max_stale_s is not None:
        age = (utcnow() - cached[1]).total_seconds()
        if age <= max_stale_s:
            res = HttpResult(True, cached[0], http, latency_ms, f"live fetch failed ({err}); serving cached copy",
                             True, True, iso_utc(cached[1]), source, url_red)
            _note_in_collector(res, res.error)
            return res
    res = HttpResult(False, None, http, latency_ms, err or "unknown error", False, False, iso_utc(), source, url_red)
    _note_in_collector(res, err)
    return res


# ---------------- evidence ledger ----------------
def new_evidence_id() -> str:
    return "ev_" + uuid.uuid4().hex[:8]


def make_evidence(kind: Kind, label: str, value: float | str | None, unit: str | None, source: str,
                  as_of: str | None, fetched_at: str, note: str | None = None, stale: bool = False) -> Evidence:
    """Create + persist an Evidence row and register it in the active collector (if any)."""
    import json
    if isinstance(value, float) and not math.isfinite(value):
        note = f"{note or ''} [non-finite value dropped]".strip()
        value = None
    for _ in range(5):
        ev = Evidence(id=new_evidence_id(), kind=kind, label=label, value=value, unit=unit, source=source,
                      as_of=as_of, fetched_at=fetched_at, stale=stale, note=note)
        try:
            with get_conn() as c:
                c.execute("INSERT INTO evidence(id,kind,label,value,unit,source,as_of,fetched_at,stale,note)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (ev.id, ev.kind.value, ev.label, json.dumps(ev.value), ev.unit, ev.source,
                           ev.as_of, ev.fetched_at, int(ev.stale), ev.note))
        except sqlite3.IntegrityError:      # id collision (1 in 4e9) -> new id
            continue
        col = _COLLECTOR.get()
        if col is not None:
            col.evidence.append(ev)
        return ev
    raise RuntimeError("could not allocate a unique evidence id")
