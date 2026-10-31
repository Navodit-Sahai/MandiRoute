"""Shared contracts v1.0 (BLUEPRINT.md section 1) -- copied exactly.

FROZEN after Gate G0. Change only via a Contract Change Request (AGENT.md 5.2).
(Stream A copy made only so the data layer can be built and tested before M0 merges.)
"""
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
