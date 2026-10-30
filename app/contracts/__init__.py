"""
Single import point:   from app.contracts import DispatchRequest, PlanSet, Evidence, ...
Contracts version: v1.0
"""
from app.contracts.evidence import Evidence, Kind, LatLon
from app.contracts.events import DispatchProgress, SourceStatus
from app.contracts.inputs import (
    CandidateInputs,
    Costs,
    DispatchRequest,
    PriceBand,
    RipenessStage,
    RouteOption,
    VehicleType,
    Waypoint,
    WeatherSample,
)
from app.contracts.plans import (
    CommitInfo,
    Condition,
    CoverageItem,
    CoverageReport,
    DiffItem,
    FlipBoundaries,
    Plan,
    PlanDiff,
    PlanOption,
    PlanSet,
    RipeningOffsetResult,
    RipeningResult,
    ScenarioResult,
)

CONTRACTS_VERSION = "1.0"

__all__ = [
    "CONTRACTS_VERSION", "Evidence", "Kind", "LatLon", "DispatchProgress", "SourceStatus",
    "CandidateInputs", "Costs", "DispatchRequest", "PriceBand", "RipenessStage", "RouteOption",
    "VehicleType", "Waypoint", "WeatherSample", "CommitInfo", "Condition", "CoverageItem",
    "CoverageReport", "DiffItem", "FlipBoundaries", "Plan", "PlanDiff", "PlanOption", "PlanSet",
    "RipeningOffsetResult", "RipeningResult", "ScenarioResult",
]
