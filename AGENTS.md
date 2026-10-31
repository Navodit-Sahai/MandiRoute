# AGENTS.md — rules for every coding-agent session (Antigravity reads this; Claude Code reads CLAUDE.md = same text)
Project: MandiGuard (tomato dispatch decision support, India). **24 hours left. Be fast, small, correct.**

## Read order (STOP reading once you have your module — do not read whole files you don't need)
1. `PROGRESS.md` (status + open issues) → find your module; claim it (status IN_PROGRESS).
2. `AGENT.md` §1–3 (vision lock, non-negotiables NN-1..NN-15, scope) and §7 (wording). Skip the rest.
3. ONLY your module's section in `BLUEPRINT.md` §3 and the PLAN.md § it cites (e.g. B4 → PLAN §7.2–7.3).
4. Contracts live in `app/contracts/` — import them, never edit. Same for `app/config.py`, `app/guards.py`.

## Ownership (never edit outside your folders; open a note in PROGRESS.md instead)
| Agent | Folders it may edit |
|---|---|
| Engine (B) | `app/engine/`, `app/explain/`, `tests/engine/`, `data/reference/ripening_ucdavis.json` |
| API/UI (C) | `app/api/`, `app/agent/`, `app/integrations/`, `app/ui/`, `tests/api/`, `tests/agent/` |
| Data (A, done) | `app/data/`, `app/db/`, `scripts/` — bug reports only, do not edit |
Engine imports NOTHING from `app/data` or `app/api`. API/UI imports engine+data only through the public functions in BLUEPRINT §2.

## Working method (saves tokens)
- ONE module per session. Write the golden tests from BLUEPRINT first, then code until `python -m pytest <your tests> -x -q --tb=short` passes. Show only failing output.
- Pure functions on contract types; no I/O in engine. No new dependencies without asking the human. Type hints, pydantic v2, `from __future__ import annotations`.
- Never reuse a variable name for a different type inside one function (this caused a real bug).
- No fixtures/stubs in live code paths (NN-1). Test data is labelled `FIXTURE_*`. Never print/commit secrets (NN-12).
- Every number shown to a user needs an Evidence id and a kind tag. UI wording per AGENT §7.

## 24-hour scope cuts (human-approved)
No P1 (replay, backtest, Flip Finder, price-change prediction). **No LangGraph, no LLM** → plain state machine + template text (same numbers). UI = 3 screens (Plan, Compare, Dispatch & Monitor). Telegram if token works, else in-app approve + check-in button. Ripening removed (SR-1, see below). Data rule D-A13: compare mandis on **grade only**, variety shown per mandi.

## RIPENING IS OFF (scope request SR-1, human-approved) — read before touching B4/B5/B8/B9/UI
Do NOT delete `app/engine/ripening.py`, its tests or the reference JSON (done, tested; re-enable later). Just make nothing depend on it:
- Contracts (one-time CR-5, done FIRST by one session, merged before both agents branch): `PlanOption.ripening: Optional[RipeningResult] = None`; `DispatchRequest.ripeness: Optional[RipenessStage] = None` (and drop its use in validators). Config: `RIPENING_ENABLED = False` (frozen settings field).
- B4 `evaluate_option`: no ripening call, set `ripening=None`. B5: no ripening eligibility filter, no staging-ripening for Plan C, no ripening line in `reconsider_if`. B9: trigger T4 (ripening) disabled; no ripening item in `PlanDiff`. B8: the `ripening` coverage dimension is NOT counted as a caveat when disabled (show nothing). B9b: no ripening text. All guarded by `if settings.ripening_enabled:` so it can be turned back on.
- UI/API: no ripeness/ripening-limit inputs, no ripening card, no ripening rows in the assumptions panel. Never claim anything about quality, spoilage or shelf life.
- Still in: price scenarios, delay scenarios, window feasibility, rain exposure, flips, commit points, triggers T1-T3/T5, replan diff, evidence. Weather along the route stays (rain).

## Branches & commits
Branch per agent: `stream-b`, `stream-c`. Commit with path-limited adds (`git add app/engine tests/engine`). Never `git add -A`. Never commit `.env`, `*.db`, scratch files.

## End of every session (always)
Append to PROGRESS.md "Session Log": module, files, tests run (counts), assumptions, blockers, next module. Run `ruff check` on your folders. Stop; do not start the next module in the same session.
