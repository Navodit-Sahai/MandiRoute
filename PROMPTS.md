# Starter prompts (copy-paste as the FIRST message of a session; replace <MODULE>)

## STEP 0 — CR-5 "ripening off" (ONE short session, ~10 min, on `main` before creating stream-b / stream-c). Any agent.
```
Read AGENTS.md section "RIPENING IS OFF". Make ONLY the contract/config change CR-5: in app/contracts make PlanOption.ripening Optional[RipeningResult]=None and DispatchRequest.ripeness Optional[RipenessStage]=None (remove/adjust the validator lines that use it); add RIPENING_ENABLED=False (field `ripening_enabled`) to the frozen Settings in app/config.py. Update tests/test_contracts.py and tests/test_config_guards.py so they pass. Do NOT touch app/engine/ripening.py. Run `python -m pytest -q`, commit "[cr-5] ripening off (optional fields + flag)", stop.
```

## Engine agent (Antigravity agent 1) — queue: B8 → B4 → B5 → B6 → B7 → B9 → B9b  (B1–B3 appear DONE: verify with `pytest tests/engine -q`)
```
Read AGENTS.md, then PROGRESS.md. You are the ENGINE agent (Stream B). Your module this session: <MODULE>.
Read only BLUEPRINT.md section for <MODULE> and the PLAN.md sections it cites. Edit only app/engine, app/explain, tests/engine.
Write the golden tests from the blueprint FIRST (label inputs FIXTURE_*), then implement until `python -m pytest tests/engine -x -q --tb=short` is green.
Public function names/signatures must match BLUEPRINT §2 exactly. Do not touch contracts/config. Then update PROGRESS.md and stop.
```
Order rationale: B8 (coverage, small) → B4 (scenario grid; deps done) → B5 (plans; **ask for Claude Code review after**) → B6 → B7 → B9 (**review**) → B9b.

## API/UI agent (Antigravity agent 2) — queue: C1 → C2 → C3-lite → C4 → C5
```
Read AGENTS.md, then PROGRESS.md. You are the API/UI agent (Stream C). Module this session: <MODULE>.
(C3-lite = plain state-machine orchestrator in app/agent/flow.py: intake -> assemble_candidates -> build plans -> coverage -> template summary; NO LangGraph, NO LLM.)
Read only BLUEPRINT.md section for <MODULE> + PLAN.md §9 (endpoints) / §10 if relevant. Edit only app/api, app/agent, app/integrations, app/ui, tests/api, tests/agent.
Use `app.data.assemble.assemble_candidates/refresh_for_monitoring/source_health` for live data; engine functions per BLUEPRINT §2. Until engine modules are DONE, use stubs ONLY when MG_MODE=dev_stub (banner "FIXTURE — NOT LIVE").
Verify by running it (uvicorn / streamlit) and checking the result in the browser. Then update PROGRESS.md and stop.
```
C5 split into 3 sessions: (1) Plan screen, (2) Compare screen (map + A/B/C cards + evidence expander), (3) Dispatch & Monitor (approve, check-in, manual tick, diff, red WHAT-IF box).

## Claude Code — REVIEW ONLY (≈3 short sessions; quota is scarce)
```
Read AGENTS.md. Review ONLY `git diff main...stream-b -- app/engine tests/engine` (or stream-c) for module <MODULE> against PLAN.md <§>.
Report ONLY concrete bugs / deviations from the normative formulas / missing golden tests, each with file:line and a 3-line fix. Do not rewrite files, do not refactor, do not review style. Max 40 lines of output.
```
Sessions: after B5 (~H10) · after B9 (~H18) · rescue for the first G2/G3 failure.

## Schedule (hours from now)
0–1 merge + setup · 1–5 B8, B4 / C1, C2 · 5–10 B5, UI Plan+Compare · **~H10–12 G2: live POST /plan (human runs with real keys)** · 12–18 B6, B7, B9, B9b / state machine, watchdog, Dispatch screen · **18–20 G3 dispatch loop, FREEZE at H20** · 20–24 rehearsal ×2, kill each API once, backup demo path.
Kill rules: no vertical slice by H14 → cut UI to Plan+Compare only; G3 not working by H20 → demo approve + check-in + manual tick only.
