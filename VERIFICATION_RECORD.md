# VERIFICATION_RECORD.md — Batch 1 (M0 + B1 + B2 + B3)

Generated 2026-10-30 17:45 by running commands in Claude's sandbox. Every number below was **printed by a command and pasted here by a script** (not typed from memory).
"Verified" in this file means exactly one thing: *I executed it and saw this output.* It does **not** mean "works on your machine" or "correct in the real world".

**Environment used:** Python 3.12.3 | pydantic 2.14.2;pytest 9.2.0;  
**Not yet run on:** your laptop, your Python version, Windows/Mac, any code of Person 2.

---
## 1. Files in this batch (all 24 were written by me; nothing here comes from your own repo)

| Path | Lines | Tests in file | What it is |
|---|---:|---:|---|
| `.env.example` | 9 |  | Template for secrets/settings. You copy it to `.env` and fill it. Contains NO real keys. |
| `app/__init__.py` | 0 |  | empty package marker |
| `app/config.py` | 133 |  | ALL tunable numbers (assumptions registry) + tiny `.env` loader. Holds the 2 `TODO(PERSON-2-RESULT)` flags. |
| `app/contracts/__init__.py` | 42 |  | Single import point: `from app.contracts import ...` |
| `app/contracts/events.py` | 36 |  | DispatchProgress (truck updates) and SourceStatus (API health). |
| `app/contracts/evidence.py` | 51 |  | Kind (8 labels), Evidence (receipt for a number), LatLon. |
| `app/contracts/inputs.py` | 138 |  | DispatchRequest (form input), Costs, enums, and what Person 2 must hand us per mandi (CandidateInputs, PriceBand, RouteOption, Waypoint, WeatherSample). |
| `app/contracts/plans.py` | 128 |  | Engine OUTPUT types: PlanSet, Plan, PlanOption, ScenarioResult, RipeningResult, CoverageReport, PlanDiff... |
| `app/engine/__init__.py` | 0 |  | empty package marker |
| `app/engine/netvalue.py` | 97 |  | B3: rupee net value of a load, with every component visible. |
| `app/engine/ripening.py` | 174 |  | B2: tomato ripening exposure from the UC Davis table. |
| `app/engine/units.py` | 104 |  | B1: time/unit helpers, closing-time check, worst-k count and mean. |
| `app/guards.py` | 43 |  | `MG_MODE` switch: live / dev_stub / replay. Blocks fixture data in live mode. |
| `data/reference/ripening_ucdavis.json` | 17 |  | The ripening table (days to full red). DATA, transcribed from a search excerpt - re-check against original. |
| `pytest.ini` | 4 |  | Tells pytest where the code is (`pythonpath = .`) and where tests live. |
| `requirements.txt` | 12 |  | Package list (no versions pinned yet). Added `tzdata` this round (see section 3, item 5). |
| `tests/__init__.py` | 0 |  | empty package marker |
| `tests/engine/__init__.py` | 0 |  | empty package marker |
| `tests/engine/test_netvalue.py` | 87 | 16 | Tests for B3. |
| `tests/engine/test_ripening.py` | 161 | 23 | Tests for B2. |
| `tests/engine/test_units.py` | 94 | 28 | Tests for B1. |
| `tests/helpers.py` | 43 |  | FIXTURE builders (fake requests/costs) used only by tests. |
| `tests/test_config_guards.py` | 91 | 13 | Tests for config.py and guards.py. |
| `tests/test_contracts.py` | 75 | 11 | Tests for the contracts. |

Notes: `PLAN.md` and `PROGRESS.md` in the zip are the *updated* versions of the earlier planning docs (PLAN: ripening flag order corrected; PROGRESS: module rows + session log). They are documents, not code.
Nothing in this batch touches the internet, a database, FastAPI, Telegram, LangGraph or the UI. Those modules are **not written yet**.

---
## 2. What was verified by execution (with evidence)

| # | Claim | Evidence (section) | Result |
|---|---|---|---|
| V1 | The full test suite passes when run from the unzipped copy | §4 | **91 passed, 0 failed** |
| V2 | The tests really detect mistakes (not vacuous) | §5 | 6 of 6 deliberate code breakages made tests fail; code restored → all pass |
| V3 | The split contracts are structurally identical to the contracts text in BLUEPRINT.md | §6 | 24 models compared; **0 structural differences** (only docstring text and module-path prefixes in type names differ) |
| V4 | `app/` code contains no city/state names, no prices, no sample API data | §7 | grep found none (only the word `http` as a field name in `SourceStatus`) |
| V5 | Golden values: PINK 10 h → 10.4 / 13.2 / 20.8 % at 20 / 22 / 25 °C; 26 °C → None; net 51 200; worst-k n=9 → 3 | §4 (named tests) | pass |

## 3. What is NOT verified / known limits (read this before trusting anything)

1. **Ripening table numbers** (`ripening_ucdavis.json`) were copied from a *search-result excerpt* during planning. I have **not** compared them with the original UC Davis page. Tests prove the code reproduces the table; they do not prove the table is right. → *You must check it.*
2. **All delay/offset/threshold values in `config.py` are my chosen assumptions** (tagged in the file). Tests prove the code uses them consistently, not that they are realistic.
3. **Golden numbers in tests (51 200, 10.4 %, …) are checks of the formulas I defined**, computed by the same formulas. If the *formula itself* is conceptually wrong, tests will not notice. Review the formulas in PLAN.md §7 yourself.
4. **Never run on your machine.** Python 3.12 / pydantic 2.14 / pytest 9.2 here. Older versions may behave differently (unknown).
5. **`tzdata` added to requirements.txt**: Python's `zoneinfo` needs the IANA timezone database, which Windows does not ship. This is from my knowledge of Python, **not tested on Windows**. If you are on Linux/Mac you can delete the line.
6. **Simplification baked into `window_status`**: only the clock time on the arrival day is compared with the closing time. Opening time, holidays, overnight arrivals and queues are not modelled.
7. **Docstring examples**: a few comments in `units.py` illustrate rules with sample values (e.g. "14:30", n=9). They are comments only (not used at runtime). Tell me if you want them removed.
8. **Contracts have never been used by any other module yet.** Real problems (missing fields, awkward types) will only show up when B4/A-modules use them. Open question CR-1 (missing weather flag) is logged in PROGRESS.md.
9. **Mutation check covers only 6 breakages**, not all possible bugs.
10. **Not written yet:** B4–B10, C1–C7, all of Stream A. Do not assume any behaviour of those.

---
## 4. Per-test results (raw output of `pytest -v`)

```
tests/engine/test_netvalue.py::test_golden_fixed_trip_cost PASSED
tests/engine/test_netvalue.py::test_per_km_transport_matches_fixed_when_totals_match PASSED
tests/engine/test_netvalue.py::test_per_quintal_per_km_transport PASSED
tests/engine/test_netvalue.py::test_holding_cost_applies_only_when_waiting PASSED
tests/engine/test_netvalue.py::test_breakdown_adds_up PASSED
tests/engine/test_netvalue.py::test_unknown_deductions_are_reported_not_hidden PASSED
tests/engine/test_netvalue.py::test_all_known_means_no_unknown_fields PASSED
tests/engine/test_netvalue.py::test_unknown_hold_cost_flagged_only_if_we_wait PASSED
tests/engine/test_netvalue.py::test_higher_price_gives_higher_net_and_longer_route_gives_lower_net PASSED
tests/engine/test_netvalue.py::test_net_is_linear_in_quantity_when_transport_is_per_qtl PASSED
tests/engine/test_netvalue.py::test_bad_inputs_rejected[0-10-0] PASSED
tests/engine/test_netvalue.py::test_bad_inputs_rejected[-5-10-0] PASSED
tests/engine/test_netvalue.py::test_bad_inputs_rejected[100--1-0] PASSED
tests/engine/test_netvalue.py::test_bad_inputs_rejected[100-10--1] PASSED
tests/engine/test_netvalue.py::test_percentages_over_100_rejected PASSED
tests/engine/test_netvalue.py::test_net_can_be_negative_and_is_not_clamped PASSED
tests/engine/test_ripening.py::test_table_is_well_formed PASSED
tests/engine/test_ripening.py::test_exact_table_points_return_table_values PASSED
tests/engine/test_ripening.py::test_never_extrapolates PASSED
tests/engine/test_ripening.py::test_interpolated_value_lies_between_neighbours PASSED
tests/engine/test_ripening.py::test_unknown_stage_is_an_error PASSED
tests/engine/test_ripening.py::test_rate_vs_day_interpolation_differ_by_at_most_4_percent PASSED
tests/engine/test_ripening.py::test_golden_pink_10_hours[20.0-10.4] PASSED
tests/engine/test_ripening.py::test_golden_pink_10_hours[22.0-13.2] PASSED
tests/engine/test_ripening.py::test_golden_pink_10_hours[25.0-20.8] PASSED
tests/engine/test_ripening.py::test_golden_outside_table_is_none_and_flagged PASSED
tests/engine/test_ripening.py::test_segments_add_up PASSED
tests/engine/test_ripening.py::test_hotter_means_more_consumed_and_earlier_stage_means_less PASSED
tests/engine/test_ripening.py::test_over_limit_flag PASSED
tests/engine/test_ripening.py::test_offset_range_is_reported_for_every_assumed_offset PASSED
tests/engine/test_ripening.py::test_one_offset_out_of_range_flags_whole_result_but_keeps_worst_of_the_valid_ones PASSED
tests/engine/test_ripening.py::test_refrigerated_uses_setpoint_not_outside_weather PASSED
tests/engine/test_ripening.py::test_refrigerated_needs_setpoint PASSED
tests/engine/test_ripening.py::test_golden_mature_green_below_12_5_is_chilling_risk PASSED
tests/engine/test_ripening.py::test_chilling_rule_only_for_mature_green PASSED
tests/engine/test_ripening.py::test_mature_green_at_or_above_threshold_is_not_chilling PASSED
tests/engine/test_ripening.py::test_missing_temperature_is_refused_not_guessed PASSED
tests/engine/test_ripening.py::test_empty_or_negative_segments_refused PASSED
tests/engine/test_ripening.py::test_zero_hour_segment_outside_range_does_not_break_result PASSED
tests/engine/test_units.py::test_seconds_hours_round_trip PASSED
tests/engine/test_units.py::test_parse_hhmm_good[14:30-expected0] PASSED
tests/engine/test_units.py::test_parse_hhmm_good[00:00-expected1] PASSED
tests/engine/test_units.py::test_parse_hhmm_good[23:59-expected2] PASSED
tests/engine/test_units.py::test_parse_hhmm_good[ 09:05 -expected3] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[24:00] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[12:60] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[9:30] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[1430] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[ab:cd] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[12:30:00] PASSED
tests/engine/test_units.py::test_parse_hhmm_bad[-1:30] PASSED
tests/engine/test_units.py::test_naive_datetime_refused PASSED
tests/engine/test_units.py::test_delayed_arrival_formula PASSED
tests/engine/test_units.py::test_delayed_arrival_crosses_midnight PASSED
tests/engine/test_units.py::test_window_unknown_when_no_closing_time PASSED
tests/engine/test_units.py::test_window_met_exactly_at_and_before_closing PASSED
tests/engine/test_units.py::test_window_missed_one_second_late PASSED
tests/engine/test_units.py::test_window_uses_local_time_not_utc PASSED
tests/engine/test_units.py::test_lowest_k_count[9-0.25-3] PASSED
tests/engine/test_units.py::test_lowest_k_count[18-0.25-5] PASSED
tests/engine/test_units.py::test_lowest_k_count[4-0.25-1] PASSED
tests/engine/test_units.py::test_lowest_k_count[1-0.25-1] PASSED
tests/engine/test_units.py::test_lowest_k_count[9-1.0-9] PASSED
tests/engine/test_units.py::test_lowest_k_count[30-0.1-3] PASSED
tests/engine/test_units.py::test_lowest_k_count_rejects_bad_input PASSED
tests/engine/test_units.py::test_mean_of_lowest PASSED
tests/test_config_guards.py::test_default_mode_is_live PASSED
tests/test_config_guards.py::test_stub_forbidden_in_live_and_replay PASSED
tests/test_config_guards.py::test_stub_allowed_in_dev_stub_with_banner PASSED
tests/test_config_guards.py::test_replay_banner PASSED
tests/test_config_guards.py::test_invalid_mode_is_an_error PASSED
tests/test_config_guards.py::test_settings_defaults_are_consistent PASSED
tests/test_config_guards.py::test_person2_flags_default_to_not_verified PASSED
tests/test_config_guards.py::test_settings_are_frozen PASSED
tests/test_config_guards.py::test_bad_delay_ordering_rejected PASSED
tests/test_config_guards.py::test_missing_delay_key_rejected PASSED
tests/test_config_guards.py::test_degrade_after_abstain_rejected PASSED
tests/test_config_guards.py::test_env_loader_reads_values_and_keeps_existing PASSED
tests/test_config_guards.py::test_env_loader_ignores_missing_file PASSED
tests/test_contracts.py::test_version_is_declared PASSED
tests/test_contracts.py::test_request_round_trips_through_json PASSED
tests/test_contracts.py::test_refrigerated_needs_a_setpoint PASSED
tests/test_contracts.py::test_naive_datetime_is_rejected PASSED
tests/test_contracts.py::test_quantity_must_be_positive PASSED
tests/test_contracts.py::test_latlon_ranges PASSED
tests/test_contracts.py::test_percentages_limited_to_0_100 PASSED
tests/test_contracts.py::test_unknown_ripeness_stage_rejected PASSED
tests/test_contracts.py::test_deductions_known_flag PASSED
tests/test_contracts.py::test_evidence_kind_values_are_the_agreed_eight PASSED
tests/test_contracts.py::test_planset_schema_builds_and_defaults_work PASSED
```

## 5. Deliberate-breakage record (I broke the code on purpose; these tests failed; I restored the code)

```
M1 netvalue.py: percentage deduction divided by 10 instead of 100
   -> FAILED tests/engine/test_netvalue.py::test_golden_fixed_trip_cost;FAILED tests/engine/test_netvalue.py::test_per_km_transport_matches_fixed_when_totals_match;FAILED tests/engine/test_netvalue.py::test_breakdown_adds_up;
M2 units.py: arrival exactly at closing time counted as missed
   -> FAILED tests/engine/test_units.py::test_window_met_exactly_at_and_before_closing;
M3 ripening.py: hours divided by 12 instead of 24
   -> FAILED tests/engine/test_ripening.py::test_golden_pink_10_hours[20.0-10.4];FAILED tests/engine/test_ripening.py::test_golden_pink_10_hours[22.0-13.2];FAILED tests/engine/test_ripening.py::test_golden_pink_10_hours[25.0-20.8];
M4 units.py: worst-k count uses floor instead of ceil
   -> FAILED tests/engine/test_units.py::test_lowest_k_count[9-0.25-3];FAILED tests/engine/test_units.py::test_lowest_k_count[18-0.25-5];
M5 ripening.py: extrapolation allowed outside 12.5-25 C
   -> FAILED tests/engine/test_ripening.py::test_never_extrapolates;FAILED tests/engine/test_ripening.py::test_golden_outside_table_is_none_and_flagged;FAILED tests/engine/test_ripening.py::test_one_offset_out_of_range_flags_whole_result_but_keeps_worst_of_the_valid_ones;
M6 guards.py: stubs allowed in live mode
   -> FAILED tests/test_config_guards.py::test_stub_forbidden_in_live_and_replay;
```

## 6. Contract equivalence check (BLUEPRINT.md text vs split package)

Method: extract the python block from BLUEPRINT.md, import it as `orig_contracts`, import `app.contracts`, compare every model.
Result printed by the script:
```
models in BLUEPRINT text: 24   models in split package: 24
only in one side: none
schemas that still differ after ignoring docstring text: none
field-level differences: only module-path prefix in type names (orig_contracts.X vs app.contracts.<file>.X) and `float | None` vs `Optional[float]` (same meaning)
```
(An earlier comparison *without* ignoring docstrings reported 21 differing models — that was only because I added explanatory docstrings to the split files.)

## 7. "No hardcoded data in app/" scan

Command: `grep -rniE "delhi|mumbai|lucknow|kanpur|pune|nashik|azadpur|vashi|uttar|maharashtra|karnataka|http" app/ --include=*.py`
Result: only `app/contracts/events.py: http: Optional[int]` (a field name). Numeric literals left in engine code are unit constants/guards: 3600 (seconds/hour), 24 (hours/day), 100 (percent), 0/1 comparisons.
The only *tunable* numbers in the project are in `app/config.py` (tagged) and the reference JSON table.

## 8. Placeholders waiting for Person 2

`grep -rn "PERSON-2-RESULT" app/` →
```
app/config.py:12:    TODO(PERSON-2-RESULT)
app/config.py:87:    # TODO(PERSON-2-RESULT): set to True ONLY after Person 2 confirms the price unit
app/config.py:90:    # TODO(PERSON-2-RESULT): set to True ONLY if Person 2's Telegram test (A1) received
```

## 9. How YOU can reproduce every check

```
pip install pydantic pytest tzdata
python -m pytest -q                 # expect: 91 passed
python -m pytest -v                 # per-test list
```
To confirm tests catch bugs: edit one line (e.g. in `netvalue.py` change `/ 100.0` to `/ 10.0`), run pytest, expect failures, then undo.

## 10. Things only a human can judge (please decide, don't assume)

- Is the ripening-flag priority `chilling_risk > out_of_range > over_limit > ok` what you want?
- Is "unknown deduction = 0 but flagged" acceptable, or should an unknown deduction block the calculation?
- Is the window rule in item 3.6 good enough for the demo?
- CR-1: add `"missing_weather"` to the ripening flag, or handle missing temperatures differently in B4?

---
## 11. Corrections made while writing this record (my own errors, found by checking)

1. `PROGRESS.md` originally said M0 = 36 tests, B1 = 38, B2 = 24, B3 = 13. Those numbers were **written from memory and were wrong**. Real counts from `pytest -v` (§1 table): contracts 11 + config/guards 13 = **24 (M0)**, units **28 (B1)**, ripening **23 (B2)**, netvalue **16 (B3)**; total **91**. PROGRESS.md has been corrected.
2. The first zip accidentally contained `.pytest_cache/` files. Removed.
3. PROGRESS.md said "4 deliberate breakages"; this round I ran 6 (§5).
