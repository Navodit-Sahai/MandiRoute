# PROGRESS.md — MandiGuard shared state (the team's memory)

**How to use:** every session reads this FIRST after AGENT.md/PLAN.md/BLUEPRINT.md, and updates it LAST.
- Edit only **your stream's rows/sections** + append to the **Session Log**. On git conflict keep both sides.
- Status values: `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `DONE` (code + required tests + live path works + contracts respected) · `VERIFIED` (integrated and exercised in an E2E case).
- Never mark `DONE` on documentation alone. Never mark an API `PASS` unless tested from a real network.
- Times in IST. Update `Last updated` whenever you change a row.

**Project clock:** Hackathon start: `__:__ IST (fill)` · Freeze (G4): `+32 h` · End: `+36 h`
**Teams:** Stream A = `<name>` · Stream B = `<name>` · Stream C = `<name>` (fill)
**Current MG_MODE for demo:** `live`

---

## 1. Gates

| Gate | Condition | Target | Status | Date/time | Notes |
|---|---|---|---|---|---|
| G0 | Contracts + scaffold merged to `main`; all three streams pulled | H2 | NOT_STARTED | | |
| G1 | API verification complete; decision D-A1 written (mandi source) | H3 | NOT_STARTED | | |
| G2 | **Vertical slice:** `POST /plan` with live data returns A/B/C + coverage grade | H12 | VERIFIED | 2026-10-31 | Live POST /plan returned 3 plans (A/B/C) with ACTIONABLE_WITH_CAVEATS grade in 39.7s |
| G3 | **Dispatch loop:** approve → Telegram → check-in → tick → recompute → diff → approval | H24 | NOT_STARTED | | |
| G4 | **FREEZE:** no new features; bug-fix only | H32 | NOT_STARTED | | |
| G5 | 3 full rehearsals + failure drills passed | H36 | NOT_STARTED | | |

---

## 2. Module tracker

### Shared
| ID | Module | Owner | Status | Branch/commit | Tests | Last updated | Notes |
|---|---|---|---|---|---|---|---|
| M0 | Scaffold + contracts v1.0 + config + guards | P1 | DONE (awaiting P1 line-by-line review + push to main) | batch-1 zip | 24 tests (11 contracts + 13 config/guards) | session-1 | Contracts split into evidence/inputs/plans/events files; fields identical to BLUEPRINT §1. **G0 is ticked only after push + P2 pulls.** |

### Stream A — Data & Integrations
| ID | Module | Status | Branch/commit | Tests | Last updated | Notes |
|---|---|---|---|---|---|---|
| A1 | API verification gate | IN_PROGRESS | human runs | | 2026-10-31 | Mandi API + Telegram buttons VERIFIED by human (see §3). Open-Meteo/OSRM/Nominatim isolated checks still to run; their e2e use is inferred from the live POST /plan. |
| A2 | HTTP core / cache / ledger | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/http.py`, `app/db/*`. Used live inside G2 run (39.7 s). |
| A3 | Mandi ingestion & shortlist | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/mandi.py` (grade-only matching D-A13). Used live inside G2 run (39.7 s). |
| A4 | Geocoding & coords table | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/geocode.py`, `scripts/build_coords.py`; **mandi_coords.csv still empty: human must verify rows**. Used live inside G2 run (39.7 s). |
| A5 | Routing + waypoint table | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/routing.py`. Used live inside G2 run (39.7 s). |
| A6 | Weather along route | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/weather.py`. Used live inside G2 run (39.7 s). |
| A7 | Candidate assembler + source health | DONE (awaiting review) | stream-b | ~114 mocked tests (tests/data) | 2026-10-31 | `app/data/assemble.py`, `health.py`; completes A-side of G2. Used live inside G2 run (39.7 s). |
| A8 | Historical loader / replay (P1) | NOT_STARTED | | | | deps A2 |
| A9 | Deployment & hardening | NOT_STARTED | | | | |

### Stream B — Decision Engine
| ID | Module | Status | Branch/commit | Tests | Last updated | Notes |
|---|---|---|---|---|---|---|
| B1 | Units / time / helpers | DONE (awaiting review) | batch-1 zip | 28 tests | session-1 | `app/engine/units.py` |
| B2 | Ripening exposure | DONE (awaiting review) | batch-1 zip | 23 tests | session-1 | Golden values pass (10.4/13.2/20.8 %, None at 26 °C, chilling_risk). Table transcribed from a search excerpt: **re-check against the original UC Davis page** |
| B3 | Net value | DONE (awaiting review) | batch-1 zip | 16 tests | session-1 | Golden 51 200 passes; unknown costs reported in `unknown_fields`, never hidden |
| B4 | Option evaluator + 9-scenario grid | DONE (awaiting review) | stream-b | 8 golden tests | 2026-10-31 | `app/engine/scenarios.py`; 9 scenarios, CVaR25/mean score, ripening=None |
| B5 | Plan builder A/B/C | DONE (awaiting review) | stream-b | 6 golden tests | 2026-10-31 | `app/engine/plans.py`; A/B/C selection, Fail(A) hedge, robustness, C departure slots |
| B6 | Commitment points | DONE (awaiting review) | stream-b | 6 tests | 2026-10-31 | `app/engine/commit.py` (commit 34f76de); closing-time rule aligned with B4 in ad4b705 |
| B7 | Flip boundaries | DONE (awaiting review) | stream-b | 6 tests | 2026-10-31 | `app/engine/flips.py` (commit 26f1ae9); delay on A only, closed-form price flip |
| B8 | Coverage grader | DONE (awaiting review) | stream-b | 8 tests | session-B8 | `app/engine/coverage.py`; ripening omitted unless `ripening_enabled` |
| B9 | Triggers / hysteresis / recompute / diff | NOT_STARTED | | | | deps B5–B8 |
| B9b | Template explainer + number check | NOT_STARTED | | | | deps B5, B9 |
| B10 | Backtest harness (P1) | NOT_STARTED | | | | deps A8, B5 |

### Stream C — Agent, API, Telegram, UI
| ID | Module | Status | Branch/commit | Tests | Last updated | Notes |
|---|---|---|---|---|---|---|
| C1 | FastAPI + DB wiring + dev stubs | DONE (awaiting review) | stream-b | 11 tests | 2026-10-31 | `app/api/main.py`, `app/agent/flow.py`; live POST /plan returns A/B/C in 39.7s |
| C2 | Telegram integration | DONE (awaiting review) | stream-c | 46 tests | 2026-10-31 | `app/integrations/telegram.py`, inline keyboard, security/version/idempotency checks, polling daemon, actions logging |
| C3 | LangGraph orchestrator | NOT_STARTED | | | | deps C1, A7, B5, B8, B9b |
| C4 | Watchdog + monitoring endpoints | DONE (awaiting review) | stream-c | 7 tests | 2026-10-31 | `app/agent/watchdog.py`, `app/api/main.py`; tick, progress, approve, notify, diff endpoints |
| C5 (Part 1) | Streamlit UI (Plan & Compare screens) | DONE (awaiting review) | stream-c | 7 tests | 2026-10-31 | Screens 1 & 2 done; PyDeck map, A/B/C cards, format_inr, no forbidden words |
| C5 (Part 2) | Dispatch & Monitor screen + what-if endpoint | DONE (awaiting review) | stream-c | 37 tests | 2026-10-31 | _Dispatch.py\, \POST /plans/{id}/whatif\, \GET /plans/{id}/events\, candidate trace blob, red-box what-if tagged \[what_if]\ |
| C6 | What-if + Flip Finder (P1) | NOT_STARTED | | | | |
| C7 | E2E tests + rehearsal | NOT_STARTED | | | | |

### Next up per stream (update at every session end)
- **A:** A1 (run the checker, interpret results). Then A2 after M0.
- **B (Person 1):** next = **B4** (option evaluator + 9-scenario grid), then B5, B8, B6, B7, C1.
- **C:** M0 done — Person 1 must push to `main` so Person 2 can pull (G0).

---

## 3. API verification (Stream A fills from real runs — no PASS without a real test)

Levels: **doc** (documentation read) · **reach** (endpoint reachable) · **resp** (real response seen) · **fields** (required fields present) · **e2e** (used inside the app).

| # | Service | doc | reach | resp | fields | e2e | Verdict | Newest data date / latency / notes |
|---|---|---|---|---|---|---|---|---|
| 1 | data.gov.in mandi prices | ✔ | ✔ | ✔ | ✔ (10 fields, dd/mm/yyyy) | ✔ (live POST /plan) | **PASS** | newest record 31/10/2026 (age 0 d); 0.1-0.7 s from PowerShell. **Default `python-requests` User-Agent stalls (32 s ReadTimeout); identifying UA fixes it (D-A11).** Both `filters[state.keyword]` and `filters[state]` work. Maharashtra tomato: only 3 records that day. **Price unit still UNVERIFIED** (plausible as Rs/quintal; metadata gives no unit). |
| 2 | Open-Meteo forecast | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | free non-commercial, <10k/day |
| 3 | Open-Meteo archive | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | |
| 4 | Open-Meteo historical-forecast | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | host from memory |
| 5 | Open-Meteo Flood (proxy) | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | river-flow proxy only |
| 6 | OSRM demo (route, alternatives) | ✔ | inferred | inferred | inferred | ✔ (plans were built from routes in the live run) | **PASS (inferred)** | not tested in isolation; confirm with the PowerShell check |
| 6b | OSRM `table` | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | needed for commit points |
| 6c | OSM-DE OSRM (fallback) | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | path from memory |
| 7 | Nominatim | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | UA required, ≤1 req/s |
| 7b | Geocoding fallbacks (Open-Meteo, Photon) | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | |
| 8 | Telegram send + inline buttons | ✔ | ✔ | ✔ | ✔ | not yet | **VERIFIED** | bot @MandiSarthi_bot, private chat, 2026-10-31 |
| 8b | Telegram callback_query | ✔ | ✔ | ✔ | ✔ (data `a|test`, from.id = owner) | not yet | **VERIFIED** | approvals must check from.id == TG_CHAT_ID |
| 8c | Telegram LIVE location edits | UNKNOWN | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | **P1 unless it passes** |
| 9 | Groq LLM (optional) | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | endpoint from memory |
| 9b | Anthropic LLM (fallback) | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | |

**Decision D-A1:** `live` - mandi API reachable with fresh records (human PowerShell + Python tests, 2026-10-31) and used end-to-end in the G2 run. Replay-led plan not needed. Decided by human. Remaining A1 items: isolated checks for Open-Meteo / OSRM table / Nominatim, price-unit confirmation.

**Facts to record from real responses (A1/A3):** newest `arrival_date`: `__` · number of markets for tomato in origin state: `__` · price unit verified: `yes/no` · record keys: `__` · data.gov.in rate/HTTP behaviour: `__` · Telegram live-location updates received: `yes/no`.

---

## 4. Decisions log (append-only; D-01…D-14 are pre-agreed in PLAN.md §2)

| ID | Date | Decision | By | Reason |
|---|---|---|---|---|
| D-01…D-14 | pre-hackathon | See PLAN.md §2 | team | |
| D-A1 | 2026-10-31 | mandi source = live (see §3) | human | verified |

---

## 5. Contract changes (CR log)

| CR | Date | Stream | Change (additive? y/n) | Why | Approved by | Commit |
|---|---|---|---|---|---|---|
| CR-1 (PROPOSED, not applied) | session-1 | P1 | additive: add `"missing_weather"` to `RipeningResult.flag` (or give B4 another way to say 'temperature missing') | B2 refuses missing temperatures (`MissingTemperatureError`) and B4 must still return a `PlanOption`. Decide when starting B4. | pending | |

Contracts version: **v1.0** (text in BLUEPRINT §1).

---

## 6. Scope requests (parked — do not build without "approve SR-n")

| SR | Date | Requested by | Request | Cost / what it displaces | Decision |
|---|---|---|---|---|---|
| SR-0 | pre-hackathon | planning | Market-absorption / supply-pressure indicator | Data does not support it (arrivals ≠ traded ≠ unsold ≠ absorption); REMOVED | rejected |
| SR-0b | pre-hackathon | planning | Feeder-region supply-shock predictor; price ML as headline | Unproven; P1 only if it beats persistence | parked |

---

## 7. Blockers & cross-stream requests

| # | Date | Raised by | Addressed to | Issue | Status |
|---|---|---|---|---|---|
| — | | | | | |

---

## 8. Assumptions introduced during the build (add to `app/config.py` and PLAN §6)

| Date | Stream | Assumption | Where shown in UI | Evidence status |
|---|---|---|---|---|
| | | | | |

---

## 9. E2E results (Stream C fills; see BLUEPRINT §4)

| Case | Date/time | Mode (live/replay/dev_stub) | Result | Notes / defects |
|---|---|---|---|---|
| E2E-1 Normal | | | | |
| E2E-2 Plan reversal | | | | |
| E2E-3 Commitment point | | | | |
| E2E-4 Post-dispatch trigger | | | | |
| E2E-5 Missing/stale evidence | | | | |

---

## 10. Questions for organisers (record answers)

| # | Question | Answer | Date |
|---|---|---|---|
| 1 | Are pre-collected historical datasets (CEDA / India Data Portal) and cached API responses allowed? | | |
| 2 | Is a pre-verified `mandi_coords.csv` allowed? | | |
| 3 | Are clearly labelled historical replays acceptable alongside the live demo? | | |
| 4 | Is Telegram acceptable as the action channel? | | |
| 5 | Any restrictions on free-tier APIs during judging? | | |

---

## 11. Known facts & lessons carried over from planning (read before coding)

- **Competitors found (do not rebuild as the headline):** NetBhav (net-realization with deductions, multi-source freshness badges, FPO console — author reports data.gov.in was down for the whole hackathon), Kisan Sabha (nearby-mandi rate comparison, freight booking/calculator), Kisan Rath (truck matching), e-NAM (trading platform + arrivals/trade dashboard). Claim only: "we found no tool doing monitored contingency plans with commitment points in our searches."
- **Mandi live record has no arrivals field**; arrivals exist only historically (CEDA / India Data Portal), lagged.
- **Heat evidence:** UC Davis days-to-full-red table (stage × 12.5–25 °C) is the only quantitative basis; Ghana study (marketed within 2–4 days at 30 °C vs 8–12 at 20 °C) and a handbook guideline (≤ 12 h transport if tomato > 25 °C) are secondary anchors. Cargo-temperature offsets are assumptions.
- **Logic-test lessons (first prototype):** (1) 27 raw scenarios collapse to 9 money scenarios — keep cargo offsets out of the money grid; (2) applying delay to *both* A and B produced a meaningless flip at δ=0 — delay applies to the monitored plan only; (3) variable-name collisions between plan labels produced wrong A/B — test with ≥ 3 candidates; (4) commit-point test returned `None` because the wrong mandi's closing time was used — pass B's own closing time; (5) point-of-no-return depends on the delay scenario — always label it.
- **Sandbox limitation:** the planning session could not reach the APIs (DNS blocked). **No API has been tested.** `scripts/check_apis.py` exists (statuses PASS/PARTIAL/FAIL/NOT TESTED, writes `api_report.json`). Run it from your own network at A1.

---

## 12. Session log (append-only; newest at the bottom)

### Planning session · pre-hackathon · Stream — · Module —
- Delivered: AGENT.md, PLAN.md, BLUEPRINT.md (contracts v1.0 verified to import + validate), PROGRESS.md, `scripts/check_apis.py` (compiles; error handling smoke-tested offline).
- Not done: any API test; any application code.
- Next: all streams start per "Next up per stream" above.

### session-1 · Person 1 (Claude wrote, Person 1 reviews) · M0 + B1 + B2 + B3 · status DONE (pending human review)
- Delivered: `app/contracts/{evidence,inputs,plans,events,__init__}.py`, `app/config.py` (assumptions registry + tiny `.env` loader), `app/guards.py`, `app/engine/{units,ripening,netvalue}.py`, `data/reference/ripening_ucdavis.json`, `pytest.ini`, `requirements.txt`, `.env.example`, tests (91 passing; 6 deliberate code breakages were each caught by the tests; see VERIFICATION_RECORD.md).
- Decisions: flag priority for ripening is `chilling_risk > out_of_range > over_limit > ok` (PLAN §7.7 corrected: the old text contradicted the BLUEPRINT golden test). Net value treats unknown deductions as 0 but lists them in `unknown_fields`.
- Placeholders waiting for Person 2's results (search the code for `TODO(PERSON-2-RESULT)`): `config.py → price_unit_verified` (set True after A3 confirms ₹/quintal from a real response), `config.py → telegram_live_location_verified` (set True only if A1's Telegram test received live-location updates).
- Open: CR-1 above; ripening table must be re-checked against the original source.
- Next module for this stream: B4.

<!-- Append new entries below using the HANDOFF BLOCK format from AGENT.md §9.3 -->


### 2026-10-31 · Stream A · sessions #1-#4 (consolidated) · A2-A7 + live fixes · status DONE (awaiting review)
- Built in a separate sandbox, merged by the human: `app/data/{http,geo,mandi,geocode,routing,weather,assemble,health}.py`, `app/db/{schema.sql,db.py}`, `scripts/build_coords.py`, reference data (`neighbour_states.json` is a DRAFT needing human review; `mandi_coords.csv` empty).
- Decisions: D-A2 extra `http_cache` table | D-A3 one weather Evidence per sample (precip in note) | D-A4 IST fixed +05:30 | D-A5 district-centroid prefilter, geocode budget 40, cached forever | D-A7 labels say "price reported (record date)" | D-A8 Nominatim skipped without MG_CONTACT_EMAIL | D-A9 prices carry unit_verified=False until MG_PRICE_UNIT_VERIFIED=1 | D-A10 state filter `state.keyword` (verified) | D-A11 always send identifying User-Agent (data.gov.in stalls python-requests UA) | D-A12 geocode tries the bracket hint first (Pune(Moshi)->Moshi); name==district => district_centroid; build_coords uses refresh.
- Hunter-pass fixes: wrong-named Open-Meteo hit accepted as market; loop-variable reuse in assemble; pagination truncation; stale-cache evidence not flagged; `force` not reaching weather.
- Cross-stream convention (CR-3): option_id / route_id start with mandi_id + `::` or `~` (B5 follows it; refresh_for_monitoring depends on it).
- Open for Stream A: coords verification (human), price-unit confirmation, isolated Open-Meteo/OSRM/Nominatim checks, A8/A9.

### 2026-10-31 · Stream A · session #5 · D-A13 grade-only matching + tzdata · status IN_PROGRESS
- Human decisions recorded: approve 24h cuts; grade-only matching (D-A13); tzdata dependency added; Telegram token: human creating.
- D-A13: `app/data/mandi.py` adds `select_grade`, `grade_support`; `group_by_mandi` now treats None as wildcard; `assemble.py` selects by GRADE, shows variety per mandi, warns "varieties differ". `selected_variety` is now None by default; pin grade for monitoring (`refresh_for_monitoring(..., grade=...)`).
- Tests: 114 passed in Stream A sandbox (mocked). NOT yet run against the merged repo (contracts/config there differ from the temp copies Stream A used).
- Security: `t.py` (diagnostic with a real key) was committed -> human to remove + rotate key. `check_apis.py` is in repo (not seen by Stream A).
- Add `tzdata>=2024.1` to requirements.txt (engine B1 uses zoneinfo("Asia/Kolkata"); fails on Windows without it).
### Scope request SR-1 and CR-5 (approved by human 2026-10-31)

### §9 Scope Requests
| SR-1 | 2026-10-31 | Remove tomato ripening exposure from the MVP (24 h left) | Human: approved | Keeps code (flag-guarded), removes dependency |
Effects: engine B4/B5/B8/B9/B9b skip ripening; UI hides it; contract fields become Optional; trigger T4 disabled. Remaining product value: A/B/C plans under delay + price scenarios, window feasibility, rain exposure, flip boundaries, commitment points, hysteresis replan with diff, evidence ledger.
Honest note: ripening was the crop-specific part of the pitch. Do not describe the product as tomato-quality or spoilage aware.

### §6 CR-5 (needs merge FIRST)  
`PlanOption.ripening` -> Optional (default None); `DispatchRequest.ripeness` -> Optional (default None); `Settings.ripening_enabled=False`. Back-compat: additive for readers, B/C must handle None.

### Session log (Stream A)
- No Stream A change needed: assembler never used ripening. Destination weather (used only for ripening) is still fetched; C may ignore it.

### 2026-10-31 · Stream B · session-B8 · B8 coverage grader · status DONE (pending review)
- Files: `app/engine/coverage.py`, `tests/engine/test_coverage.py`. Tests: 8 passed (`tests/engine/test_coverage.py`).
- Assumptions: a dimension is a caveat if ANY priced+routable candidate is affected (detail names mandis); price age = calendar days `now.date() - record_date`; ABSTAIN returns `items=[]`, reasons filled. Kinds: price/unit=observed, weather=forecast, closing/deductions=user_input, location=computed. `evidence` arg accepted but unused.
- Ripening (SR-1): item omitted when `settings.ripening_enabled` is False; when enabled it only checks the ripeness stage was given.
- Blocker: `ruff` not installed in this env (not run). Next module: B4.

### 2026-10-31 · Stream B · session-B4 · B4 option evaluator & scenario grid · status DONE (pending review)
- Module: B4 (`evaluate_option`, `option_sort_key`, `sort_options`).
- Files: `app/engine/scenarios.py`, `app/engine/__init__.py`, `tests/engine/test_scenarios.py`.
- Tests run: 83 passed in `tests/engine/` (8 golden tests in `test_scenarios.py`); 224 passed full suite (`pytest -q --tb=short`).
- Assumptions: 27 environmental combinations collapse to 9 money scenarios; missed window adds `NEXT_DAY_WAIT_H * hold_cost_per_hour` with constant price level; closing time falls back from `cand.latest_arrival_local` to `req.mandi_latest_arrival`; rain exposure sums valid precipitation samples along route; ripening sets `ripening=None` without calling ripening when `ripening_enabled` is False.
- Blockers: None. Next module: B5 (Plan builder A/B/C).

- [A1 human run 2026-10-31] Telegram bot @MandiSarthi_bot: sendMessage, inline buttons and callback_query (data a|test) VERIFIED, private chat only. Live-location edits: UNKNOWN (P1, not needed; check-in buttons used).

### 2026-10-31 · Stream B · session-B5 · B5 plan builder A/B/C · status DONE (pending review)
- Module: B5 (`build_plan_set`).
- Files: `app/engine/plans.py`, `app/engine/__init__.py`, `tests/engine/test_plans.py`.
- Tests run: 101 passed in `tests/engine/` (6 golden tests in `test_plans.py`); 242 passed full suite (`pytest -q --tb=short`).
- Assumptions: Ripening OFF (no ripening eligibility filter, no staging-ripening, no ripening reconsider_if); option_id strictly formatted as `{mandi_id}::{route_id}::{depart_iso}`; Plan B chooses best mean Net over Fail(A) scenarios, or standby when Fail(A) is empty; Plan C evaluates departure slots from config with hold cost; ABSTAIN when no priced candidates or coverage grade is ABSTAIN.
- Blockers: None. Next module: B6 / B7 / B9.

### 2026-10-31 · Stream C · session-C1 · C1 minimal FastAPI app + C3-lite flow · status DONE (pending review)
- Module: C1 (FastAPI app, routes, persistence, dev_stubs) + C3-lite (plain state-machine flow).
- Files: `app/api/{main,deps,stubs,__init__}.py`, `app/agent/{flow,__init__}.py`, `tests/api/{test_api,__init__}.py`, `tests/agent/{test_flow,__init__}.py`.
- Endpoints: `GET /health`, `GET /sources/health`, `GET /assumptions`, `POST /plan`, `GET /plans/{id}`, `GET /plans/{id}/trace`, `GET /evidence/{id}`.
- Tests run: 11 passed in `tests/api` & `tests/agent`; 253 passed full suite (`pytest -q --tb=short`).
- Live Verification: Gate G2 passed. Live `POST /plan` with Pune, Maharashtra origin returned status `awaiting_approval`, coverage `ACTIONABLE_WITH_CAVEATS`, and 3 plans (Plan A: APMC Panvel, Plan B: Pune(Pimpri), Plan C: APMC Panvel +2h) in 39.7s.
- Blockers: None. Next module: C2 (Telegram integration) or C5 (Streamlit UI).

### 2026-11-01 — Stream B — plain-language layer (additive, app/explain)
- Files: `app/explain/templates.py` (+`plain_grade(grade, lang="hi")`, +`price_source_line(price_band, lang="hi", *, market)`), `app/explain/__init__.py` (exports), `tests/explain/test_plain.py` (new).
- Tests: `pytest tests/explain -q` → 16 passed. `ruff check app/explain tests/explain` clean.
- Assumptions: PriceBand has no market field → `market=` is a required keyword the caller passes (MandiCandidate name); no contract change. Dropped "aaj" from the Hindi line: record_date may not be today (would overstate freshness, AGENT §7). Unknown grade/lang → ValueError.
- For Stream C: `from app.explain import plain_grade, price_source_line`; show `plain_grade(ps.grade, lang)` next to the grade; `price_source_line(band, lang, market=cand.name)` under each price.
- Blockers: none. Next: C wires into UI.

### 2026-10-31 · Coordinator snapshot (evening) - READ THIS FIRST
- **Working:** live `POST /plan` returned A/B/C (G2 VERIFIED). Telegram buttons verified. Ripening is OFF (SR-1/CR-5).
- **Branches:** history is linear on `stream-b`: B8 -> B6 -> B7 -> B5 -> `ad4b705` (engine review: C rain_exposure None, A commit/flip/reconsider_if wired, B6 uses B4 closing rule) -> C1 `5e16805`. `stream-c` is aligned to it. Rule: Claude Code = worktree on `stream-b` (engine); Antigravity = `D:\nxt_launchpad` on `stream-c` (API/UI); sync with `git merge stream-b` on stream-c.
- **Review status:** B4-B8 and C1 are "DONE (awaiting review)". The B5 review commit exists; its findings were not logged (killed terminal) - read `git show ad4b705`.
- **Next:** B9 (triggers/diff, Claude Code) -> B9b explainer -> UI Plan + Compare (Antigravity, done C5 Part 1) -> Dispatch & Monitor + watchdog C4 -> Telegram C2 last. Human: verify >=10 mandi coordinates; confirm price unit.

### 2026-10-31 · Stream C · session-C5-part1 · C5 Part 1 Streamlit screens "Plan" and "Compare" · status DONE (pending review)
- Module: C5 Part 1 (`app/ui/app.py`, `app/ui/pages/1_Plan.py`, `app/ui/pages/2_Compare.py`, `app/ui/format.py`, `app/ui/api_client.py`).
- API additions: `map_points` and `route_paths` extracted from assembly candidates / route options, persisted to `agent_trace` under node `geo`, returned by `POST /plan` and restored by `GET /plans/{id}`.
- Screens:
  - Entry point `app/ui/app.py`: Mode banner, backend status, navigation overview.
  - Screen 1 `1_Plan.py`: Complete `DispatchRequest` form (origin, quintals, departure IST datetime, loading time, transport mode & rate, deductions, risk mode), source health strip with `MANDI_SOURCE_NOTE`, read-only assumptions expander, 120s timeout POST /plan submission, and interactive candidate mandi closing times re-plan form. Ripening OFF (no ripening/ripeness fields, vehicle="open").
  - Screen 2 `2_Compare.py`: PyDeck map with origin, candidate mandis (color-coded A/B/C), and route paths. Plan cards A/B/C with role note, 9-scenario net ranges via `format_inr`, nominal travel time (`TRAVEL_TIME_LABEL`), rain exposure, window status (`WINDOW_UNKNOWN_LABEL` when closing unknown), flip boundaries (`NO_FLIP_LABEL`), commitment points (`COMMIT_APPROX_TEMPLATE`), reconsider-if list, robustness count (`ROBUSTNESS_TEMPLATE`), coverage grade with reasons, and evidence ledger links to `GET /evidence/{id}`. Normal ABSTAIN handling.
- Compliance: AGENT §7 wording checked (`scan_forbidden_words`), no direct engine/data imports from `app/ui`.
- Tests run: 18 passed in `tests/ui`, `tests/api`, `tests/agent`; 260 passed full test suite (`pytest -q --tb=short`).
### 2026-10-31 · Stream C · session-C5-fixes · UI fixes (evidence kind tags, source strip, demo defaults, closing re-plan, data notes) · status DONE (pending review)
- Fixes in `app/ui/format.py`, `app/ui/api_client.py`, `app/ui/pages/1_Plan.py`, `app/ui/pages/2_Compare.py`:
  1. Evidence kind tags: fetched from `GET /evidence/{id}` (cached). Travel time and distance are `[computed]`; unevidenced numbers show `:red[[untagged]]`. Cost inputs show `[user_input]`.
  2. Source status strip: shows exact source NAME as heading (`datagov_agmarknet`, `open_meteo_forecast`, `open_meteo_flood`, `osrm`, `open_meteo_geocoding`, `nominatim`), ok/failed, kind tag, `as_of` or `fetched <timestamp>`, failed notes, and mandi note ("daily records, not real-time").
  3. Cost inputs: demo defaults caption added, one-click "Clear deductions" button unticks all include checkboxes.
  4. Closing times re-plan: verified and captioned ("Closing time is a user input; leave blank if unknown.").
  5. Compare page: collapsed "Data notes" box for warnings; location precision shown for all candidates ("market" or "district centroid (approximate)").
- Tests: 22 passed in `tests/ui` and `tests/api`. Live browser/curl verification for Pune returned status `awaiting_approval` with 8 candidate mandis.

### 2026-10-31 · Stream C · session-C4-watchdog · Explainer wiring + C4 Watchdog & Monitoring Endpoints · status DONE (pending review)
- Part 0 (Explainer wiring):
  - Updated `app/agent/flow.py`: persisted `assembly_meta` trace node `{selected_grade, selected_variety}`; wired explain step with `render_plan_summary(planset, evidence_index)` and `check_numbers(summary, allowed_values(...))`; drops text with warning if number check fails.
  - Returned `summary` in `POST /plan` and `GET /plans/{id}`. Displayed in `2_Compare.py` under cards in box titled "Plain-language summary (template, numbers checked)".
  - Updated `tests/agent/test_flow.py` asserting non-empty summary string and `assembly_meta` trace node.
- Part 1 (C4 Watchdog & Endpoints):
  - Created `app/integrations/notify.py`: `notify(plan_id, kind, text, buttons)` logs to `actions` table.
  - Created `app/agent/watchdog.py`: `tick_plan` with per-plan locking, refresh with pinned grade, `evaluate_triggers_and_recompute`, persistence to `watch_events`, `watch_state`, `plan_versions`, notification on replan proposal, and `diff_text = render_diff(diff, evidence_index)`. Background daemon thread enabled when `MG_WATCHDOG=1` in FastAPI lifespan.
  - Endpoints in `app/api/main.py`: `POST /plans/{id}/approve` (enforcing valid dispatch/replan state transitions and 409 conflict checks), `POST /plans/{id}/progress` (parsing delay:30/60, passed:wp, manual ETA), `POST /plans/{id}/watch/tick` (?fresh=1 query support), and `GET /plans/{id}/diff` (PlanDiff + diff_text).
- Tests: 30 passed in `tests/api`, `tests/agent`, `tests/ui` (including `test_watchdog_endpoints.py` covering approve/reject/409, progress, tick, diff, hysteresis, cooldown, no-fresh-data degradation, lock serialization, exception resilience).
- Verification: live Pune plan `e523b823` exercised through approve (`monitoring`), progress #1 (`reported_delay_min: 60.0`), tick #1, progress #2, and tick #2.


### 2026-10-31 · Stream C · session-C5-part2 · C5 Part 2 Dispatch & Monitor screen + what-if endpoint · status DONE (pending review)
- Module: C5 Part 2 (`app/ui/pages/3_Dispatch.py`, `app/api/main.py`, `app/agent/flow.py`, `app/ui/format.py`, `app/ui/api_client.py`).
- Endpoints & Flow:
  - Candidates persistence: `app/agent/flow.py` stores assembled candidates as `agent_trace` node=`candidates` with candidate models dump and evidence ids. `GET /plans/{id}` excludes this blob.
  - `POST /plans/{id}/whatif`: applies non-destructive scenario modifications to deep copies of candidates (delay_min, price_delta_pct on Plan A mandi, rain_mm_segment on Plan A route, closing_time overrides); recomputes with `build_plan_set`; renders `diff_text` via `render_diff`; never modifies or persists to `plan_versions` or `plans`.
  - `GET /plans/{id}/events`: returns all watchdog monitoring events for the plan in chronological order.
  - Added API client helpers in `app/ui/api_client.py`: `post_whatif`, `get_events`, `post_approve`, `post_progress`, `post_watch_tick`, `get_diff`.
- UI Screen 3 (`3_Dispatch.py`):
  - Plan picker default from session state or manual text input with validation.
  - Status badge (awaiting_approval, monitoring, dispatched, replan_pending, rejected, abstained).
  - Dispatch approval controls: "Approve Dispatch" / "Reject Dispatch".
  - Progress updates: "Passed Waypoint N", "Delayed +30 min", "Delayed +60 min", "Arrived at Mandi".
  - Watchdog controls: "Run watchdog tick now" and "Run tick with fresh data" showing triggers, persisted status, and diff.
  - Re-plan approval panel: appears when status is `replan_pending`, displays diff_text and changed terms table with "Approve Re-plan" and "Keep Current Plan" buttons.
  - Red-bordered box titled exactly "What-if scenario (user-set) - not observed" with inputs for delay, price change %, rain mm, closing time override; side-by-side comparison of original Plan A vs What-If Plan A; every number tagged `[what_if]`.
  - Watchdog events timeline in reverse chronological order.
- Compliance: AGENT §7 wording rules respected (no forbidden words), Ripening remains OFF (SR-1), no direct engine/data imports from UI.
- Tests: 37 passed in `tests/ui`, `tests/api`, `tests/agent` (added `tests/api/test_whatif.py` and tests in `tests/ui/test_format.py`, `tests/ui/test_screens.py`).

### 2026-10-31 ? Stream C ? session-C2-telegram ? Telegram integration + Why-Not wiring ? status DONE (pending review)
- Part 0 (Why-Not wiring):
  - Wired `why_not_highest` and `render_why_not` in `app/agent/flow.py`; verified via `check_numbers(text, allowed_values(w, {}))`; withheld with warning if check fails.
  - Returned `why_not_text` in `POST /plan` and `GET /plans/{id}`; rendered in `app/ui/pages/2_Compare.py` inside "Why not the highest-price mandi?".
  - Unit test in `tests/agent/test_flow.py` verifies why_not text and withholding behavior on check failure.
- Part 1 (Telegram integration):
  - Created `app/integrations/telegram.py`:
    - `send()` with 20s timeout, retry, never raises, token redaction across URLs, errors, and actions logs.
    - `build_callback_data()` with action|plan_id|version|arg (strictly <= 64 bytes) and `parse_callback_data()`.
    - Polling thread `_telegram_polling_loop()` reading/persisting `tg_offset` to actions table.
    - Security validation: `from.id == TG_CHAT_ID` and `chat.id == TG_CHAT_ID`; silent rejection of unauthorized attempts.
    - Stale version rejection ("this button is out of date"), idempotency ("already done"), and button removal after use.
    - Internal execution calling `apply_plan_approval` and `apply_plan_progress` (no HTTP self-calls).
    - Wired `notify()` in `app/integrations/notify.py`: sends Telegram message when enabled, logs `message_id` receipt or error (ok=0/1) to actions table.
    - Wired message templates: (a) plan proposal with Approve/Reject; (b) dispatch approved with check-in buttons (Passed waypoint, Delayed +30m, Delayed +60m, Arrived); (c) replan proposed with Approve replan / Keep current.
- Part 2 (Demo script):
  - Created `scripts/run_demo.ps1`: removes stale `DATAGOV_KEY`, sets `PYTHONPATH=.`, `MG_WATCHDOG=1`, `MG_DEMO=1`, `MG_TELEGRAM=1`, `MG_MODE=live`, launches FastAPI on port 8000 and Streamlit on port 8501 in separate PowerShell windows. Contains no secrets.
- Tests & Verification:
  - 8 new tests in `tests/api/test_telegram.py` using `responses` covering unauthorized rejection, stale version, double tap idempotence, callback_data byte length <= 64, token redaction in logs/DB, failure resilience during approval, offset persistence, and thread exception resilience.
  - 46 tests passing across `tests/api`, `tests/agent`, and `tests/ui`.
  - Live verification with `MG_TELEGRAM=1`: Pune plan `23d2af56` created, proposal message sent (`message_id: 5`), dispatch approved, and check-in buttons message sent (`message_id: 6`).

### 2026-11-01 · Stream C · session-compare-tags-fix · Compare page computed tags fix · status DONE (pending review)
- Fix:
  - Added `tag_for(value, evidence_id=None, source=None)` in `app/ui/format.py`: returns `[computed]` when `source="computed"` is explicitly passed for engine calculations (Flip Boundaries, Commitment Points, Reconsider conditions, Robustness, Window feasibility, and Net values). Returns `:red[[untagged]]` only when neither an evidence_id nor source is given. Preserves existing evidence-based tags (`[observed]`, `[forecast]`, `[proxy]`).
  - Updated `app/ui/pages/2_Compare.py`: replaced `format_kind_tag(None)` with `tag_for(source='computed')` across all engine calculation lines.
  - Added unit test in `tests/ui/test_format.py` verifying `tag_for` with engine calculations, unexplained values, and evidence lookups.
- Verification:
  - All 47 Stream C tests passed (`pytest tests/ui tests/api tests/agent`).
  - Verified live on Pune plan `23d2af56`: zero `[untagged]` values remain anywhere on the Compare screen.


### 2026-11-01 · Stream C · session-simplify-ui · Simplify Plan & Compare pages for non-technical users · status DONE (pending review)
- Module: UI simplification for non-technical users (app/ui/pages/1_Plan.py, app/ui/pages/2_Compare.py, app/ui/format.py, tests/ui/test_format.py).
- Changes:
  1. Language selector: Added Hindi / English toggle at top of Plan and Compare pages, persisted in st.session_state['lang'].
  2. Plan page: Default view shows only 3 required inputs (Quantity in qtl, Origin location, Departure date+time). Advanced parameters (transport rate/mode, handling & market deductions, clear deductions, risk mode, loading time, max distance) placed inside collapsed expander 'Advanced settings (for detailed users)'. Source Status strip and read-only assumptions panel kept below expander.
  3. Compare page: Added plain-language summary box per plan above detailed cards using app.explain.plain_grade, price_source_line, and translate_top_caveat (translating top coverage caveats such as closing time unknown to Hindi: 'mandi band hone ka time pata nahi, nikalne se pehle phone kar lein' and English). Preserved all existing detailed cards, scenario results, flip boundaries, commitment points, and evidence ledger inside collapsed expander 'Detailed view (for technical users / judges)'.
  4. Helpers & Tests: Added translate_top_caveat and format_plain_summary in app/ui/format.py; added unit tests in tests/ui/test_format.py covering translations and forbidden words check.
- Tests & Verification:
  - 16 passed in tests/ui.
  - 65 passed across tests/ui, tests/api, tests/agent, tests/explain.
  - 346 passed across full repo test suite (pytest -q --tb=short).
  - ruff check app/ui tests/ui clean.
- Assumptions & Blockers: None. Next module: Demo walkthrough / Final user review.


### 2026-11-01 · Stream C · session-generalize-commodity · Generalize crop support beyond Tomato · status DONE (pending review)
- Changes:
  1. Contracts: Changed DispatchRequest.crop from Literal['Tomato'] = 'Tomato' to str = 'Tomato' in app/contracts/inputs.py, allowing any commodity (e.g. Onion, Potato, Green Chilli) while preserving Tomato default.
  2. Plan UI (1_Plan.py): Added Commodity / फसल selector (Tomato, Onion, Potato, Green Chilli, Other) to the dispatch form, dynamically labeling quantity and passing the selected commodity in the request payload.
  3. Home UI (home.py): Updated landing headings and workflow descriptions to general agricultural commodity dispatch.
- Tests & Verification:
  - 16 passed in tests/ui.
  - 346 passed full repo test suite (pytest -q --tb=short).
  - ruff check clean.
