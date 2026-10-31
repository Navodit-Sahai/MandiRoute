"""Tests that the shared contracts reject bad input and survive a JSON round-trip."""
import pytest
from pydantic import ValidationError

from app.contracts import (
    CONTRACTS_VERSION, CommitInfo, Costs, CoverageReport, DispatchRequest, Evidence, FlipBoundaries,
    Kind, LatLon, PlanSet,
)
from tests.helpers import make_costs, make_request


def test_version_is_declared():
    assert CONTRACTS_VERSION == "1.0"


def test_request_round_trips_through_json():
    req = make_request()
    again = DispatchRequest.model_validate_json(req.model_dump_json())
    assert again == req


def test_refrigerated_needs_a_setpoint():
    with pytest.raises(ValidationError):
        make_request(vehicle="refrigerated")
    ok = make_request(vehicle="refrigerated", refrigerated_setpoint_c=13.0)
    assert ok.refrigerated_setpoint_c == 13.0


def test_naive_datetime_is_rejected():
    from datetime import datetime
    with pytest.raises(ValidationError):
        make_request(preferred_departure=datetime(2030, 1, 1, 5, 0))   # no timezone


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        make_request(quantity_qtl=0)


def test_latlon_ranges():
    with pytest.raises(ValidationError):
        LatLon(lat=91, lon=0)
    with pytest.raises(ValidationError):
        LatLon(lat=0, lon=181)


def test_percentages_limited_to_0_100():
    with pytest.raises(ValidationError):
        make_costs(commission_pct=101)
    with pytest.raises(ValidationError):
        make_costs(commission_pct=-1)


def test_unknown_ripeness_stage_rejected():
    with pytest.raises(ValidationError):
        make_request(ripeness="LIGHT_RED")      # not in the UC Davis table -> unsupported on purpose


def test_deductions_known_flag():
    assert make_costs().deductions_known is True
    assert make_costs(commission_pct=None).deductions_known is False


def test_evidence_kind_values_are_the_agreed_eight():
    assert {k.value for k in Kind} == {
        "observed", "forecast", "historical", "proxy", "assumption", "computed", "user_input", "what_if"}


def test_planset_schema_builds_and_defaults_work():
    assert PlanSet.model_json_schema()
    assert FlipBoundaries().delay_flip_pct is None
    assert CommitInfo(computed=False).last_feasible_waypoint == {}
    ev = Evidence(id="ev_00000000", kind=Kind.OBSERVED, label="x", source="test", fetched_at="2030-01-01T00:00:00Z")
    assert ev.stale is False
    CoverageReport(grade="ABSTAIN", items=[], reasons=[])
