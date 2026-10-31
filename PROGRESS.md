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
| A1 | API verification gate | NOT_STARTED | | | | Run `scripts/check_apis.py`; fill §3 |
| A2 | HTTP core / cache / ledger | NOT_STARTED | | | | deps M0 |
| A3 | Mandi ingestion & shortlist | NOT_STARTED | | | | deps A2 |
| A4 | Geocoding & coords table | NOT_STARTED | | | | deps A2 |
| A5 | Routing + waypoint table | NOT_STARTED | | | | deps A2 |
| A6 | Weather along route | NOT_STARTED | | | | deps A2, A5 |
| A7 | Candidate assembler + source health | NOT_STARTED | | | | deps A3–A6; completes A-side of G2 |
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
| B6 | Commitment points | NOT_STARTED | | | | deps B4 |
| B7 | Flip boundaries | NOT_STARTED | | | | deps B4, B6 |
| B8 | Coverage grader | DONE (awaiting review) | stream-b | 8 tests | session-B8 | `app/engine/coverage.py`; ripening omitted unless `ripening_enabled` |
| B9 | Triggers / hysteresis / recompute / diff | NOT_STARTED | | | | deps B5–B8 |
| B9b | Template explainer + number check | NOT_STARTED | | | | deps B5, B9 |
| B10 | Backtest harness (P1) | NOT_STARTED | | | | deps A8, B5 |

### Stream C — Agent, API, Telegram, UI
| ID | Module | Status | Branch/commit | Tests | Last updated | Notes |
|---|---|---|---|---|---|---|
| C1 | FastAPI + DB wiring + dev stubs | DONE (awaiting review) | stream-b | 11 tests | 2026-10-31 | `app/api/main.py`, `app/agent/flow.py`; live POST /plan returns A/B/C in 39.7s |
| C2 | Telegram integration | NOT_STARTED | | | | deps M0; live-location only if A1 verifies |
| C3 | LangGraph orchestrator | NOT_STARTED | | | | deps C1, A7, B5, B8, B9b |
| C4 | Watchdog | NOT_STARTED | | | | deps C1, C3, B9 |
| C5 | Streamlit UI (4 screens) | NOT_STARTED | | | | deps C1 |
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
| 1 | data.gov.in mandi prices | ✔ (fields) | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | one external project reported it down during a hackathon; unit (₹/qtl?) unverified |
| 2 | Open-Meteo forecast | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | free non-commercial, <10k/day |
| 3 | Open-Meteo archive | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | |
| 4 | Open-Meteo historical-forecast | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | host from memory |
| 5 | Open-Meteo Flood (proxy) | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | river-flow proxy only |
| 6 | OSRM demo (route, alternatives) | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | ≤1 req/s |
| 6b | OSRM `table` | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | needed for commit points |
| 6c | OSM-DE OSRM (fallback) | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | path from memory |
| 7 | Nominatim | ✔ | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | UA required, ≤1 req/s |
| 7b | Geocoding fallbacks (Open-Meteo, Photon) | partial | NOT TESTED | NOT TESTED | NOT TESTED | — | NOT TESTED | |
| 8 | Telegram send + inline buttons | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | |
| 8b | Telegram callback_query | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | |
| 8c | Telegram LIVE location edits | UNKNOWN | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | **P1 unless it passes** |
| 9 | Groq LLM (optional) | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | endpoint from memory |
| 9b | Anthropic LLM (fallback) | partial | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | |

**Decision D-A1 (fill after running the checker):** `<mandi source verdict + chosen scope: live | replay-led | pivot> — decided by <name> at <time>`

**Facts to record from real responses (A1/A3):** newest `arrival_date`: `__` · number of markets for tomato in origin state: `__` · price unit verified: `yes/no` · record keys: `__` · data.gov.in rate/HTTP behaviour: `__` · Telegram live-location updates received: `yes/no`.

---

## 4. Decisions log (append-only; D-01…D-14 are pre-agreed in PLAN.md §2)

| ID | Date | Decision | By | Reason |
|---|---|---|---|---|
| D-01…D-14 | pre-hackathon | See PLAN.md §2 | team | |
| D-A1 | | (mandi source verdict) | | |

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


### 2026-10-31 · Stream A · session #5 · D-A13 grade-only matching + tzdata · status IN_PROGRESS
- Human decisions recorded: approve 24h cuts; grade-only matching (D-A13); tzdata dependency added; Telegram token: human creating.
- D-A13: `app/data/mandi.py` adds `select_grade`, `grade_support`; `group_by_mandi` now treats None as wildcard; `assemble.py` selects by GRADE, shows variety per mandi, warns "varieties differ". `selected_variety` is now None by default; pin grade for monitoring (`refresh_for_monitoring(..., grade=...)`).
- Tests: 114 passed in Stream A sandbox (mocked). NOT yet run against the merged repo (contracts/config there differ from the temp copies Stream A used).
- Security: `t.py` (diagnostic with a real key) was committed -> human to remove + rotate key. `check_apis.py` is in repo (not seen by Stream A).
- Add `tzdata>=2024.1` to requirements.txt (engine B1 uses zoneinfo("Asia/Kolkata"); fails on Windows without it).
## Paste into PROGRESS.md

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
