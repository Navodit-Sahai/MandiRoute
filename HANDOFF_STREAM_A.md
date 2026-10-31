### 2026-10-31 · Stream A · Modules A2–A7 (A1 gate, A8 P1, A9 not done) · status IN_PROGRESS (code done, NOT live-verified)
- Delivered: app/data/{http,geo,mandi,geocode,routing,weather,assemble,health}.py, app/db/{schema.sql,db.py},
  scripts/build_coords.py, data/reference/{neighbour_states.json (DRAFT, needs human review), mandi_coords.csv (header only)}.
  Shared-file copies (to be replaced by M0): app/contracts, app/config.py, app/guards.py.
- Tests: 108 passed (mocked, FIXTURE_*). mypy + ruff(F,E9) clean. NOTHING verified against live APIs.
- Decisions: D-A2 http_cache table added; D-A3 per-sample weather evidence (precip in note); D-A4 IST = fixed +05:30 (no tzdata).
- New assumptions / UNKNOWN for gate A1: data.gov.in state filter key + state spellings; price unit (MG_PRICE_UNIT_VERIFIED);
  fallback OSRM host routing.openstreetmap.de; page-size cap of the mandi API.
- Contract-change requests (config.py is frozen): .env loader + datagov_key()/env()/price_unit_verified(); http_cache table.
- Cross-stream: option_id / route_id MUST start with mandi_id then '::' or '~' (refresh_for_monitoring parses it) -> B and C agree.
  Watchdog should call refresh_for_monitoring(..., variety=, grade=) with the values stored from the plan; force=True for demo ticks.
- Next module for this stream: A1 (run scripts/check_apis.py with real keys), then A9 hardening.
