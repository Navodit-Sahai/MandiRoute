# BLUEPRINT.md — MandiGuard: Module-by-Module Build Spec

Read `AGENT.md` and `PLAN.md` first. This file is the **interface and work breakdown**. Public function signatures here are the only coupling between streams.

Module status lives in `PROGRESS.md`. A module is **DONE** only when: code merged on your branch · required tests pass · `MG_MODE=live` path works without fixtures · contract types respected · PROGRESS.md updated.

---

## 0. Repository layout

```
mandiguard/
  AGENT.md  PLAN.md  BLUEPRINT.md  PROGRESS.md
  .env.example   requirements.txt   requirements.lock   README.md
  scripts/check_apis.py                      # Stream A (already written)
  data/reference/ripening_ucdavis.json       # Stream B (real published table)
  data/reference/mandi_coords.csv            # Stream A (verified geocodes only)
  app/
    config.py            # Assumptions Registry (shared, frozen after G0)
    guards.py            # MG_MODE guard (shared, frozen after G0)
    contracts/           # shared, frozen after G0  (section 1 below)
    db/                  # Stream A: schema.sql, db.py
    data/                # Stream A: http.py mandi.py geocode.py routing.py weather.py assemble.py health.py history.py
    engine/              # Stream B: units.py ripening.py netvalue.py scenarios.py plans.py commit.py flips.py coverage.py triggers.py recompute.py
    explain/             # Stream B: templates.py numbercheck.py
    agent/               # Stream C: state.py tools.py graph.py llm.py watchdog.py
    integrations/        # Stream C: telegram.py
    api/                 # Stream C: main.py routes.py deps.py stubs.py (dev_stub only)
  ui/streamlit_app.py    # Stream C
  tests/{data,engine,api,e2e}/
```

`guards.py` contract:
```python
def mode() -> Literal["live","dev_stub","replay"]          # reads MG_MODE, default "live"
def require_stub_allowed(name: str) -> None                # raises RuntimeError in live/replay mode
def banner() -> str | None                                 # None in live; fixture/replay banner text otherwise
```

---

## 1. Shared contracts (`app/contracts/` — **copy exactly; verified to import and validate with pydantic v2**)

Split into files `evidence.py`, `inputs.py`, `plans.py`, `events.py` if you like, with a package `__init__.py` re-exporting everything — but keep names and fields identical. Version: **contracts v1.0**.

```python
from __future__ import annotations
from enum import Enum
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

# ---------------- evidence ----------------
class Kind(str, Enum):
    OBSERVED="observed"; FORECAST="forecast"; HISTORICAL="historical"; PROXY="proxy"
    ASSUMPTION="assumption"; COMPUTED="computed"; USER_INPUT="user_input"; WHAT_IF="what_if"

class Evidence(BaseModel):
    id: str
    kind: Kind
    label: str
    value: float | str | None = None
    unit: Optional[str] = None
    source: str
    as_of: Optional[str] = None          # date/time the DATA refers to
    fetched_at: str                      # ISO UTC when we fetched it
    stale: bool = False
    note: Optional[str] = None

class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

# ---------------- request ----------------
class VehicleType(str, Enum):
    OPEN="open"; COVERED="covered"; REFRIGERATED="refrigerated"

class RipenessStage(str, Enum):   # UC Davis table stages only. 'light red' unsupported in MVP.
    MATURE_GREEN="MG"; BREAKER="BREAKER"; TURNING="TURNING"; PINK="PINK"

class Costs(BaseModel):
    transport_mode: Literal["per_km_per_trip","fixed_per_trip","per_qtl_per_km"]
    transport_rate: float = Field(ge=0)          # Rs per km / Rs per trip / Rs per qtl-km
    commission_pct: Optional[float] = Field(default=None, ge=0, le=100)
    market_fee_pct: Optional[float] = Field(default=None, ge=0, le=100)
    other_pct: Optional[float] = Field(default=None, ge=0, le=100)
    hamali_per_qtl: Optional[float] = Field(default=None, ge=0)
    loading_per_qtl: Optional[float] = Field(default=None, ge=0)
    hold_cost_per_hour: Optional[float] = Field(default=None, ge=0)
    @property
    def deductions_known(self) -> bool:
        return None not in (self.commission_pct, self.market_fee_pct, self.hamali_per_qtl)

class DispatchRequest(BaseModel):
    crop: Literal["Tomato"] = "Tomato"
    quantity_qtl: float = Field(gt=0)            # QUINTALS ONLY (1 qtl = 100 kg)
    origin: LatLon
    origin_label: str
    origin_state: str
    ripeness: RipenessStage
    vehicle: VehicleType
    refrigerated_setpoint_c: Optional[float] = None
    loading_time: datetime                       # timezone-aware
    preferred_departure: datetime                # timezone-aware
    max_distance_km: float = 400
    costs: Costs
    mandi_latest_arrival: dict[str, str] = {}    # mandi_id -> "HH:MM" local; missing = UNKNOWN
    risk_mode: Literal["cvar25","mean"] = "cvar25"
    ripening_limit_pct: float = 25.0             # user threshold; NOT evidence-based
    @model_validator(mode="after")
    def _chk(self):
        if self.vehicle == VehicleType.REFRIGERATED and self.refrigerated_setpoint_c is None:
            raise ValueError("refrigerated vehicle requires refrigerated_setpoint_c")
        for d in (self.loading_time, self.preferred_departure):
            if d.tzinfo is None: raise ValueError("datetimes must be timezone-aware")
        return self

# ---------------- candidate inputs (Stream A -> Stream B) ----------------
class PriceBand(BaseModel):
    low: float; typical: float; high: float      # Rs/QUINTAL: min/modal/max from ONE observed record
    record_date: str                             # arrival_date of that record (ISO)
    variety: Optional[str] = None
    grade: Optional[str] = None
    unit_verified: bool = False                  # True only if unit confirmed from source
    evidence_ids: list[str] = []

class Waypoint(BaseModel):
    idx: int
    point: LatLon
    elapsed_nominal_s: float                     # seconds from departure along THIS route
    to_dest_s: dict[str, float | None] = {}      # mandi_id -> nominal seconds from here (OSRM table); None = unreachable/unknown

class WeatherSample(BaseModel):
    waypoint_idx: int
    time_iso: str                                # hour aligned to nominal ETA
    temp_c: Optional[float] = None
    precip_mm: Optional[float] = None
    evidence_id: str

class RouteOption(BaseModel):
    route_id: str
    distance_km: float
    duration_h_nominal: float
    waypoints: list[Waypoint]
    weather: list[WeatherSample] = []
    geometry_ok: bool = True
    evidence_ids: list[str] = []

class CandidateInputs(BaseModel):
    mandi_id: str
    mandi_name: str
    location: LatLon
    location_precision: Literal["market","district_centroid","unknown"]
    price: Optional[PriceBand] = None
    routes: list[RouteOption] = []
    latest_arrival_local: Optional[str] = None   # "HH:MM"; None = UNKNOWN
    destination_weather: list[WeatherSample] = []

# ---------------- outputs (Stream B -> Stream C) ----------------
class ScenarioResult(BaseModel):
    price_level: Literal["low","typical","high"]
    delay_level: Literal["none","moderate","severe"]
    net_value: float
    arrival_iso: str
    window_status: Literal["met","missed","unknown"]

class RipeningOffsetResult(BaseModel):
    offset_c: float
    cargo_temp_c: float
    consumed_pct: Optional[float]                # None = outside evidence range
    out_of_range: bool

class RipeningResult(BaseModel):
    by_offset: list[RipeningOffsetResult]
    worst_consumed_pct: Optional[float]
    flag: Literal["ok","over_limit","out_of_range","chilling_risk"]

class Condition(BaseModel):
    kind: Literal["eta_slip_h","price_drop_pct","rain_mm_remaining","ripening_pct","window_miss_scenarios"]
    comparison: Literal[">=","<="]
    threshold: float
    rationale: str

class FlipBoundaries(BaseModel):
    delay_flip_pct: Optional[float] = None       # smallest delay (% of nominal duration) on monitored route at which alt beats it
    price_flip_per_qtl: Optional[float] = None   # monitored mandi typical price below which alt wins
    diversion_feasible_at_flip: Optional[bool] = None
    note: Optional[str] = None

class CommitInfo(BaseModel):
    computed: bool
    last_feasible_waypoint: dict[str, Optional[int]] = {}   # scenario -> waypoint idx (None=never feasible)
    note: Optional[str] = None

class PlanOption(BaseModel):
    option_id: str
    mandi_id: str
    mandi_name: str
    route_id: str
    depart_iso: str
    distance_km: float
    duration_h_nominal: float
    rain_exposure_mm: Optional[float]
    scenario_results: list[ScenarioResult]
    score: float
    mean_net: float
    ripening: RipeningResult
    evidence_ids: list[str]

class Plan(BaseModel):
    label: Literal["A","B","C"]
    option: PlanOption
    role_note: str
    reconsider_if: list[Condition] = []
    flip: FlipBoundaries = FlipBoundaries()
    commit: CommitInfo = CommitInfo(computed=False)

class CoverageItem(BaseModel):
    dimension: str
    kind: Kind
    status: Literal["ok","degraded","missing"]
    detail: str

class CoverageReport(BaseModel):
    grade: Literal["ACTIONABLE","ACTIONABLE_WITH_CAVEATS","ADVISORY_ONLY","ABSTAIN"]
    items: list[CoverageItem]
    reasons: list[str]

class PlanSet(BaseModel):
    plan_id: str
    version: int
    created_at: str
    plans: list[Plan]
    robustness_a_best_in: int
    robustness_of: int
    coverage: CoverageReport
    evidence_ids: list[str] = []

class DiffItem(BaseModel):
    term: str
    old: float | str | None
    new: float | str | None
    delta: float | None = None
    effect: str

class PlanDiff(BaseModel):
    old_version: int
    new_version: int
    primary_reason: str
    items: list[DiffItem]
    recommendation_changed: bool

# ---------------- events / monitoring ----------------
class DispatchProgress(BaseModel):
    plan_id: str
    dispatched_at: str
    last_waypoint_idx: Optional[int] = None
    last_position: Optional[LatLon] = None
    reported_delay_min: float = 0.0
    source: Literal["telegram_location","checkin_button","manual_eta","route_estimate"] = "route_estimate"

class SourceStatus(BaseModel):
    name: str
    ok: bool
    http: Optional[int] = None
    latency_ms: Optional[int] = None
    as_of: Optional[str] = None
    fetched_at: Optional[str] = None
    kind: Kind
    note: Optional[str] = None
```

Notes: `Evidence.id` format `ev_<8 hex>` (helper `new_evidence_id()` lives in `app/data/http.py`). Money fields are floats in ₹; seconds are floats; all ISO strings are timezone-aware.

---

## 2. Public interfaces between streams

### Stream A → others (`app/data/assemble.py`, `app/data/health.py`)
```python
class CandidateAssembly(BaseModel):
    candidates: list[CandidateInputs]
    evidence: list[Evidence]          # every number that appears in candidates
    source_status: list[SourceStatus]
    warnings: list[str]               # e.g. "district centroid used for X", "price record 4 days old"
    selected_variety: str | None
    selected_grade: str | None

def assemble_candidates(req: DispatchRequest, now: datetime) -> CandidateAssembly
def refresh_for_monitoring(req: DispatchRequest, plan_option_ids: list[str], now: datetime) -> CandidateAssembly  # fresh data for watchdog
def source_health() -> list[SourceStatus]
```
(`CandidateAssembly` is defined in `app/data/assemble.py`; it is public API but not a shared contract.)

### Stream B → others (`app/engine/*`, `app/explain/*`) — all pure
```python
# units / config helpers
def hours(seconds: float) -> float
def parse_hhmm(s: str) -> tuple[int,int]

# ripening
def days_to_red(stage: RipenessStage, temp_c: float) -> float | None
def ripening_exposure(stage, segments: list[tuple[float, float]],      # (hours, ambient_c)
                      vehicle: VehicleType, setpoint_c: float | None,
                      limit_pct: float, cfg) -> RipeningResult

# money
def net_value(req: DispatchRequest, price_per_qtl: float, distance_km: float, wait_hours: float) -> float

# options and plans
def evaluate_option(req, cand: CandidateInputs, route: RouteOption, depart: datetime, cfg, wait_hours_after: float = 0.0) -> PlanOption
def build_plan_set(req, cands: list[CandidateInputs], evidence: list[Evidence], now: datetime, cfg, plan_id: str, version: int = 1) -> PlanSet

# commitment points & flips
def compute_commit_points(req, plan_a: PlanOption, route_a: RouteOption, alt: PlanOption, alt_cand: CandidateInputs, cfg) -> CommitInfo
def compute_flips(req, plan_a: PlanOption, plan_b: PlanOption, cand_a, cand_b, route_a, cfg) -> FlipBoundaries

# coverage
def grade_coverage(req, cands, evidence, now, cfg) -> CoverageReport

# monitoring
class WatchState(BaseModel): trigger_counts: dict[str,int] = {}; cooldown_until: str | None = None; last_price_record_date: str | None = None
class TriggerDecision(BaseModel):
    fired: list[str]; persisted: list[str]; propose_replan: bool; reason: str
    new_state: WatchState; new_plan_set: PlanSet | None = None; diff: PlanDiff | None = None
def evaluate_triggers_and_recompute(prev: PlanSet, req, fresh_cands: list[CandidateInputs], fresh_evidence, progress: DispatchProgress,
                                    state: WatchState, now: datetime, cfg) -> TriggerDecision

# explanation (deterministic default)
def render_plan_summary(ps: PlanSet, evidence_index: dict[str, Evidence]) -> str
def render_diff(diff: PlanDiff, evidence_index) -> str
def check_numbers(text: str, allowed_values: list[float], tol: float = 0.01) -> list[str]   # returns offending numbers
```

### Stream C consumes the above; exposes HTTP endpoints (PLAN.md §9) and Telegram callback schema:
`callback_data = "<action>|<plan_id>|<version>|<arg>"`, actions: `approve`, `reject`, `checkin` (arg: `passed:<wp_idx>` · `delay:30` · `delay:60` · `arrived`), `replan_ok`, `replan_no`. Max 64 bytes — use short ids.

---

## 3. Module specifications

Notation: **Owner** · **Deps** · **Parallel with** · **Est.** · **DoD** = Definition of Done.

### M0 — Scaffold & contracts (Stream C, ≈ 1.5 h) — **blocks Gate G0**
Create layout above, `requirements.txt`, `.env.example` (`DATAGOV_KEY, TG_TOKEN, TG_CHAT_ID, GROQ_API_KEY, ANTHROPIC_API_KEY, ORS_KEY, MG_MODE=live, DB_PATH`), `app/config.py` (Assumptions Registry from PLAN.md §6 as a frozen pydantic `Settings`), `app/guards.py`, contracts exactly as §1, `pytest` config, `README` with run commands, empty package files, CI-less `make test`.
**DoD:** `python -c "import app.contracts"` ok; `pytest` runs (≥ 1 contract round-trip test); pushed to `main`; PROGRESS gate G0 ticked. Streams A/B start module work that needs no contracts (A1, B1, B2) **before** M0 is merged.

---
### STREAM A — Data & Integrations

**A1 — API verification gate (≈ 1 h, no deps).** Run `scripts/check_apis.py` (with keys) on the human's machine; help interpret; fill `PROGRESS.md → API Verification` with actual PASS/PARTIAL/FAIL, record date of newest mandi record, observed latency, rate-limit notes. Apply the PLAN.md §5 decision rule and write **Decision D-A1** in PROGRESS.md. Also verify: price unit (₹/quintal?) from a returned record or documentation; whether Telegram live location edits arrive.
**DoD:** table filled with real results, decision logged, `api_report.json` kept (keys redacted).

**A2 — HTTP core, cache, ledger (≈ 2 h; deps M0).** `app/data/http.py`: `request_json(source, method, url, params, ttl, rate_limiter, cache_key)` → `(data|None, Evidence-making meta, error|None)`; SQLite-backed cache with TTL; token-bucket limiter per host (OSRM/Nominatim 1/s); 1 retry with backoff; log every call to `source_calls`; `new_evidence_id()`, `make_evidence(kind, label, value, unit, source, as_of, fetched_at, note)` persisting to `evidence`. Never raise on HTTP error — return a structured failure.
**Tests:** cache hit avoids network; limiter spacing ≥ 1 s between calls; failure returns error object; evidence persisted. **DoD** as global.

**A3 — Mandi ingestion & shortlist (≈ 3 h; deps A2).** `app/data/mandi.py`:
```python
def fetch_mandi_records(commodity: str, states: list[str], key: str, now) -> MandiFetch   # records normalised to lowercase keys; arrival_date parsed; failure → empty + SourceStatus(ok=False)
def select_variety_grade(records) -> tuple[str|None, str|None]  # rule: pair present in the largest number of candidate mandis; tie → alphabetical; expose choice to UI
def build_price_band(record_group, now) -> PriceBand           # low=min_price, typical=modal_price, high=max_price from ONE record (newest date); unit_verified from A1 finding
def shortlist(records, origin: LatLon, max_km, k, geocoder) -> list[mandi_id]  # straight-line prefilter (haversine) → top K
```
States queried = origin state + `NEIGHBOUR_STATES` mapping stored in `data/reference/` (human-reviewed). Persist raw records to `price_records` each fetch (gives monitoring history). Sanity checks: price > 0, min ≤ modal ≤ max else mark record suspect (exclude + warning). `mandi_id` = `slug(state|district|market)`.
**Tests (FIXTURE_ via `responses`):** key lowercasing, date parsing (`dd/mm/yyyy`), selection rule determinism, suspect-record exclusion, stale evidence flag when age > `PRICE_DEGRADE_DAYS`.
**DoD:** live call returns ≥ 1 mandi with real `arrival_date` shown in evidence (record in PROGRESS).

**A4 — Geocoding & coordinate table (≈ 2 h; deps A2).** `geocode_mandi(market, district, state) -> GeoResult(lat, lon, precision, display_name, source)`. Order: manual `mandi_coords.csv` (verified rows only) → cache → Nominatim structured query → Open-Meteo geocoding → district centroid (`precision="district_centroid"`) → `unknown`. Ambiguity rule: if top result outside the stated state's bounding region (check `display_name` contains state) → discard. Never invent coordinates. Produce a script `scripts/build_coords.py` that geocodes the ~15 demo mandis once (1 req/s, UA set) and prints rows for the human to **verify on a map** before adding to CSV.
**Tests:** limiter respected; ambiguous/foreign result rejected; precision tags correct. **DoD:** ≥ 10 demo mandis resolved, each with precision label.

**A5 — Routing (≈ 3 h; deps A2).** `get_routes(o, d, alternatives=True) -> list[RouteOption]` via OSRM `route` (geometry GeoJSON, `alternatives=true`, `overview=full|simplified`); sample waypoints every `WAYPOINT_SPACING_KM` up to `MAX_WAYPOINTS` including first and last, computing `elapsed_nominal_s` by cumulative step durations (or proportional distance if steps absent — note in evidence); **one `table` call** per route: sources = waypoints, destinations = candidate mandis → fill `Waypoint.to_dest_s`. Cache by rounded (o,d). Fallback server on failure; if both fail: return failure (no straight-line fake route; B treats missing route as no candidate). Call budget per request ≈ K routes + K tables ≈ 16 calls at 1/s — document and cache.
**Tests:** waypoint count ≤ MAX; first/last included; elapsed monotone; table mapping to mandi ids; failure path. **DoD:** live route returns duration/distance for a real pair.

**A6 — Weather along route (≈ 3 h; deps A2, A5).** `get_weather_along(route, depart, now) -> list[WeatherSample]` and `get_destination_weather(loc, window)`; one multi-location Open-Meteo call per route (hourly `temperature_2m,precipitation`, `timezone=auto`, `forecast_days=3`); align each waypoint to the hour of `depart + elapsed_nominal_s`; evidence kind `forecast`, `as_of`=model reference/time of fetch (document what the API exposes). Optional river-flow proxy: Flood API `river_discharge` → `Evidence(kind=proxy)` per route midpoint (advisory only). Missing values → `None` (never 0).
**Tests:** hour alignment; None handling; multi-location parsing (list vs dict). **DoD:** live call returns samples for a real route.

**A7 — Candidate assembler & source health (≈ 2 h; deps A3–A6).** Implement `assemble_candidates`, `refresh_for_monitoring`, `source_health`. Collect warnings. Must degrade gracefully: failed weather → samples missing (flag), failed price → candidate dropped. Latency budget: initial assemble ≤ 40 s with warm cache (stream progress to caller via callback optional).
**Tests:** orchestration with all sources mocked (FIXTURE) incl. each failure mode. **DoD:** live end-to-end returns `CandidateAssembly` for a real origin. **This completes the A side of Gate G2.**

**A8 — Historical loader & replay data (P1, ≈ 4 h; deps A2).** Load CEDA / India Data Portal extracts (human downloads; script parses) for tomato × 1–2 states; Open-Meteo archive + historical-forecast for same dates; expose `replay_candidates(req, as_of_date)` returning `CandidateAssembly` with all evidence `kind=historical` and `mode=replay`. **Never mixes with live.**

**A9 — Deployment & hardening (≈ 2 h).** Run script, `.env` handling, startup self-check calling `source_health()`, log rotation, README.

---
### STREAM B — Decision Engine (pure Python, no I/O)

**B1 — Units, time, config helpers (≈ 1 h, no deps).** Seconds↔hours; IST handling (`zoneinfo("Asia/Kolkata")`); `parse_hhmm`; deterministic rounding helpers (₹ to 0 dp for display, internal full precision); `next_trading_day` is NOT modelled (window miss = `NEXT_DAY_WAIT_H` hold, per PLAN §7.2). **Tests:** tz-aware arithmetic across midnight.

**B2 — Ripening exposure (≈ 2 h, deps B1).** Store table in `data/reference/ripening_ucdavis.json` (PLAN §7.7). Implement `days_to_red` (rate-interpolation, `None` outside 12.5–25 °C), `ripening_exposure` (offsets by vehicle from config; refrigerated uses set-point, offset 0; chilling_risk rule; flags). **Golden tests (must match):** PINK 10 h @ 20 °C → 10.4 %; @ 22 °C → 13.2 %; @ 25 °C → 20.8 %; cargo 26 °C → `consumed_pct=None, out_of_range=True`; MG refrigerated set-point 10 °C → `chilling_risk`; interpolation differs from day-interpolation by ≤ 4 % at table midpoints; never extrapolates. **DoD:** tests green; docstring states "ripening, not spoilage".

**B3 — Net value (≈ 1.5 h, deps B1).** Exactly PLAN §7.1; `per_km_per_trip | fixed_per_trip | per_qtl_per_km`. **Golden:** Q=40, P=1500, pct 6 %, fixed 10/qtl, transport 4800 → 51 200.0; per_qtl_per_km case; unknown deductions ⇒ treated 0 **and** reported via a returned flag (use a small `NetBreakdown` dataclass internally). Rejects Q ≤ 0.

**B4 — Option evaluator & scenario grid (≈ 3 h, deps B2, B3, M0).** `evaluate_option` produces 9 `ScenarioResult`s (price × delay) with window status per PLAN §7.2, and a `RipeningResult` from route weather segments (segment hours from waypoint elapsed times × the nominal duration; ambient = weather sample temperature at segment start; if temperature missing → segment unusable ⇒ `out_of_range`-like flag `missing_weather` handled as coverage issue, never as 0 °C). `rain_exposure_mm` = Σ precipitation at route samples (document aggregation; units mm/h × hours covered). `score` per `risk_mode` using `k = ceil(0.25·9) = 3`.
**Golden tests:** raw scenario count 27 collapses to 9 money scenarios; k(n=9)=3, k(n=18)=5, k(n=4)=1; score uses lowest-k mean; window `unknown` when closing time absent; missed window adds `NEXT_DAY_WAIT_H × hold` and keeps price level; deterministic tie-break.

**B5 — Plan builder A/B/C (≈ 3 h, deps B4).** Exactly PLAN §7.4. Build options for every (candidate × route) with priced candidates; eligibility filter; A, Fail(A), B, C (departure slots, hold cost, staging ripening); `robustness`; `role_note` strings; `reconsider_if` conditions (numbers from flips/commit). Abstain path returns `PlanSet` with `plans=[]` and `coverage.grade=ABSTAIN`.
**Golden tests (use FIXTURE inputs, label them):** (1) `risk_mode` cvar vs mean can choose different A; (2) B is selected by mean Net over Fail(A) and can differ from second-best by score; (3) empty Fail(A) ⇒ B=standby with correct note; (4) ineligible (ripening over limit) options excluded from A unless all are; (5) no priced option ⇒ ABSTAIN. *(Lesson from the first logic test: do not reuse variable names across plan labels; test with ≥ 3 candidates.)*

**B6 — Commitment points (≈ 2 h, deps B4).** PLAN §7.5. Returns `CommitInfo` per scenario with `None` where never feasible; `computed=False` if B closing time unknown. **Tests:** monotone-delay property (`last_feasible` non-increasing as δ grows); unknown window ⇒ not computed; waypoint 0 vs last; **note text always contains "approximate"**.

**B7 — Flip boundaries (≈ 2 h, deps B4, B6).** PLAN §7.6. Delay applied to A only. **Tests:** closed-form price flip equals brute-force scan within tolerance; `None` when B never wins; `diversion_feasible_at_flip` consistent with B6; flip changes when A's transport cost changes.

**B8 — Coverage grader (≈ 1.5 h, deps M0).** PLAN §7.8. **Tests:** all-ok → ACTIONABLE; closing unknown → WITH_CAVEATS; 3 caveats → ADVISORY_ONLY; stale price > abstain days → ABSTAIN; district centroid counted; `items` list complete with kind tags.

**B9 — Triggers, hysteresis, recompute, diff (≈ 3.5 h, deps B5–B8).** PLAN §7.9. `WatchState` is persisted by Stream C. T1–T5 evaluated deterministically; T6 advisory; counts reset when trigger clears; cooldown; margin rule; new `PlanSet(version+1)` and `PlanDiff` with items for: typical price, ETA, window status, remaining rain, ripening worst-case, best-alternative value delta, labels swap; `primary_reason` = largest-impact changed term. **Tests:** needs `HYSTERESIS_N` consecutive ticks; fired-but-no-better ⇒ `propose_replan=False` with reason; cooldown blocks; T5 once per new record; diff recomputation deterministic (same inputs ⇒ same diff).

**B9b — Template explainer & number check (≈ 2 h, deps B5, B9).** `render_plan_summary`, `render_diff`, `check_numbers`. Text uses only approved wording (AGENT §7), shows kind tags and record dates. `check_numbers` extracts numeric tokens and verifies each is in `allowed_values` (tolerance) or is an index/date/label. **Tests:** injected unknown number flagged; template output contains no forbidden phrases.

**B10 — Backtest harness (P1, ≈ 4 h, deps A8, B5).** For historical dates: compare chosen option vs baselines (highest typical price; nearest mandi) using realised next-day record price at the chosen mandi (real historical) — report average regret **as measured**, whatever it is; label "historical replay — not live". If price-change quantiles are trialled: temporal split, pinball loss and coverage vs persistence; ship only if it wins.

---
### STREAM C — Agent, API, Telegram, UI

**C1 — FastAPI app, DB wiring, stubs (≈ 3 h; deps M0; A2 for DB helper).** `app/api/main.py` with lifespan (init DB, start watchdog + Telegram poller threads), routes per PLAN §9 using pydantic contracts, `deps.py` provider selection by `guards.mode()`, `stubs.py` (dev_stub only, FIXTURE banner; raises in live). CORS for Streamlit. Persist `plans`, `plan_versions`, `agent_trace`. **Tests:** TestClient: `/health`, `/plan` returns ABSTAIN when providers return nothing; stubs raise in live mode; validation errors → 422.
**DoD:** `uvicorn app.api.main:app` serves `/docs`; **with Stream A/B merged, `POST /plan` works live (G2).**

**C2 — Telegram integration (≈ 3 h; deps M0).** `app/integrations/telegram.py`: `send_text`, `send_plan_approval(plan_id, version, text)` with inline buttons, `send_checkin_keyboard`, `send_replan_proposal(diff_text)`; background long-poll thread (`getUpdates`, allowed_updates `message, edited_message, callback_query`), `answerCallbackQuery`, parsing `callback_data` (§2) → internal events (`approve/reject/checkin/replan_ok/replan_no`); location handling (`message.location`, `edited_message.location` → `DispatchProgress(source="telegram_location")`) **only enabled if A1 verified**; message ids stored in `actions`. Handle 409 (webhook set) by `deleteWebhook`. **Tests:** `callback_data` round-trip ≤ 64 bytes; event mapping; send failure → `actions.ok=0` and UI warning (no silent success). **DoD:** real message + button press verified by hand.

**C3 — LangGraph orchestrator (≈ 3.5 h; deps C1, A7, B5, B8, B9b).** Graph per PLAN §10 with SQLite checkpointer and `interrupt` for approval. Tool registry; LLM optional (`llm.py`: intake parse + tool choice + paraphrase; every LLM output validated: JSON schema for tool choice; `check_numbers` for text); critic node; step cap 12; every step written to `agent_trace`. **LLM-off path must yield identical numbers and a full template explanation.** Plain-state-machine fallback behind the same node functions.
**Tests:** LLM disabled end-to-end; hallucinated number ⇒ template fallback; stale price ⇒ critic loop ≤ 2 then degrade; abstain path; interrupt resumes on approval.

**C4 — Watchdog (≈ 2 h; deps C1, C3, B9).** Thread ticking every `POLL_INTERVAL_S` for plans in `dispatched|monitoring`: build `DispatchProgress` (latest check-in/location/manual ETA; else route-estimate), `refresh_for_monitoring`, `evaluate_triggers_and_recompute`, persist `WatchState` and `watch_events`; on `propose_replan` → store `PlanSet v+1` + diff, status `replan_pending`, send Telegram proposal. `POST /plans/{id}/watch/tick` calls the same function. **Never changes destination without an approval row.** **Tests:** hysteresis across two ticks; no action without approval; cooldown.

**C5 — Streamlit UI (≈ 5 h; deps C1; live data after G2).** Four screens (call FastAPI only):
1. **Plan:** form (all `DispatchRequest` fields), per-mandi closing-time inputs, assumptions panel (read-only table of PLAN §6 values, editable delay stress), source-health strip with kind tags and record dates.
2. **Compare:** map (pydeck) + A/B/C cards: net range, travel time, rain exposure, window status, ripening range with out-of-range flags, flip boundaries, commitment points (with "approximate"), "reconsider if…" list, coverage grade + robustness count, "Why not the highest-price mandi?" (its Fail scenarios), evidence drawer (click number → `/evidence/{id}`).
3. **Dispatch & Monitor:** approve button, Telegram receipt, progress timeline, manual tick button, what-if controls (clearly red-bordered "WHAT-IF — not observed"), trigger status, **diff view** and replan approval.
4. **Evidence & Replay:** ledger table, agent trace, (P1) replay/backtest results labelled historical.
Banner from `/health.mode`. Never display a number without its tag.
**DoD:** a non-developer can run the demo script with it.

**C6 — What-if & Flip Finder (P1, ≈ 3 h).** `/plans/{id}/whatif` recomputation (engine only, results flagged `what_if`); Flip Finder scanning origins × slots with live data under rate limits; surfaces combos where A≠B or windows flip; if none, says so.

**C7 — E2E tests & demo rehearsal (≈ 2 h).** Cases E2E-1…5 below, plus kill-each-API drills.

---

## 4. End-to-end test cases (live sources where possible; label anything else)

| ID | Name | Setup | Pass criteria |
|---|---|---|---|
| E2E-1 | Normal | Real origin + ≥ 3 live mandis, closing times user-set | `POST /plan` returns A/B/C; every number has an evidence id; coverage graded; record dates shown |
| E2E-2 | Plan reversal | Change departure slot (real forecast) or apply labelled what-if delay | Plans recompute; either labels swap or flip boundary moves; UI states which; what-if flagged |
| E2E-3 | Commit point | Dispatch with B reachable at waypoint k, then report delay at later waypoint | Diversion shown feasible before, infeasible after; text says "approximate / under scenario" |
| E2E-4 | Post-dispatch trigger | Telegram check-in "Delayed +60m" (or manual ETA slip, labelled) held for `HYSTERESIS_N` ticks | Recompute from **fresh** data; diff lists changed terms; approval requested; no action before approval |
| E2E-5 | Missing/stale evidence | Disable mandi API (bad key) / null weather | ABSTAIN or downgraded grade with explicit reasons; **no fabricated fallback**; last real snapshot shown with age |

Record outcomes in `PROGRESS.md → E2E results` (pass/fail, date, notes).

---

## 5. Parallel starting points (so all three can begin immediately)
- **Stream A:** A1 now → (after M0) A2 → A3/A4/A5 can proceed in parallel sessions only if split between two humans; otherwise sequential.
- **Stream B:** B1 → B2 → B3 immediately (no contracts needed); then B4+ after M0.
- **Stream C:** M0 immediately (everyone depends on it) → C1 → C2 (independent of A/B).
Two humans can also split Stream A (A3+A4 | A5+A6) after A2, or Stream C (C2+C3 | C1+C5) after M0 — record the split in PROGRESS.md.
