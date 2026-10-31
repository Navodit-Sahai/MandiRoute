# PROGRESS.md — MandiGuard shared memory (read order: AGENT.md → PLAN.md → BLUEPRINT.md → PROGRESS.md)

> **UPDATE PROTOCOL (every agent, every session):**
> 1. At start: read this file fully; claim your module (status `IN_PROGRESS`, stream, timestamp).
> 2. At end: update ONLY your stream's rows + append ONE entry to **Session Log** (newest at bottom) + update **Gate status** if changed.
> 3. Never delete another stream's notes. On merge conflict keep both sides.
> 4. Status words: `NOT_STARTED · IN_PROGRESS · BLOCKED · DONE · VERIFIED`. `DONE` = Definition of Done met **including live-mode path**. `VERIFIED` = confirmed against real APIs on a human's machine.
> 5. Label claims `VERIFIED / ASSUMPTION / UNKNOWN` (NN-14). Never paste secrets.

_Last updated: 2026-10-31 · by Stream A session #1_

---

## 1. Gate status
| Gate | Target | Status | Notes |
|---|---|---|---|
| G0 contracts merged (M0) | H2 | **NOT DONE by Stream C** | Stream A made *temporary copies* of contracts/config/guards so it could build. Stream C's M0 must replace/reconcile them (see §6 CR-1, CR-2). |
| G1 API verification decision | H3 | **NOT DONE** | A1 never run: Stream A's sandbox has no access to data.gov.in / OSRM / Open-Meteo. A human must run it (see §4). |
| G2 vertical slice (`POST /plan` live) | H12 | not started | Needs B5 + C1 + live-verified A7. |
| G3 dispatch loop | H24 | not started | |
| G4 freeze / G5 rehearsal | H32 / H36 | not started | |

## 2. Module tracker
| ID | Module | Stream | Status | Notes |
|---|---|---|---|---|
| M0 | Scaffold + contracts | C | NOT_STARTED (A has temp copies) | contracts copied exactly from BLUEPRINT §1 |
| A1 | API verification gate | A | **NOT_STARTED — needs human + keys** | `scripts/check_apis.py` was NOT in the uploads; not seen by Stream A |
| A2 | HTTP core, cache, ledger | A | IN_PROGRESS (code+mocked tests done; live DoD pending) | `app/data/http.py`, `app/db/*` |
| A3 | Mandi ingestion & shortlist | A | IN_PROGRESS (same) | `app/data/mandi.py` |
| A4 | Geocoding & coords table | A | IN_PROGRESS (same) | `app/data/geocode.py`, `scripts/build_coords.py`; **needs ≥10 human-verified rows in mandi_coords.csv** |
| A5 | Routing | A | IN_PROGRESS (same) | `app/data/routing.py` |
| A6 | Weather along route | A | IN_PROGRESS (same) | `app/data/weather.py` |
| A7 | Assembler & source health | A | IN_PROGRESS (same) | `app/data/assemble.py`, `health.py` |
| A8 | Historical loader (P1) | A | NOT_STARTED | only after all P0 DONE |
| A9 | Deployment & hardening | A | NOT_STARTED | |
| B1–B10 | Decision engine | B | NOT_STARTED (as far as this file knows) | Stream B: update your rows |
| C1–C7 | API, agent, Telegram, UI | C | NOT_STARTED (as far as this file knows) | Stream C: update your rows |

## 3. Stream A — what exists (file inventory, all in repo root)
| Path | What it is |
|---|---|
| `app/data/http.py` | `request_json(...)` single entry for every external call: SQLite TTL cache, per-host `RateLimiter`, 1 retry+backoff, timeout ≤20 s, **never raises on HTTP errors** (returns `HttpResult`, iterable as `(data, meta, error)`), stale-cache fallback (`max_stale_s`), secret redaction, `make_evidence()`, `new_evidence_id()`, `collect()` context (gathers evidence/calls/warnings), `add_warning()`, test time hooks. |
| `app/data/geo.py` | haversine, India bbox, `mandi_id()` slug, state-name aliases, `states_for()` (neighbours), `IST` fixed +05:30 (no tzdata dep). |
| `app/data/mandi.py` | data.gov.in fetch (pagination, filter-style fallback, client-side re-check), `normalize_record`, suspect rules, `select_variety_grade`, `build_price_band` (3 observed evidence rows), `shortlist`. |
| `app/data/geocode.py` | chain: manual verified CSV / `mandi_settings` → cache → Nominatim → Open-Meteo geocoding → district centroid → unknown. Precision tags; never invents coords; outages not cached as "not found". |
| `app/data/routing.py` | OSRM route (+alternatives, steps) → waypoints with step-based `elapsed_nominal_s`; ONE table call per route → `Waypoint.to_dest_s`; fallback server + 60 s circuit-breaker; no straight-line fakes. |
| `app/data/weather.py` | one multi-location Open-Meteo call per route; local-hour alignment using `utc_offset_seconds` (+05:30 trap handled); None never 0; destination weather; river-flow **proxy** evidence. |
| `app/data/assemble.py` | **`assemble_candidates(req, now)`**, **`refresh_for_monitoring(req, option_ids, now, *, variety, grade, force)`**, `CandidateAssembly`. Re-exports `source_health`. |
| `app/data/health.py` | **`source_health(probe=True)`** (cached 5-min probes; `probe=False` = passive from `source_calls`). |
| `app/db/schema.sql`, `db.py` | PLAN §8 schema + `http_cache` + indexes. Helpers: `get_conn`, `init_db`, `get_evidence(id)`, `get/upsert_mandi_setting`. |
| `app/contracts/`, `app/config.py`, `app/guards.py` | **TEMP COPIES** (exact per BLUEPRINT). config also has `.env` auto-loader + `env()`, `datagov_key()`, `price_unit_verified()`. |
| `data/reference/neighbour_states.json` | **DRAFT, needs human review.** |
| `data/reference/mandi_coords.csv` | header only (no unverified/invented rows). |
| `scripts/build_coords.py` | geocodes demo mandis → `mandi_coords_candidates.csv` (git-ignored) for HUMAN map verification. |
| `tests/data/*` | 108 tests, all HTTP mocked (`FIXTURE_*` in `tests/data/fixtures.py`). |

### Public API for other streams
```python
from app.data.assemble import assemble_candidates, refresh_for_monitoring, CandidateAssembly
from app.data.health import source_health
from app.db.db import init_db, get_evidence, upsert_mandi_setting   # C: /evidence/{id}, UI closing-time inputs
from app.data.http import collect, make_evidence                      # optional
```
- `assemble_candidates` can take ~30–70 s **cold** (1 req/s OSRM/Nominatim); warm cache ≈ <40 s. Accepts `progress=callable(str)`.
- Mandi ids = `app.data.geo.mandi_id(state, district, market)` → keys of `DispatchRequest.mandi_latest_arrival`. UI must list ids from `CandidateAssembly.candidates`.
- Every number in candidates has an evidence id; `CandidateAssembly.evidence` is the ledger subset; full rows in DB table `evidence`.
- Degradation: failed price → candidate dropped; failed weather → `route.weather=[]` + warning; failed route → candidate kept with `routes=[]` + warning; unknown location → dropped. Check `warnings` and `source_status`.

## 4. API Verification (gate A1) — partial, human run 2026-10-31 (PowerShell Invoke-RestMethod)
| Source | Result | Newest record date | Latency | Notes |
|---|---|---|---|---|
| data.gov.in Agmarknet (via PowerShell) | **PASS** (VERIFIED by human run) | 31/10/2026 (age 0 d) | 0.1–0.7 s | all 10 fields present, lowercase ids: state, district, market, commodity, variety, grade, arrival_date, min_price, max_price, modal_price. Date format **dd/mm/yyyy** confirmed. Both `filters[state.keyword]` and `filters[state]` work. `limit=500` accepted. Prices come as JSON numbers. **Tomato Maharashtra today: total=3 records only.** |
| data.gov.in via **Python `requests`** | **ROOT CAUSE FOUND (VERIFIED by human test)** | – | – | default `python-requests` User-Agent -> ReadTimeout (32 s); custom UA `MandiGuard/0.1 (test)` -> 200 in 0.8 s; `curl/8.0` -> 200 in 0.9 s. **Fix applied in http.py (D-A11): every call sends `MandiGuard/0.1 (<MG_CONTACT_EMAIL>)`.** Needs a human re-run of build_coords.py to confirm. |
| Price unit | **UNVERIFIED** | – | – | field metadata gives NO unit. Values (MH 500–1200, TN 3000–4000) are plausible as Rs/quintal, absurd as Rs/kg. Plausible ≠ verified: confirm from Agmarknet's own table header before setting `MG_PRICE_UNIT_VERIFIED=1`. |
| Open-Meteo / OSRM / Nominatim / Telegram | UNKNOWN | – | – | not yet run |
Decision D-A1: mandi API **PASS** by the G1 rule (fresh record <=3 d, 10 fields) via PowerShell; Python-client path must still be confirmed.

## 5. Decisions
| ID | Decision | By |
|---|---|---|
| D-A2 | Added generic `http_cache` table (mandi/misc); routes→`route_cache`, weather→`weather_cache`, geocodes→`geocode_cache`. | A |
| D-A3 | One weather Evidence per sample (value = temp °C); precipitation is in the evidence **note** because `WeatherSample` has a single `evidence_id`. | A |
| D-A4 | India time = fixed +05:30 (`geo.IST`), avoids `tzdata` dependency on Windows. | A |
| D-A5 | Mandi shortlist uses straight-line prefilter on market coords, else **district centroid** (network, budget 40/call, cached forever). Run `build_coords.py` to pre-warm. | A |
| D-A6 | Variety/grade chosen among **shortlisted** mandis; mandis lacking that pair are dropped with a named warning. | A |
| D-A7 | Price evidence labels say "price reported (record date: …)" (not "today") because records can be days old. | A |
| D-A8 | Nominatim is **skipped** (not called anonymously) if `MG_CONTACT_EMAIL` unset. | A |
| D-A10 | Server-side state filter: keep default `state.keyword` (verified working; plain `state` also works). Page limit 500 stays. | A |
| D-A11 | `request_json` always sends an identifying User-Agent (caller headers override). Fixes data.gov.in stalling the default python UA. | A |
| D-A12 | Geocoding tries the BRACKET hint first (`Pune(Moshi)`->Moshi); a hit on the cleaned name that equals the district is downgraded to `district_centroid`; `refresh=True` bypasses cache (used by build_coords). Cause: live run geocoded Pune(Moshi) to Pune city centre labelled `market`. | A |
| D-A9 | Prices carry `unit_verified=False` until human sets `MG_PRICE_UNIT_VERIFIED=1` after A1. | A |

## 6. Contract / shared-file changes (CR log)
| CR | What | Why | Affects | Back-compat |
|---|---|---|---|---|
| CR-1 (**needs approval**) | `app/config.py`: `.env` loader (autoload on import), `env()`, `datagov_key()`, `price_unit_verified()` | no python-dotenv allowed (PLAN §3); keys must load | C (M0 owner) | additive |
| CR-2 (**needs approval**) | `app/db/schema.sql`: extra table `http_cache` + 3 indexes | generic TTL cache | all | additive |
| CR-3 (**cross-stream convention**) | `option_id` and `route_id` MUST start with `mandi_id` followed by `::` or `~` | `refresh_for_monitoring` derives mandi ids from them (contract exposes none to A) | **B (option ids), C (watchdog)** | needs B+C agreement |
| CR-4 (optional) | `WeatherSample` could carry `precip_evidence_id`; or `Evidence` gets structured extra values | precip currently only in note | A, B, C | additive |

## 7. Open issues / blockers (address to a stream)
- **[Human]** A1 partly done (mandi API ok). Re-run `build_coords.py` after the User-Agent fix. Still: Open-Meteo, OSRM, Nominatim; confirm price unit; set `MG_CONTACT_EMAIL`.
- **[Human]** Decide the variety/grade rule (see findings above).
- **[Human]** Review `data/reference/neighbour_states.json` (DRAFT).
- **[Human]** Run `scripts/build_coords.py`, verify on map, add ≥10 rows (verified=1) to `mandi_coords.csv`.
- **[Human]** `scripts/check_apis.py` is referenced by PLAN/BLUEPRINT but was never provided to Stream A — supply it.
- **[B]** Adopt CR-3 in `option_id`/`route_id`. `Waypoint.to_dest_s` is `None` when OSRM says unreachable or the table call failed (then `{}` empty) — treat as "commit points not computed".
- **[C]** Call `init_db()` in lifespan; call `refresh_for_monitoring(..., variety=, grade=)` with values stored from the plan (pass both or neither); `force=True` for demo ticks (bypasses price+weather caches). Persist `CandidateAssembly.selected_variety/grade` with the plan.
- **[C]** M0 must reconcile the temp copies of contracts/config/guards (do not fork them).
- **[A]** A8 and A9 not started.

### Findings from the real data (2026-10-31) — need a human decision / follow-up
- **Very thin data:** Maharashtra had only 3 tomato records today (neighbour states add more). Few candidates is normal.
- **Variety rule drops mandis:** Pune(Moshi)=`Local/Local`, Rahata & Khed=`Other/Local`. Rule D-A6 keeps the most common pair (`Other/Local`) and DROPS Pune. Options: keep (strict comparability) or match on GRADE only / treat `Other`,`Local`,`Deshi` as one group. **Human decision pending; not changed.**
- **Degenerate price bands:** some records have min=modal=max (e.g. 800/800/800) -> the 3 price scenarios collapse. Valid under our rules but weak; suggest B/coverage adds a caveat (Stream B request).
- **Market names carry the real yard in brackets** (`Pune(Moshi)`, `Khed(Chakan)`): `clean_market_name` strips brackets, so Nominatim would geocode "Pune" city, not Moshi, yet may be labelled `market` precision. **Use verified manual coords for demo mandis.**
- **District renames:** `Ahilyanagar` (formerly Ahmednagar) may not geocode by name -> falls to unknown; add manual coords.
- Records have trailing spaces in market names (`"Pune(Moshi) "`); already stripped by `normalize_record`.

## 8. Gotchas learned (save yourself time)
- `urllib.parse.urlparse` splits `;…` off the path (breaks OSRM-style `/driving/a;b` parsing in test callbacks) → use `urlsplit`.
- Open-Meteo times are **local** (UTC+05:30) → hour boundaries ≠ UTC hours; align with `utc_offset_seconds`.
- Open-Meteo returns a **list** for ≥2 locations, a **dict** for 1.
- Tests use `FakeClock` (conftest) — `sleep()` advances instantly; real waiting never happens. Limiter/backoff read time via `http._clock/_sleep` hooks.
- Don't reuse loop variable names across record/route types (mypy caught this in `assemble.py`).
- `DB_PATH=':memory:'` is rejected (one connection per call).
- No fixtures in `app/` (a test enforces NN-1).

## 9. Scope Requests (AGENT §3 protocol)
_None yet._

## 10. E2E results (BLUEPRINT §4)
_Not run._

## 11. Session Log (append only, newest last)

### 2026-10-31 · Stream A · session #1 · A2–A7 code complete (mocked) · status IN_PROGRESS
**Steps taken, in order:**
1. Read AGENT.md, PLAN.md, BLUEPRINT.md fully. Chose Stream A (data & integrations) because the request was "files for fetching data from the APIs".
2. Copied shared files exactly: `app/contracts/__init__.py`, `app/guards.py`; wrote `app/config.py` (Assumptions Registry + env helpers) and `.env.example`, `.gitignore`.
3. Wrote DB: `schema.sql` (PLAN §8 + `http_cache`), `db.py`.
4. Wrote `http.py` (A2) → tests green (17).
5. Wrote `geo.py`, `neighbour_states.json` (DRAFT), empty `mandi_coords.csv`.
6. Wrote `geocode.py` (A4), `mandi.py` (A3), `routing.py` (A5), `weather.py` (A6), `health.py`, `assemble.py` (A7), each with tests.
7. **Hunter pass** (static + review) found and fixed: Open-Meteo market match only checked district (accepted wrong-named places); loop-variable reuse in `assemble.py`; pagination truncation if API caps page size; slug ids in warnings instead of market names; stale-cache price evidence not flagged; `force` not reaching weather; flood label aligned to approved wording; `.env` never loaded (added autoload); vacuous `or True` assertion in a test; test-double and fixture bugs (`urlparse` `;`).
8. Added `scripts/build_coords.py`, `requirements.txt`, `README.md`, handoff note, this file; packaged `mandiguard_stream_a.zip`.
- Tests: **108 passed** (all mocked). mypy clean on `app/` + `scripts/`; ruff F/E9 clean.
- **NOT done / NOT verified:** any live API call (sandbox has no access to those hosts); A1 gate; A8; A9; real coordinates.
- New assumptions: D-A2…D-A9 above; filter-style/state-spelling guesses for data.gov.in (UNKNOWN until A1).
- Next module for Stream A: **A1** (human run), then A9 hardening; A8 only after all P0 DONE.

### 2026-10-31 · Stream A · session #2 · A1 partial results recorded · status IN_PROGRESS
- Human ran PowerShell checks against data.gov.in: HTTP 200, fast, fields/date format/filters confirmed (see §4).
- Python `requests` ReadTimeout vs fast PowerShell is unexplained -> diagnostic sent to human (compare default User-Agent / bracket encoding).
- No code changed this session. Findings and pending decisions added to §7.
- Next: Python-side diagnostic result -> then Open-Meteo/OSRM checks; variety-rule decision.

### 2026-10-31 · Stream A · session #3 · User-Agent fix · status IN_PROGRESS
- Human test proved data.gov.in stalls the default python-requests UA. Added `default_user_agent()` in `app/data/http.py`, used by all calls (Nominatim still sets its own contact UA). New test added. Tests: 109 expected passing.
- Next: human re-runs `scripts/build_coords.py`; then Open-Meteo/OSRM checks.

### 2026-10-31 · Stream A · session #4 · geocoding precision fix · status IN_PROGRESS
- Human ran build_coords.py OK (UA fix works). Pune(Moshi) came back as Pune city centre (wrong yard, labelled market); Khed(Chakan) uncertain; Rahata plausible. NOTHING verified yet -> mandi_coords.csv still empty.
- Fix D-A12 in geocode.py/geo.py/build_coords.py + 3 tests. Old results are cached permanently: human must re-run build_coords (now uses refresh=True).
- Next: human verifies 3 rows on map by hand (type correct Moshi/Chakan coords if needed), then Open-Meteo/OSRM checks.