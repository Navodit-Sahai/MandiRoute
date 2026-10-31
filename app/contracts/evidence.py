"""
Basic building blocks shared by every part of the project.

  Kind     -> the "label" stuck on every number (was it observed? forecast? an assumption?)
  Evidence -> the "receipt" for one number: where it came from and when
  LatLon   -> a latitude/longitude pair

These classes are a CONTRACT (agreement) between Person 1 and Person 2.
Do not rename or remove fields. Changes go through a Contract Change Request in PROGRESS.md.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Kind(str, Enum):
    """What type of information a number is. The UI must always show this tag."""

    OBSERVED = "observed"      # really measured/recorded (e.g. today's mandi price record)
    FORECAST = "forecast"      # a weather model's prediction
    HISTORICAL = "historical"  # old real data (used for replay / backtest)
    PROXY = "proxy"            # indirect signal (e.g. river-flow alert; NOT road flooding)
    ASSUMPTION = "assumption"  # our own chosen value (e.g. delay stress); must be visible in UI
    COMPUTED = "computed"      # calculated by our code from other numbers
    USER_INPUT = "user_input"  # typed by the dispatcher
    WHAT_IF = "what_if"        # user-set "what if" scenario, NOT observed


class Evidence(BaseModel):
    """The receipt for one number: value + where it came from + how fresh it is."""

    id: str                              # unique id, format "ev_<8 hex chars>"
    kind: Kind
    label: str                           # human-readable name of the number
    value: float | str | None = None
    unit: Optional[str] = None
    source: str                          # e.g. "data.gov.in/agmarknet", "open-meteo/forecast", "user", "engine"
    as_of: Optional[str] = None          # the date/time the DATA refers to (e.g. mandi record date)
    fetched_at: str                      # when WE fetched it (ISO time, UTC)
    stale: bool = False                  # True if older than our freshness limit
    note: Optional[str] = None


class LatLon(BaseModel):
    """A point on the map."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
