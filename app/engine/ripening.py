"""
B2 - Tomato RIPENING EXPOSURE.

What this measures : how much of the tomato's remaining ripening window is used up during the trip.
What it does NOT   : spoilage, shelf life in days, grade loss, or any rupee loss. Never show it as such.

Data: data/reference/ripening_ucdavis.json  (days to full red colour by stage and temperature,
      only for 12.5 - 25 degrees C).

How the number is built:
    consumed = sum over trip segments of  (hours_in_segment / 24) / days_to_full_red(stage, cargo_temp)
    cargo_temp = outside_temp + assumed_offset        (offsets come from config.py, they are ASSUMPTIONS)
    For refrigerated trucks cargo_temp = the set-point.
If a cargo temperature is outside the table we do NOT extrapolate: that result is None + out_of_range.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from app.config import REFERENCE_DIR, Settings
from app.contracts import RipeningOffsetResult, RipeningResult, RipenessStage, VehicleType

TABLE_FILE = REFERENCE_DIR / "ripening_ucdavis.json"


class MissingTemperatureError(ValueError):
    """
    A trip segment has no temperature (weather data missing).
    We never pretend it was 0 degrees. The caller (module B4) must treat this as a data gap
    that lowers the evidence-coverage grade.
    """


# ------------------------------------------------------------------ reference table
@lru_cache(maxsize=4)
def load_table(path: Path = TABLE_FILE) -> dict:
    """Read the ripening table from disk once and check that it is well-formed."""
    with open(path, encoding="utf-8") as f:
        table = json.load(f)

    temps = table["temps_c"]
    if any(b <= a for a, b in zip(temps, temps[1:])):
        raise ValueError("ripening table: temps_c must be strictly increasing")
    for stage, days in table["days_to_full_red"].items():
        if len(days) != len(temps):
            raise ValueError(f"ripening table: stage {stage} has {len(days)} values for {len(temps)} temperatures")
        if any(d <= 0 for d in days):
            raise ValueError(f"ripening table: stage {stage} has a non-positive day count")
    return table


def _stage_key(stage: RipenessStage | str) -> str:
    return stage.value if isinstance(stage, RipenessStage) else str(stage)


def days_to_red(stage: RipenessStage | str, temp_c: float, table: Optional[dict] = None) -> Optional[float]:
    """
    Days until full red colour at `temp_c`.
    Between two table temperatures we interpolate the RATE (1/days) in a straight line
    (ripening is a speed, so averaging speeds is more natural than averaging days).
    Outside 12.5-25 C -> None (outside the evidence range).
    """
    table = table or load_table()
    temps = table["temps_c"]
    key = _stage_key(stage)
    if key not in table["days_to_full_red"]:
        raise ValueError(f"unknown ripeness stage '{key}'")
    days = table["days_to_full_red"][key]

    if temp_c < temps[0] or temp_c > temps[-1]:
        return None

    i = bisect_right(temps, temp_c) - 1        # index of the table temperature just below (or equal to) temp_c
    if i >= len(temps) - 1:                    # exactly the last table temperature
        return float(days[-1])

    rate_low, rate_high = 1.0 / days[i], 1.0 / days[i + 1]
    share = (temp_c - temps[i]) / (temps[i + 1] - temps[i])      # 0 at lower temp, 1 at higher temp
    return 1.0 / (rate_low + (rate_high - rate_low) * share)


# ------------------------------------------------------------------ the trip calculation
def _exposure_for_one_offset(
    stage: RipenessStage, cargo_temps: Sequence[tuple[float, float]], offset_c: float, table: dict
) -> RipeningOffsetResult:
    """cargo_temps = list of (hours, cargo_temperature) already including the offset."""
    consumed_fraction = 0.0
    out_of_range = False
    weighted_temp_sum = 0.0
    total_hours = 0.0

    for hours, cargo_temp in cargo_temps:
        total_hours += hours
        weighted_temp_sum += hours * cargo_temp
        if hours == 0:
            continue                                   # a zero-length segment uses no ripening time
        d = days_to_red(stage, cargo_temp, table)
        if d is None:
            out_of_range = True                        # keep looping so we still compute the mean temperature
        else:
            consumed_fraction += (hours / 24.0) / d

    mean_temp = (weighted_temp_sum / total_hours) if total_hours > 0 else (
        sum(t for _, t in cargo_temps) / len(cargo_temps)
    )
    return RipeningOffsetResult(
        offset_c=offset_c,
        cargo_temp_c=mean_temp,
        consumed_pct=None if out_of_range else consumed_fraction * 100.0,
        out_of_range=out_of_range,
    )


def ripening_exposure(
    stage: RipenessStage,
    segments: Sequence[tuple[float, Optional[float]]],
    vehicle: VehicleType,
    setpoint_c: Optional[float],
    limit_pct: float,
    cfg: Settings,
) -> RipeningResult:
    """
    stage      : ripeness stage of the load
    segments   : list of (hours, outside_temperature_c) covering the whole trip, in order
    vehicle    : open / covered / refrigerated
    setpoint_c : cooling set-point (required for refrigerated trucks)
    limit_pct  : the user's own "too much ripening" threshold (NOT evidence-based)

    Flag priority (strongest warning first):
        chilling_risk > out_of_range > over_limit > ok
    """
    if not segments:
        raise ValueError("need at least one trip segment")
    for hours, ambient in segments:
        if hours < 0:
            raise ValueError("segment hours cannot be negative")
        if ambient is None:
            raise MissingTemperatureError("a trip segment has no temperature; refusing to assume one")
    if vehicle == VehicleType.REFRIGERATED and setpoint_c is None:
        raise ValueError("refrigerated vehicle requires setpoint_c")

    table = load_table()
    results: list[RipeningOffsetResult] = []

    for offset in cfg.offsets_c[vehicle.value]:
        if vehicle == VehicleType.REFRIGERATED:
            base_temps = [(h, float(setpoint_c)) for h, _ in segments]      # outside weather does not matter
        else:
            base_temps = [(h, float(a)) for h, a in segments]               # type: ignore[arg-type]
        cargo_temps = [(h, t + offset) for h, t in base_temps]
        results.append(_exposure_for_one_offset(stage, cargo_temps, offset, table))

    known = [r.consumed_pct for r in results if r.consumed_pct is not None]
    worst = max(known) if known else None

    chilling = (
        vehicle == VehicleType.REFRIGERATED
        and stage == RipenessStage.MATURE_GREEN
        and float(setpoint_c) < cfg.chilling_threshold_c
    )
    if chilling:
        flag = "chilling_risk"
    elif any(r.out_of_range for r in results):
        flag = "out_of_range"
    elif worst is not None and worst > limit_pct:
        flag = "over_limit"
    else:
        flag = "ok"

    return RipeningResult(by_offset=results, worst_consumed_pct=worst, flag=flag)
