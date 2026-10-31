"""
Assumptions Registry (PLAN.md section 6) + tiny .env loader. Shared, frozen after G0.

ALL tunable numbers of the project live HERE (and nowhere else).
Every value is tagged:
    ASSUMPTION : our own choice with no verified India-specific source -> must be visible in the UI
    DESIGN     : an engineering choice (not a claim about the real world)
    POLICY     : forced by an API provider's usage policy
    VERIFIED   : taken from a source we actually read
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

# Folder locations
REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "data" / "reference"

# CR-5: Ripening feature flag
RIPENING_ENABLED: bool = False


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ------------------------------------------------------------------ time
    timezone_name: str = "Asia/Kolkata"          # DESIGN: all user-facing times are Indian time

    # ------------------------------------------------- delay stress scenarios
    delay_mult: dict[str, float] = {"none": 0.0, "moderate": 0.25, "severe": 0.60}   # ASSUMPTION

    # ------------------------------------------------------- scoring of plans
    cvar_q: float = 0.25                         # DESIGN: Plan A score = average of worst 25%
    eps_abs_rs: float = 500.0                    # ASSUMPTION
    eps_rel: float = 0.03                        # ASSUMPTION
    next_day_wait_h: float = 12.0                # ASSUMPTION

    # ---------------------------------------------------- tomato ripening
    ripening_enabled: bool = False               # CR-5 (scope request SR-1)
    ripening_limit_pct: float = 25.0             # user threshold
    offsets_c: dict[str, list[float]] = {        # ASSUMPTION
        "open": [0.0, 5.0, 10.0],
        "covered": [0.0, 3.0, 6.0],
        "refrigerated": [0.0],
    }
    chilling_threshold_c: float = 12.5           # VERIFIED: UC Davis note
    handbook_alert_hours: float = 12.0
    handbook_alert_temp_c: float = 25.0

    # ----------------------------------------------------- data freshness
    price_degrade_days: int = 3                  # ASSUMPTION
    price_abstain_days: int = 7                  # ASSUMPTION

    # -------------------------------------------------- candidates & routes
    candidate_k: int = 8                         # DESIGN
    waypoint_spacing_km: float = 25.0            # DESIGN
    max_waypoints: int = 8                       # DESIGN
    departure_slots_h: list[float] = [2.0, 4.0, 8.0, 12.0]   # DESIGN

    # ------------------------------------------------------------ monitoring
    poll_interval_s: int = 300                   # DESIGN: 5 minutes (demo ~30s)
    poll_interval_demo_s: int = 30
    hysteresis_n: int = 2                        # DESIGN
    replan_margin_abs_rs: float = 1000.0         # ASSUMPTION
    replan_margin_rel: float = 0.02              # ASSUMPTION
    cooldown_min: int = 30                       # DESIGN
    window_miss_trigger: int = 3                 # DESIGN
    rain_trigger_delta_mm: float = 5.0           # ASSUMPTION: CR-6, remaining-route rain increase for T3

    # ------------------------------------------------------ caching / limits
    cache_ttl_s: dict[str, float | None] = {     # seconds; None = never expires
        "mandi": 60 * 60, "weather": 30 * 60, "routes": 24 * 3600, "geocode": None}
    cache_ttl_min: dict[str, int] = {"mandi": 60, "weather": 30, "routes": 1440}
    rate_limit_interval_s: dict[str, float] = {"osrm": 1.0, "nominatim": 1.0}
    osrm_min_interval_s: float = 1.0             # POLICY
    nominatim_min_interval_s: float = 1.0        # POLICY

    # --------------------------------------- facts confirmed at gates
    price_unit_verified: bool = False
    telegram_live_location_verified: bool = False

    @model_validator(mode="after")
    def _sanity_checks(self):
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
    return Settings()


def load_env_file(path: Path | str | None = None) -> None:
    """
    Read a ".env" file (lines like KEY=value) into os.environ.
    - Lines starting with # are comments; text after ' #' is ignored.
    - Existing environment variables are never overwritten.
    - Secrets are never printed or logged.
    """
    env_path = Path(path) if path else (REPO_ROOT / ".env")
    if not env_path.exists() or not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def load_env(path: str | os.PathLike[str] = ".env") -> None:
    load_env_file(path)


def env(name: str, default: str | None = None) -> str | None:
    """Read an env var; empty string counts as unset."""
    v = os.environ.get(name)
    return v if v else default


def datagov_key() -> str | None:
    return env("DATAGOV_KEY")


def price_unit_verified() -> bool:
    """True only after a human confirmed the Rs/quintal unit at gate A1 (NN-15)."""
    return (env("MG_PRICE_UNIT_VERIFIED", "0") or "0").strip().lower() in ("1", "true", "yes")


def _autoload() -> None:
    """Load .env from the repo root and current working directory."""
    load_env_file(REPO_ROOT / ".env")
    load_env_file(".env")


_autoload()
