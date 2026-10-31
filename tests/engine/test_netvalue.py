"""B3 tests. All prices/quantities/costs are FIXTURES (invented for testing)."""
import pytest

from app.engine.netvalue import net_breakdown, net_value
from tests.helpers import make_costs, make_request


def test_golden_fixed_trip_cost():
    """40 qtl x 1500 = 60000; 6% -> 3600; (6+4)/qtl x 40 = 400; transport 4800  =>  51200"""
    req = make_request(quantity_qtl=40, costs=make_costs(transport_mode="fixed_per_trip", transport_rate=4800, hold_cost_per_hour=None))
    assert net_value(req, 1500, distance_km=999, wait_hours=0) == pytest.approx(51200.0)


def test_per_km_transport_matches_fixed_when_totals_match():
    req = make_request(costs=make_costs(transport_mode="per_km_per_trip", transport_rate=20))
    assert net_value(req, 1500, distance_km=240, wait_hours=0) == pytest.approx(51200.0)    # 20 x 240 = 4800


def test_per_quintal_per_km_transport():
    req = make_request(quantity_qtl=40, costs=make_costs(transport_mode="per_qtl_per_km", transport_rate=0.5))
    b = net_breakdown(req, 1500, distance_km=240, wait_hours=0)
    assert b.transport == pytest.approx(0.5 * 40 * 240)


def test_holding_cost_applies_only_when_waiting():
    req = make_request(costs=make_costs(hold_cost_per_hour=100))
    no_wait = net_value(req, 1500, 240, 0)
    wait = net_value(req, 1500, 240, 12)
    assert no_wait - wait == pytest.approx(1200.0)


def test_breakdown_adds_up():
    req = make_request()
    b = net_breakdown(req, 1500, 240, 3)
    assert b.net == pytest.approx(b.gross - b.percent_deduction - b.fixed_deduction - b.transport - b.holding)
    assert b.gross == pytest.approx(40 * 1500)
    assert b.percent_deduction == pytest.approx(60000 * 0.06)
    assert b.fixed_deduction == pytest.approx(400)


def test_unknown_deductions_are_reported_not_hidden():
    costs = make_costs(commission_pct=None, market_fee_pct=None, hamali_per_qtl=None, loading_per_qtl=None)
    b = net_breakdown(make_request(costs=costs), 1500, 240, 0)
    assert set(b.unknown_fields) == {"commission_pct", "market_fee_pct", "hamali_per_qtl"}
    assert b.deductions_known is False
    assert b.percent_deduction == 0 and b.fixed_deduction == 0      # treated as 0 in arithmetic, but flagged


def test_all_known_means_no_unknown_fields():
    b = net_breakdown(make_request(), 1500, 240, 0)
    assert b.unknown_fields == () and b.deductions_known is True


def test_unknown_hold_cost_flagged_only_if_we_wait():
    costs = make_costs(hold_cost_per_hour=None)
    assert "hold_cost_per_hour" not in net_breakdown(make_request(costs=costs), 1500, 240, 0).unknown_fields
    assert "hold_cost_per_hour" in net_breakdown(make_request(costs=costs), 1500, 240, 5).unknown_fields


def test_higher_price_gives_higher_net_and_longer_route_gives_lower_net():
    req = make_request()
    assert net_value(req, 1600, 240, 0) > net_value(req, 1500, 240, 0)
    assert net_value(req, 1500, 300, 0) < net_value(req, 1500, 240, 0)


def test_net_is_linear_in_quantity_when_transport_is_per_qtl():
    costs = make_costs(transport_mode="per_qtl_per_km", transport_rate=0.5)
    n1 = net_value(make_request(quantity_qtl=10, costs=costs), 1500, 100, 0)
    n2 = net_value(make_request(quantity_qtl=20, costs=costs), 1500, 100, 0)
    assert n2 == pytest.approx(2 * n1)


@pytest.mark.parametrize("price,dist,wait", [(0, 10, 0), (-5, 10, 0), (100, -1, 0), (100, 10, -1)])
def test_bad_inputs_rejected(price, dist, wait):
    with pytest.raises(ValueError):
        net_value(make_request(), price, dist, wait)


def test_percentages_over_100_rejected():
    costs = make_costs(commission_pct=60, market_fee_pct=30, other_pct=20)
    with pytest.raises(ValueError):
        net_value(make_request(costs=costs), 1500, 100, 0)


def test_net_can_be_negative_and_is_not_clamped():
    req = make_request(costs=make_costs(transport_mode="fixed_per_trip", transport_rate=1_000_000))
    assert net_value(req, 1500, 10, 0) < 0
