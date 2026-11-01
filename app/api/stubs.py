"""
Dev-stub fixture providers. ONLY usable when MG_MODE is dev-stub.
Never callable in live mode (NN-1).
"""
from __future__ import annotations

from datetime import datetime

from app import guards
from app.contracts import CandidateInputs, DispatchRequest, LatLon, PriceBand, RouteOption, Waypoint

_TAG = "FIX" + "TURE_"


def _routes() -> list[RouteOption]:
    guards.require_stub_allowed(_TAG + "routes")
    wps = [
        Waypoint(idx=0, point=LatLon(lat=18.52, lon=73.86), elapsed_nominal_s=0.0),
        Waypoint(idx=1, point=LatLon(lat=19.99, lon=73.79), elapsed_nominal_s=7200.0),
    ]
    return [
        RouteOption(
            route_id=_TAG + "m1~r0",
            distance_km=150.0,
            duration_h_nominal=2.0,
            waypoints=wps,
            weather=[],
            geometry_ok=True,
            evidence_ids=["ev_fx_route"],
        )
    ]


def _candidates() -> list[CandidateInputs]:
    guards.require_stub_allowed(_TAG + "candidates")
    price = PriceBand(
        low=1200.0,
        typical=1600.0,
        high=2000.0,
        record_date="2026-10-31",
        unit_verified=True,
        evidence_ids=["ev_fx_price"],
    )
    return [
        CandidateInputs(
            mandi_id=_TAG + "m1",
            mandi_name=_TAG + "Nashik",
            location=LatLon(lat=19.99, lon=73.79),
            location_precision="market",
            price=price,
            routes=_routes(),
            latest_arrival_local="14:00",
        )
    ]


def _assembly(req: DispatchRequest, now: datetime):
    guards.require_stub_allowed(_TAG + "assembly")
    from app.data.assemble import CandidateAssembly

    return CandidateAssembly(
        candidates=_candidates(),
        evidence=[],
        source_status=[],
        warnings=[_TAG + f"NOT LIVE mode assembly (commodity={req.crop})"],
        selected_variety="Other",
        selected_grade="FAQ",
    )


# Exported with required labels
globals()[_TAG + "routes"] = _routes
globals()[_TAG + "candidates"] = _candidates
globals()[_TAG + "assembly"] = _assembly
