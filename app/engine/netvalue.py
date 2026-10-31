"""
B3 - Net value of selling a load.

    gross      = quantity_qtl x price_per_qtl
    percent    = (commission% + market fee% + other%) / 100
    fixed_ded  = quantity_qtl x (hamali_per_qtl + loading_per_qtl)
    transport  = depends on how the truck owner quotes the price (see Costs.transport_mode)
    holding    = hold_cost_per_hour x wait_hours
    NET        = gross x (1 - percent) - fixed_ded - transport - holding

UNITS (never mix!):  quantity in QUINTALS (1 qtl = 100 kg) | price in RUPEES PER QUINTAL |
                     distance in KM (one way) | wait in HOURS.

Unknown costs are treated as 0 in the arithmetic BUT reported in `unknown_fields`, so the
evidence grader can lower the recommendation grade. Unknown is never silently "free".
"""
from __future__ import annotations

from dataclasses import dataclass

from app.contracts import DispatchRequest


@dataclass(frozen=True)
class NetBreakdown:
    """Every part of the calculation, so the UI can show "where did this number come from"."""

    gross: float
    percent_deduction: float      # rupees taken as percentages (commission, fee, other)
    fixed_deduction: float        # rupees taken as per-quintal charges (hamali, loading)
    transport: float
    holding: float
    net: float
    unknown_fields: tuple[str, ...]   # inputs that were not given and were treated as 0

    @property
    def deductions_known(self) -> bool:
        return not any(name in self.unknown_fields for name in ("commission_pct", "market_fee_pct", "hamali_per_qtl"))


def net_breakdown(req: DispatchRequest, price_per_qtl: float, distance_km: float, wait_hours: float) -> NetBreakdown:
    """Full calculation with every component visible."""
    if price_per_qtl <= 0:
        raise ValueError("price_per_qtl must be positive (rupees per quintal)")
    if distance_km < 0:
        raise ValueError("distance_km cannot be negative")
    if wait_hours < 0:
        raise ValueError("wait_hours cannot be negative")

    c = req.costs
    q = req.quantity_qtl

    # Which inputs did the user NOT give? (reported, and treated as 0 below)
    unknown: list[str] = []
    if c.commission_pct is None:
        unknown.append("commission_pct")
    if c.market_fee_pct is None:
        unknown.append("market_fee_pct")
    if c.hamali_per_qtl is None:
        unknown.append("hamali_per_qtl")
    if wait_hours > 0 and c.hold_cost_per_hour is None:
        unknown.append("hold_cost_per_hour")      # only matters when we actually wait

    percent_total = (c.commission_pct or 0.0) + (c.market_fee_pct or 0.0) + (c.other_pct or 0.0)
    if percent_total > 100:
        raise ValueError("percentage deductions add up to more than 100")

    gross = q * price_per_qtl
    percent_deduction = gross * percent_total / 100.0
    fixed_deduction = q * ((c.hamali_per_qtl or 0.0) + (c.loading_per_qtl or 0.0))

    if c.transport_mode == "per_km_per_trip":
        transport = c.transport_rate * distance_km
    elif c.transport_mode == "fixed_per_trip":
        transport = c.transport_rate
    elif c.transport_mode == "per_qtl_per_km":
        transport = c.transport_rate * q * distance_km
    else:  # cannot happen: the contract only allows the three modes above
        raise ValueError(f"unknown transport_mode '{c.transport_mode}'")

    holding = (c.hold_cost_per_hour or 0.0) * wait_hours
    net = gross - percent_deduction - fixed_deduction - transport - holding

    return NetBreakdown(
        gross=gross,
        percent_deduction=percent_deduction,
        fixed_deduction=fixed_deduction,
        transport=transport,
        holding=holding,
        net=net,
        unknown_fields=tuple(unknown),
    )


def net_value(req: DispatchRequest, price_per_qtl: float, distance_km: float, wait_hours: float) -> float:
    """Just the final rupee number (the public function other modules call)."""
    return net_breakdown(req, price_per_qtl, distance_km, wait_hours).net
