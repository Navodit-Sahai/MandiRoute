"""
Things that happen while the system runs: driver progress reports and API health.

CONTRACT: do not rename/remove fields without a Contract Change Request.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from app.contracts.evidence import Kind, LatLon


class DispatchProgress(BaseModel):
    """What we know about the truck AFTER dispatch."""

    plan_id: str
    dispatched_at: str
    last_waypoint_idx: Optional[int] = None
    last_position: Optional[LatLon] = None
    reported_delay_min: float = 0.0
    source: Literal["telegram_location", "checkin_button", "manual_eta", "route_estimate"] = "route_estimate"


class SourceStatus(BaseModel):
    """Health of one external data source (shown in the UI's source-health strip)."""

    name: str
    ok: bool
    http: Optional[int] = None
    latency_ms: Optional[int] = None
    as_of: Optional[str] = None
    fetched_at: Optional[str] = None
    kind: Kind
    note: Optional[str] = None
