"""
Everything that comes OUT of the engine (Person 1) and goes to the API/UI/agent.

CONTRACT: do not rename/remove fields without a Contract Change Request.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from app.contracts.evidence import Kind


class ScenarioResult(BaseModel):
    """Result of ONE of the 9 money scenarios (3 price levels x 3 delay levels)."""

    price_level: Literal["low", "typical", "high"]
    delay_level: Literal["none", "moderate", "severe"]
    net_value: float                                      # rupees
    arrival_iso: str
    window_status: Literal["met", "missed", "unknown"]    # did we reach before the mandi's closing time?


class RipeningOffsetResult(BaseModel):
    """Ripening exposure for ONE assumed cargo-temperature offset."""

    offset_c: float                  # assumed (cargo temperature - outside temperature)
    cargo_temp_c: float              # average cargo temperature used
    consumed_pct: Optional[float]    # % of the remaining ripening window used. None = outside table range
    out_of_range: bool


class RipeningResult(BaseModel):
    by_offset: list[RipeningOffsetResult]
    worst_consumed_pct: Optional[float]
    flag: Literal["ok", "over_limit", "out_of_range", "chilling_risk"]


class Condition(BaseModel):
    """A 'reconsider this plan if ...' rule shown to the user."""

    kind: Literal["eta_slip_h", "price_drop_pct", "rain_mm_remaining", "ripening_pct", "window_miss_scenarios"]
    comparison: Literal[">=", "<="]
    threshold: float
    rationale: str


class FlipBoundaries(BaseModel):
    """The point where the alternative plan starts to beat the monitored plan."""

    delay_flip_pct: Optional[float] = None       # delay (% of normal travel time) at which B beats A
    price_flip_per_qtl: Optional[float] = None   # A's typical price below which B wins
    diversion_feasible_at_flip: Optional[bool] = None
    note: Optional[str] = None


class CommitInfo(BaseModel):
    """Last waypoint from which switching to the alternative is still possible (approximate)."""

    computed: bool
    last_feasible_waypoint: dict[str, Optional[int]] = {}   # scenario name -> waypoint index (None = never)
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
    ripening: Optional[RipeningResult] = None
    evidence_ids: list[str]


class Plan(BaseModel):
    label: Literal["A", "B", "C"]
    option: PlanOption
    role_note: str
    reconsider_if: list[Condition] = []
    flip: FlipBoundaries = FlipBoundaries()
    commit: CommitInfo = CommitInfo(computed=False)


class CoverageItem(BaseModel):
    dimension: str
    kind: Kind
    status: Literal["ok", "degraded", "missing"]
    detail: str


class CoverageReport(BaseModel):
    grade: Literal["ACTIONABLE", "ACTIONABLE_WITH_CAVEATS", "ADVISORY_ONLY", "ABSTAIN"]
    items: list[CoverageItem]
    reasons: list[str]


class PlanSet(BaseModel):
    plan_id: str
    version: int
    created_at: str
    plans: list[Plan]
    robustness_a_best_in: int     # in how many scenarios Plan A had the best net value ...
    robustness_of: int            # ... out of this many tested scenarios
    coverage: CoverageReport
    evidence_ids: list[str] = []


class DiffItem(BaseModel):
    term: str
    old: float | str | None
    new: float | str | None
    delta: Optional[float] = None
    effect: str


class PlanDiff(BaseModel):
    old_version: int
    new_version: int
    primary_reason: str
    items: list[DiffItem]
    recommendation_changed: bool
