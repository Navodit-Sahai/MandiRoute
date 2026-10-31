"""Assumptions Registry (PLAN.md section 6) + tiny .env loader. Shared, frozen after G0.

Only thresholds/assumptions live here (no magic numbers in logic). Secrets are read from the
environment ONLY (NN-12) via the small accessors at the bottom; they are never logged.
No python-dotenv dependency (not in PLAN section 3) -> a 15-line loader is used instead.
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    delay_mult: dict[str, float] = {"none": 0.0, "moderate": 0.25, "severe": 0.60}   # ASSUMPTION
    cvar_q: float = 0.25                                                             # design
    eps_abs_rs: float = 500.0                                                        # ASSUMPTION
    eps_rel: float = 0.03                                                            # ASSUMPTION
    next_day_wait_h: float = 12.0                                                    # ASSUMPTION
    offsets_c: dict[str, list[float]] = {                                            # ASSUMPTION
        "open": [0.0, 5.0, 10.0], "covered": [0.0, 3.0, 6.0], "refrigerated": [0.0]}
    ripening_limit_pct: float = 25.0                                                 # user threshold
    handbook_alert_transport_h: float = 12.0
    handbook_alert_cargo_c: float = 25.0
    price_degrade_days: int = 3                                                      # ASSUMPTION
    price_abstain_days: int = 7                                                      # ASSUMPTION
    candidate_k: int = 8                                                             # design
    waypoint_spacing_km: float = 25.0                                                # design
    max_waypoints: int = 8                                                           # design
    departure_slots_h: tuple[int, ...] = (2, 4, 8, 12)                               # design
    poll_interval_s: int = 300                                                       # design
    poll_interval_demo_s: int = 30
    hysteresis_n: int = 2                                                            # design
    replan_margin_abs_rs: float = 1000.0                                             # ASSUMPTION
    replan_margin_rel: float = 0.02                                                  # ASSUMPTION
    cooldown_min: int = 30                                                           # design
    window_miss_trigger: int = 3                                                     # design
    # seconds; None = never expires
    cache_ttl_s: dict[str, float | None] = {
        "mandi": 60 * 60, "weather": 30 * 60, "routes": 24 * 3600, "geocode": None}
    # minimum seconds between calls to the same host (policy)
    rate_limit_interval_s: dict[str, float] = {"osrm": 1.0, "nominatim": 1.0}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ---------------- .env loader (does not override variables already set) ----------------
def load_env(path: str | os.PathLike[str] = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


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
    """Load .env from the repo root and the working directory (existing env vars win)."""
    load_env(Path(__file__).resolve().parents[1] / ".env")
    load_env(".env")


_autoload()
