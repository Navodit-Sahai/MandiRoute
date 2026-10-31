"""
B1 - Small helpers used everywhere in the engine: time, units, and the "worst 25%" maths.

Rules of this file:
  * Pure functions: no internet, no database, never reads the clock (the caller passes the time in).
  * Internal time unit = SECONDS. Hours only for display.
  * All datetimes must be timezone-aware (we refuse naive ones: they cause silent bugs).
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

SECONDS_PER_HOUR = 3600.0


# ---------------------------------------------------------------- seconds <-> hours
def seconds_to_hours(seconds: float) -> float:
    return seconds / SECONDS_PER_HOUR


def hours_to_seconds(hours: float) -> float:
    return hours * SECONDS_PER_HOUR


# ------------------------------------------------------------------- clock times
def parse_hhmm(text: str) -> tuple[int, int]:
    """
    Turn a closing-time string like "14:30" into (14, 30).
    Strict on purpose: exactly HH:MM, hour 00-23, minute 00-59. Anything else -> ValueError.
    """
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise ValueError(f"time must look like HH:MM (24-hour), got '{text}'")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range, got '{text}'")
    return hour, minute


def to_local(moment: datetime, tz_name: str) -> datetime:
    """Convert a timezone-aware datetime to the project's local timezone."""
    if moment.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return moment.astimezone(ZoneInfo(tz_name))


def delayed_arrival(depart: datetime, duration_s: float, delay_multiplier: float) -> datetime:
    """
    When will the truck arrive?
        arrival = departure + normal_duration x (1 + delay_multiplier)
    delay_multiplier 0.25 means the trip takes 25% longer than normal.
    """
    if depart.tzinfo is None:
        raise ValueError("departure must be timezone-aware")
    if duration_s < 0:
        raise ValueError("duration cannot be negative")
    if delay_multiplier < 0:
        raise ValueError("delay multiplier cannot be negative")
    return depart + timedelta(seconds=duration_s * (1.0 + delay_multiplier))


def window_status(arrival: datetime, latest_hhmm: str | None, tz_name: str) -> Literal["met", "missed", "unknown"]:
    """
    Did we reach the mandi before its closing time?

      * latest_hhmm is None  -> "unknown"  (we do NOT guess a closing time)
      * arrival's clock time (local) <= closing time -> "met"
      * otherwise -> "missed"

    Known simplification: only the clock time of the arrival day is compared. Opening time,
    holidays and queues are not modelled.
    """
    if latest_hhmm is None:
        return "unknown"
    hour, minute = parse_hhmm(latest_hhmm)
    local_arrival = to_local(arrival, tz_name)
    return "met" if local_arrival.time() <= time(hour, minute) else "missed"


# ------------------------------------------------- "worst 25% of scenarios" maths
def lowest_k_count(n: int, q: float) -> int:
    """
    How many scenarios count as "the worst q share"?   k = ceil(q x n), but at least 1.
    Examples of the rule: n=9, q=0.25 -> 3.   n=4, q=0.25 -> 1.
    Rounding to 9 decimals first avoids float noise (e.g. 0.1 x 30 = 3.0000000000000004).
    """
    if n <= 0:
        raise ValueError("need at least one scenario")
    if not (0 < q <= 1):
        raise ValueError("q must be in (0, 1]")
    return max(1, math.ceil(round(q * n, 9)))


def mean_of_lowest(values: Sequence[float], k: int) -> float:
    """Average of the k smallest values."""
    if not values:
        raise ValueError("values is empty")
    if not (1 <= k <= len(values)):
        raise ValueError("k must be between 1 and len(values)")
    smallest = sorted(values)[:k]
    return sum(smallest) / k
