# AGENT.md — Standing Instructions for Every Claude Session (MandiGuard)

> **Read order at the start of EVERY session:** `AGENT.md` (this file) → `PLAN.md` → `BLUEPRINT.md` → `PROGRESS.md`.
> These four files are the team's shared memory. Sessions cannot see each other. If it is not written in these files or committed to the repo, it does not exist for the next session.

---

## 1. VISION LOCK (immutable — do not reinterpret, extend, or "improve")

**MandiGuard** is a contingency-first, climate-aware dispatch decision-support system for perishable produce (MVP crop: **tomato only**), for FPO dispatchers / traders in India.

> **Core promise:** *Every dispatch plan ships with the conditions under which it should be abandoned. We watch for those conditions after dispatch, recompute from fresh data, show exactly what changed, and ask a human before changing anything.*

What makes it different (and the ONLY things worth protecting if time runs short):
1. **Plan A / B / C** where B is chosen by *Plan A's failure scenarios* (not "second best"), and C is hold/delay.
2. **Commitment points** (last feasible diversion waypoint, approximate, scenario-labelled) and **flip boundaries** (delay / price at which B beats A).
3. **Post-dispatch monitoring** with trigger rules, hysteresis, recompute and a **deterministic structured diff**.
4. **Evidence coverage**: every number tagged `observed | forecast | historical | proxy | assumption | computed | user_input | what_if`; recommendation grade `ACTIONABLE / ACTIONABLE_WITH_CAVEATS / ADVISORY_ONLY / ABSTAIN`.
5. **Human approval** before dispatch and before any destination/route change.

What it is **NOT**: a mandi price comparison app, a weather dashboard, a chatbot, a profit calculator, a marketplace, an FPO pooling tool. (NetBhav, Kisan Sabha, Kisan Rath and e-NAM already cover price comparison, freight booking and basic net-realization. Do not rebuild them as the headline.)

Stack: **Python 3.11+, FastAPI backend, SQLite, LangGraph (single orchestrator), Streamlit UI, Telegram Bot API.**

---

## 2. NON-NEGOTIABLES

| # | Rule |
|---|---|
| NN-1 | **No fabricated data at runtime.** No hardcoded prices, weather, routes or "sample" API responses in `app/`. Fixtures exist ONLY under `tests/` and in the guarded `dev_stub` mode (see NN-2). |
| NN-2 | `MG_MODE` env var: `live` (default, demo) · `dev_stub` (fixtures allowed, UI shows a red **"FIXTURE — NOT LIVE"** banner) · `replay` (historical real data, UI shows **"HISTORICAL REPLAY — NOT LIVE"**). In `live` mode any stub provider MUST raise. |
| NN-3 | **Never call daily data "real-time".** Mandi prices are daily records: always show the record's `arrival_date` and `fetched_at`. |
| NN-4 | **Observed min/modal/max ≠ forecast.** UI labels: "Low / Typical / High price *reported today*" — never "predicted range". |
| NN-5 | **River discharge ≠ road flooding.** Wording: "river-flow alert (proxy)". Advisory only; never triggers an automatic replan. |
| NN-6 | **Ripening ≠ spoilage ≠ price loss.** Feature name: "Tomato Ripening Exposure". Never output a ₹ penalty or "quality loss %" from it. Out-of-table temperatures → "outside evidence range", never extrapolate. |
| NN-7 | **Arrivals ≠ traded quantity ≠ unsold stock ≠ market absorption.** Market-absorption / arrival-to-trade features are REMOVED. Do not reintroduce. |
| NN-8 | **The LLM never computes money, times, distances, feasibility, thresholds or flip points.** All numbers come from deterministic Python. LLM may only: parse intake text, choose among registered tools, and paraphrase a structured diff. A deterministic template explainer MUST exist and be the default when the LLM is unavailable or fails the number check. |
| NN-9 | **No invented confidence percentages.** Use coverage grades and scenario counts ("A is best in 7 of 9 tested scenarios"). |
| NN-10 | **Unknown ≠ zero.** Missing mandi closing time, deductions, price record, weather → flagged in coverage and downgrade/abstain. Never silently default. |
| NN-11 | **Humans approve** dispatch and every destination/route change. The watchdog may recompute automatically; it may never act automatically. |
| NN-12 | **Secrets** live in `.env` (git-ignored). Never print, log, commit or paste tokens/API keys. Redact in reports. |
| NN-13 | **Respect API policies**: OSRM demo ≤ 1 req/s, Nominatim ≤ 1 req/s + identifying User-Agent + cache everything, Open-Meteo non-commercial free tier (attribute CC BY 4.0). |
| NN-14 | **Label every claim** in docs, UI and answers as `VERIFIED` (tested/read in a primary source), `ASSUMPTION`, or `UNKNOWN`. Do not call a feature "novel/unique"; say "we found no tool doing X in our searches". |
| NN-15 | **Units**: quantity in **quintals** (1 qtl = 100 kg); prices in **₹ per quintal** (verify unit from source at A3; if unverified set `unit_verified=False` and flag); distance km; durations seconds internally, hours in UI; datetimes timezone-aware (Asia/Kolkata for display). |

---

## 3. SCOPE LOCK

### IN (build these)
Live mandi price ingestion · geocoding with cache + manual coords table · OSRM routes/alternatives/table · Open-Meteo hourly weather along route · scenario grid (9 money scenarios) · Plan A/B/C · commitment points · flip boundaries · tomato ripening exposure · evidence ledger + coverage grader · FastAPI · LangGraph orchestrator (one agent) · critic + number check + template explainer · Telegram dispatch / approval / check-in buttons · watchdog + hysteresis · recompute + diff · Streamlit UI (4 screens) · labelled what-if controls · source-health strip.

### P1 (only after all P0 modules are DONE and integrated)
Historical replay (real past data, labelled) · backtest of regret vs naive baselines · Flip Finder · Telegram live-location (only if A1 verified it works) · price-change quantiles **only if they beat persistence** on a temporal split.

### OUT / REMOVED (do not build, do not propose again)
Market-absorption / market-pressure scoring · arrivals-based predictions · feeder-region supply-shock predictor · FPO pooling · OR-tools / VRP · LSTM / TFT / any deep model · satellite imagery · voice / Hindi UI · multiple crops · mobile app · payments · multi-agent swarms · price ML as a headline feature · any paid API as a core dependency.

### Scope-change protocol
If the human asks for something OUT/P1 or not listed: (1) do **not** implement it; (2) append it to `PROGRESS.md → Scope Requests` with a one-line cost/benefit; (3) tell the human it is parked and name what it would displace; (4) continue the assigned module. Only an explicit human message "approve scope change SR-n" overrides this.

---

## 4. SESSION PROTOCOL

### 4.1 Start (always, in order)
1. Read the four files. Identify your **stream** (A, B or C — given by the human; if not given, ask once).
2. `git pull` (if you have a repo). Re-read `PROGRESS.md` — it may have changed.
3. Check **Gate status** and **Module tracker**. Choose the next module **in your stream** whose dependencies are DONE (see `BLUEPRINT.md`). If the human names a module, do that one.
4. **Claim it**: set status `IN_PROGRESS`, your stream, timestamp, in PROGRESS.md (commit immediately if possible).
5. Run existing tests to learn the baseline.
6. State in one short paragraph: stream, module, what you will deliver, what you will NOT touch.

### 4.2 During
- Work **only** inside your stream's owned paths (§5). Code against `app/contracts/` exactly as written.
- Write tests first or alongside. A module is not DONE without its required tests (`BLUEPRINT.md`).
- Whenever you are tempted to add a feature, run the **Anti-Drift Checklist** (§8).
- If blocked by another stream's unfinished module, code against the contract with a test-only fixture and mark the dependency in PROGRESS.md — do not wait, do not reimplement their module.
- If an API fails: do NOT substitute fake data. Implement graceful degradation (stale-with-timestamp or "unavailable"), record the failure in PROGRESS.md → Blockers.

### 4.3 End (always)
1. Run the module tests; run the full suite.
2. Update `PROGRESS.md`: module status (`DONE` only if Definition of Done met), files touched, tests added/passing, decisions made, assumptions introduced, open issues, **"Next module for this stream"**, and append a **Session Log** entry.
3. Commit with message `[stream-X][module-ID] <what>`; push your branch; open/merge PR per §5.4.
4. Give the human a **HANDOFF BLOCK** (§9.3) they can paste into PROGRESS.md if you have no repo access.

---

## 5. PARALLEL WORK & OWNERSHIP

### 5.1 Streams (one human + one Claude session each)
| Stream | Owner theme | Owns (only this stream edits) |
|---|---|---|
| **A — Data & Integrations** | Live APIs, cache, evidence, candidate assembly, historical data | `app/data/`, `app/db/`, `scripts/`, `data/reference/mandi_coords.csv`, `tests/data/` |
| **B — Decision Engine** | Pure deterministic logic: ripening, net value, scenarios, plans, commit points, flips, triggers, diff, coverage, explainer | `app/engine/`, `app/explain/`, `data/reference/ripening_ucdavis.json`, `tests/engine/` |
| **C — Agent, API, Telegram, UI** | FastAPI, LangGraph, Telegram, watchdog, Streamlit, demo | `app/api/`, `app/agent/`, `app/integrations/`, `ui/`, `tests/api/`, `tests/e2e/` |
| Shared (frozen) | Contracts, config, guards | `app/contracts/`, `app/config.py`, `app/guards.py` — change only via a **Contract Change Request** |

### 5.2 Contract Change Request (CR)
Contracts are the only coupling between streams. After Gate **G0** they are frozen. To change: add a CR in `PROGRESS.md → Contract Changes` (what, why, affected streams, backward-compatible? y/n), get the human's OK, make the change in ONE small commit touching only `app/contracts/` + the changelog, announce in the Session Log. **Additive optional fields only** after G1; breaking changes only with all three humans' agreement.

### 5.3 Rules that keep sessions independent
- Never edit another stream's files. If you find a bug there: write a failing test in your own `tests/` dir **or** add an issue to `PROGRESS.md → Blockers` addressed to that stream.
- Stream B imports nothing from `app/data/` or `app/api/` (pure functions on contract types). Stream A imports nothing from `app/engine/`. Stream C imports from both through their public functions listed in `BLUEPRINT.md`.
- Public function signatures in `BLUEPRINT.md` are the API. Do not rename them.

### 5.4 Git workflow
Branches `stream-a`, `stream-b`, `stream-c` → merge to `main` via small PRs when a module is DONE or at a Gate. Rebase on `main` before merging. `PROGRESS.md` conflicts: each stream edits only its own section plus appends to the Session Log; on conflict keep both sides.

---

## 6. CODING STANDARDS
- Python 3.11+, type hints everywhere, **pydantic v2** models at all boundaries, `from __future__ import annotations`.
- Sync code (`requests`, plain `def` FastAPI endpoints); background work via `threading` (watchdog, Telegram polling). No async unless unavoidable.
- Pure functions in `app/engine/`: no I/O, no `datetime.now()` (take `now` as a parameter), no randomness. Deterministic tie-breaks as specified.
- Every external call: timeout (≤ 20 s), 1 retry with backoff, cache, rate-limit, log to `source_calls`, return an `Evidence` object with `as_of` and `fetched_at`.
- Configuration thresholds only in `app/config.py` (the **Assumptions Registry**, see `PLAN.md §6`). No magic numbers in logic.
- Tests: `pytest`; HTTP mocked with `responses` in tests only. Name fixtures `FIXTURE_*` and put "FIXTURE — not observed data" in docstrings.
- Logging via `logging`; never log secrets. Keep functions < 60 lines; explicit over clever.
- Dependencies: only those in `PLAN.md §3`. Ask before adding any. Pin versions in `requirements.lock` after the first working install.

---

## 7. WORDING RULES (UI + explanations)
Approved labels: "Price reported today (record date: …)" · "Forecast issued/as of …" · "Estimated travel time (OSRM, no traffic)" · "Assumed delay stress (adjustable)" · "River-flow alert (proxy, not road flooding)" · "Ripening window consumed (range; tomato, UC Davis table)" · "Outside evidence range" · "Closing time unknown — window not checked" · "A is best in k of 9 tested scenarios" · "Last feasible diversion waypoint under *scenario* (approximate)" · "Historical replay — not live" · "What-if scenario (user-set) — not observed".
Forbidden: "real-time prices", "predicted price range", "road flooded", "spoilage %", "quality loss", "confidence 87%", "guaranteed", "AI-optimised profit", "unique / no competitor".

---

## 8. ANTI-DRIFT CHECKLIST (run before each commit and whenever tempted to add something)
1. Does this serve the Vision Lock §1 items 1–5? If not → parked.
2. Is it in IN (or P1 with all P0 done)? If it is in OUT → stop.
3. Does it need data I have not verified exists? → mark UNKNOWN, ask, or drop.
4. Could it make a number look more precise than its evidence? → show a range/label or remove.
5. Am I letting the LLM compute or decide a number? → move to Python.
6. Does it add a dependency/service? → needs human OK.
7. Will it still work in `live` mode without fixtures? → required for DONE.
8. Does it touch another stream's files or a contract? → don't / file a CR.
9. Is the time cost > its demo value? → park in Scope Requests.
10. Would a skeptical judge asking "where does this number come from?" get an answer from the Evidence ledger?

If any answer is wrong, fix or park. Do not "just add it quickly".

---

## 9. APPENDICES

### 9.1 Copy-paste first message for a new session
```
Attach/paste AGENT.md, PLAN.md, BLUEPRINT.md, PROGRESS.md (and the repo if available).
I am Stream <A|B|C>. Follow AGENT.md §4.1: read everything, pull latest, pick the next
unblocked module in my stream (or work on module <ID> if I name it), claim it in PROGRESS.md,
then implement it with tests. Do not touch other streams' files. Do not add scope.
At the end, update PROGRESS.md and give me a HANDOFF BLOCK.
```
Stream-specific add-ons:
- **A:** "Start with A1 (run scripts/check_apis.py on my machine and help me interpret the results) before any integration code."
- **B:** "Start with B1/B2 (they have no dependencies). Reference tests in BLUEPRINT are golden — make them pass exactly."
- **C:** "Start with M0 (scaffold + contracts exactly as in BLUEPRINT.md) — the other two streams are waiting on Gate G0."

### 9.2 Resume prompt (continuing a half-finished module)
```
Resume Stream <X>. Read the four files; open PROGRESS.md → Session Log and the module tracker.
Module <ID> is IN_PROGRESS with notes there. Verify what exists in the repo vs the notes,
finish the module, meet its Definition of Done, update PROGRESS.md, hand off.
```

### 9.3 HANDOFF BLOCK (paste into PROGRESS.md → Session Log if you cannot commit)
```
### <date-time IST> · Stream <X> · Module <ID> · status <NOT_STARTED|IN_PROGRESS|BLOCKED|DONE|VERIFIED>
- Delivered: <files / functions>
- Tests: <names> — <pass/fail counts>
- Decisions: <D-nn: …>
- New assumptions (added to config registry?): <…>
- Blockers / questions for other streams: <…>
- Next module for this stream: <ID>
```

### 9.4 If the human says "just make it better / add X"
Answer with: the Vision Lock sentence, the anti-drift checklist result, and either implement (if in scope) or log a Scope Request. Do not silently expand.
