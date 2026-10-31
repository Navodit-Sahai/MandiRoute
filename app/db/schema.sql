-- MandiGuard SQLite schema (PLAN.md section 8). Owned by Stream A. Idempotent (IF NOT EXISTS).
-- Additions vs PLAN (additive only): http_cache (generic TTL cache for mandi/misc), indexes.

CREATE TABLE IF NOT EXISTS source_calls(id INTEGER PRIMARY KEY, ts TEXT, source TEXT, url_redacted TEXT, http INT, latency_ms INT, ok INT, note TEXT);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, kind TEXT, label TEXT, value TEXT, unit TEXT, source TEXT, as_of TEXT, fetched_at TEXT, stale INT, note TEXT);
CREATE TABLE IF NOT EXISTS price_records(id INTEGER PRIMARY KEY, fetched_at TEXT, state TEXT, district TEXT, market TEXT, commodity TEXT, variety TEXT, grade TEXT, arrival_date TEXT, min_price REAL, max_price REAL, modal_price REAL, unit TEXT, unit_verified INT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS geocode_cache(key TEXT PRIMARY KEY, lat REAL, lon REAL, precision TEXT, display_name TEXT, source TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS route_cache(key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS weather_cache(key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS http_cache(key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS mandi_settings(mandi_id TEXT PRIMARY KEY, name TEXT, latest_arrival_local TEXT, lat REAL, lon REAL, precision TEXT, source TEXT); -- user-filled; empty at start
CREATE TABLE IF NOT EXISTS plans(plan_id TEXT PRIMARY KEY, request_json TEXT, status TEXT, created_at TEXT, updated_at TEXT);
  -- status: draft|awaiting_approval|dispatched|monitoring|replan_pending|completed|abstained|rejected
CREATE TABLE IF NOT EXISTS plan_versions(plan_id TEXT, version INT, planset_json TEXT, diff_json TEXT, created_at TEXT, PRIMARY KEY(plan_id, version));
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, plan_id TEXT, version INT, kind TEXT, decision TEXT, by TEXT, ts TEXT); -- kind: dispatch|replan
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, plan_id TEXT, type TEXT, payload_json TEXT, receipt_json TEXT, ok INT, ts TEXT);
CREATE TABLE IF NOT EXISTS watch_state(plan_id TEXT PRIMARY KEY, last_tick_ts TEXT, trigger_counts_json TEXT, cooldown_until TEXT, last_progress_json TEXT);
CREATE TABLE IF NOT EXISTS watch_events(id INTEGER PRIMARY KEY, plan_id TEXT, ts TEXT, kind TEXT, detail_json TEXT);
CREATE TABLE IF NOT EXISTS agent_trace(id INTEGER PRIMARY KEY, plan_id TEXT, step INT, node TEXT, tool TEXT, args_json TEXT, result_summary TEXT, ok INT, ts TEXT);
CREATE TABLE IF NOT EXISTS replay_runs(id INTEGER PRIMARY KEY, name TEXT, params_json TEXT, result_json TEXT, created_at TEXT); -- P1

CREATE INDEX IF NOT EXISTS ix_source_calls_src_ts ON source_calls(source, ts);
CREATE INDEX IF NOT EXISTS ix_price_records_mkt ON price_records(state, district, market, commodity, arrival_date);
CREATE INDEX IF NOT EXISTS ix_price_records_fetch ON price_records(fetched_at);
