"""
Test helpers.

EVERYTHING IN THIS FILE IS A FIXTURE - invented numbers used only to test code.
It is NOT observed data and must never be imported from the `app/` folder.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.contracts import Costs, DispatchRequest, LatLon


def make_costs(**overrides) -> Costs:
    """FIXTURE costs: a truck paid per km, with all three main deductions given."""
    values = dict(
        transport_mode="per_km_per_trip",
        transport_rate=20.0,
        commission_pct=4.0,
        market_fee_pct=2.0,
        hamali_per_qtl=6.0,
        loading_per_qtl=4.0,
        hold_cost_per_hour=100.0,
    )
    values.update(overrides)
    return Costs(**values)


def make_request(**overrides) -> DispatchRequest:
    """FIXTURE dispatch request. Override any field by keyword."""
    values = dict(
        quantity_qtl=40.0,
        origin=LatLon(lat=20.0, lon=77.0),
        origin_label="fixture origin",
        origin_state="fixture state",
        ripeness="PINK",
        vehicle="covered",
        loading_time=datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc),
        preferred_departure=datetime(2030, 1, 1, 5, 0, tzinfo=timezone.utc),
        costs=make_costs(),
    )
    values.update(overrides)
    return DispatchRequest(**values)
