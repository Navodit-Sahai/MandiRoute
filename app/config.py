"""
ALL tunable numbers of the project live HERE (and nowhere else).

Important: these are NOT "examples". They are the project's ASSUMPTIONS REGISTRY
(PLAN.md section 6). Every value is tagged:
    ASSUMPTION : our own choice with no verified India-specific source -> must be visible in the UI
    DESIGN     : an engineering choice (not a claim about the real world)
    POLICY     : forced by an API provider's usage policy
    VERIFIED   : taken from a source we actually read (source named in the comment)

Values that must be REPLACED with Person 2's real test results are marked:
    TODO(PERSON-2-RESULT)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

# Folder locations (calculated from where this file sits, so they work on any laptop)
REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "data" / "reference"


class Settings(BaseModel):
    # "frozen" = nobody can change a setting while the program runs (no accidental edits)
    model_config = ConfigDict(frozen=True)

    # ------------------------------------------------------------------ time
    timezone_name: str = "Asia/Kolkata"          # DESIGN: all user-facing times are Indian time

    # ------------------------------------------------- delay stress scenarios
    # Delay is expressed as a fraction of the normal travel time.
    # 0.25 means "the trip takes 25% longer than the route planner says".
    # ASSUMPTION: no India-specific verified source. Shown in the UI and user-adjustable.
    delay_mult: dict[str, float] = {"none": 0.0, "moderate": 0.25, "severe": 0.60}

    # ------------------------------------------------------- scoring of plans
    cvar_q: float = 0.25            # DESIGN: Plan A score = average of the worst 25% of scenarios
    eps_abs_rs: float = 500.0       # ASSUMPTION: A "fails" in a scenario if the best plan beats it by more than this (Rs)...
    eps_rel: float = 0.03           # ASSUMPTION: ...or by more than this fraction of A's own net value (whichever is larger)
    next_day_wait_h: float = 12.0   # ASSUMPTION: hours of waiting if we miss the mandi's closing time

    # ---------------------------------------------------- tomato ripening
    # Assumed (cargo temperature - outside temperature), in degrees C.
    # ASSUMPTION. Only weak evidence: one non-Indian study reported exposed fruit
    # 10-15 C above air temperature. Shown as a RANGE, never as one exact number.
    offsets_c: dict[str, list[float]] = {
        "open": [0.0, 5.0, 10.0],
        "covered": [0.0, 3.0, 6.0],
        "refrigerated": [0.0],      # refrigerated: we use the set-point itself
    }
    # Below this temperature mature-green tomatoes risk chilling injury.
    # VERIFIED: UC Davis postharvest note (mature-green tomatoes are held at 12.5-15 C).
    chilling_threshold_c: float = 12.5
    # One handbook says: avoid transport longer than 12 hours if tomato temperature is above 25 C.
    # Guidance from ONE handbook only (not India-calibrated). Used for an alert message, not for money.
    handbook_alert_hours: float = 12.0
    handbook_alert_temp_c: float = 25.0

    # ----------------------------------------------------- data freshness
    price_degrade_days: int = 3     # ASSUMPTION: price record older than this -> recommendation is downgraded
    price_abstain_days: int = 7     # ASSUMPTION: price record older than this -> system abstains

    # -------------------------------------------------- candidates & routes
    candidate_k: int = 8                  # DESIGN: how many nearest mandis we route (rate limits!)
    waypoint_spacing_km: float = 25.0     # DESIGN: one sample point every ~25 km on a route
    max_waypoints: int = 8                # DESIGN: never more than this many sample points per route
    departure_slots_h: list[float] = [2.0, 4.0, 8.0, 12.0]   # DESIGN: Plan C tries leaving this many hours later

    # ------------------------------------------------------------ monitoring
    poll_interval_s: int = 300            # DESIGN: watchdog checks every 5 minutes (use ~30 for demos)
    hysteresis_n: int = 2                 # DESIGN: a trigger must hold for this many checks in a row
    replan_margin_abs_rs: float = 1000.0  # ASSUMPTION: only propose a change if it gains at least this many Rs...
    replan_margin_rel: float = 0.02       # ASSUMPTION: ...or this fraction of the current plan's value
    cooldown_min: int = 30                # DESIGN: after proposing a change, stay quiet this long
    window_miss_trigger: int = 3          # DESIGN: trigger if Plan A misses the window in this many of 9 scenarios

    # ------------------------------------------------------ caching / limits
    cache_ttl_min: dict[str, int] = {"mandi": 60, "weather": 30, "routes": 1440}   # DESIGN (minutes)
    osrm_min_interval_s: float = 1.0        # POLICY: OSRM demo server allows at most 1 request/second
    nominatim_min_interval_s: float = 1.0   # POLICY: Nominatim allows at most 1 request/second

    # --------------------------------------- facts only Person 2 can confirm
    # TODO(PERSON-2-RESULT): set to True ONLY after Person 2 confirms the price unit
    #                        (Rs per quintal) from a real data.gov.in response (module A1/A3).
    price_unit_verified: bool = False
    # TODO(PERSON-2-RESULT): set to True ONLY if Person 2's Telegram test (A1) received
    #                        live-location updates. Until then we rely on check-in buttons.
    telegram_live_location_verified: bool = False

    @model_validator(mode="after")
    def _sanity_checks(self):
        """Catch typing mistakes in the settings above as early as possible."""
        d = self.delay_mult
        if set(d) != {"none", "moderate", "severe"}:
            raise ValueError("delay_mult must have exactly the keys: none, moderate, severe")
        if not (0 <= d["none"] <= d["moderate"] <= d["severe"]):
            raise ValueError("delay_mult must satisfy 0 <= none <= moderate <= severe")
        if set(self.offsets_c) != {"open", "covered", "refrigerated"}:
            raise ValueError("offsets_c must have exactly the keys: open, covered, refrigerated")
        if not (0 < self.cvar_q <= 1):
            raise ValueError("cvar_q must be between 0 (excluded) and 1")
        if self.price_degrade_days > self.price_abstain_days:
            raise ValueError("price_degrade_days must not exceed price_abstain_days")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the one shared Settings object (created once)."""
    return Settings()


def load_env_file(path: Path | None = None) -> None:
    """
    Read a ".env" file (lines like KEY=value) into environment variables.
    - Lines starting with # are comments; text after " #" on a line is ignored.
    - A variable that is ALREADY set in the real environment is never overwritten.
    - Secrets are not printed or logged anywhere.
    """
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)
