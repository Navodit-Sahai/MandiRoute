"""
Everything that goes INTO the engine:
  1. DispatchRequest  -> what the dispatcher types in the form
  2. CandidateInputs  -> what Person 2's data code collects for every possible mandi

CONTRACT: do not rename/remove fields without a Contract Change Request.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.contracts.evidence import LatLon


# ----------------------------------------------------------------------------
# 1. The dispatcher's request
# ----------------------------------------------------------------------------
class VehicleType(str, Enum):
    OPEN = "open"
    COVERED = "covered"
    REFRIGERATED = "refrigerated"


class RipenessStage(str, Enum):
    """Only the stages present in the UC Davis ripening table. 'Light red' is NOT supported."""

    MATURE_GREEN = "MG"
    BREAKER = "BREAKER"
    TURNING = "TURNING"
    PINK = "PINK"


class Costs(BaseModel):
    """Money inputs typed by the dispatcher. None means 'not known' (never silently 0)."""

    # How transport cost is quoted by the truck owner:
    #   per_km_per_trip : rupees per km for the whole truck
    #   fixed_per_trip  : one fixed rupee amount for the whole trip
    #   per_qtl_per_km  : rupees per quintal per km
    transport_mode: Literal["per_km_per_trip", "fixed_per_trip", "per_qtl_per_km"]
    transport_rate: float = Field(ge=0)

    commission_pct: Optional[float] = Field(default=None, ge=0, le=100)   # % of sale value
    market_fee_pct: Optional[float] = Field(default=None, ge=0, le=100)   # % of sale value
    other_pct: Optional[float] = Field(default=None, ge=0, le=100)        # % of sale value
    hamali_per_qtl: Optional[float] = Field(default=None, ge=0)           # rupees per quintal
    loading_per_qtl: Optional[float] = Field(default=None, ge=0)          # rupees per quintal
    hold_cost_per_hour: Optional[float] = Field(default=None, ge=0)       # rupees per hour of waiting

    @property
    def deductions_known(self) -> bool:
        """True only if the three main deductions were all given by the user."""
        return None not in (self.commission_pct, self.market_fee_pct, self.hamali_per_qtl)


class DispatchRequest(BaseModel):
    crop: str = "Tomato"
    quantity_qtl: float = Field(gt=0)            # QUINTALS ONLY (1 quintal = 100 kg)
    origin: LatLon
    origin_label: str
    origin_state: str
    ripeness: Optional[RipenessStage] = None
    vehicle: VehicleType
    refrigerated_setpoint_c: Optional[float] = None
    loading_time: datetime                       # must be timezone-aware
    preferred_departure: datetime                # must be timezone-aware
    max_distance_km: float = 400
    costs: Costs
    mandi_latest_arrival: dict[str, str] = {}    # mandi_id -> "HH:MM" local time. Missing = UNKNOWN
    risk_mode: Literal["cvar25", "mean"] = "cvar25"
    ripening_limit_pct: float = 25.0             # the user's own threshold; NOT evidence-based

    @model_validator(mode="after")
    def _check_request(self):
        if self.vehicle == VehicleType.REFRIGERATED and self.refrigerated_setpoint_c is None:
            raise ValueError("refrigerated vehicle requires refrigerated_setpoint_c")
        for moment in (self.loading_time, self.preferred_departure):
            if moment.tzinfo is None:
                raise ValueError("datetimes must be timezone-aware")
        return self


# ----------------------------------------------------------------------------
# 2. What the data side (Person 2) hands to the engine for each candidate mandi
# ----------------------------------------------------------------------------
class PriceBand(BaseModel):
    """Prices from ONE observed mandi record. This is NOT a forecast."""

    low: float        # rupees per QUINTAL: min_price of the record
    typical: float    # rupees per QUINTAL: modal_price of the record
    high: float       # rupees per QUINTAL: max_price of the record
    record_date: str  # the record's own date (ISO)
    variety: Optional[str] = None
    grade: Optional[str] = None
    unit_verified: bool = False   # True only after Person 2 confirms the unit from the source
    evidence_ids: list[str] = []


class Waypoint(BaseModel):
    """A sampled point along a route."""

    idx: int
    point: LatLon
    elapsed_nominal_s: float                       # seconds since departure, along THIS route
    to_dest_s: dict[str, float | None] = {}        # mandi_id -> seconds from here (None = unknown)


class WeatherSample(BaseModel):
    waypoint_idx: int
    time_iso: str                                  # the hour matched to our estimated arrival there
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
    location_precision: Literal["market", "district_centroid", "unknown"]
    price: Optional[PriceBand] = None
    routes: list[RouteOption] = []
    latest_arrival_local: Optional[str] = None     # "HH:MM"; None = UNKNOWN
    destination_weather: list[WeatherSample] = []
