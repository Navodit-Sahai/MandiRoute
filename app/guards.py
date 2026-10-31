"""
The "no fake data" safety switch.

MG_MODE (an environment variable) decides what the program is allowed to do:
    live      -> only real data. Stub/fixture providers are FORBIDDEN. (default; use this for the demo)
    dev_stub  -> developer testing: fixtures allowed, but the UI shows a red "FIXTURE - NOT LIVE" banner
    replay    -> real HISTORICAL data, UI shows "HISTORICAL REPLAY - NOT LIVE"
"""
from __future__ import annotations

import os
from typing import Literal, Optional

Mode = Literal["live", "dev_stub", "replay"]
_VALID_MODES = ("live", "dev_stub", "replay")


def mode() -> Mode:
    """Read MG_MODE. If it is not set we assume the SAFEST option: live."""
    value = os.environ.get("MG_MODE", "live").strip().lower()
    if value not in _VALID_MODES:
        raise ValueError(f"MG_MODE must be one of {_VALID_MODES}, got '{value}'")
    return value  # type: ignore[return-value]


def require_stub_allowed(name: str) -> None:
    """Call this at the start of any code that returns fixture data. Crashes loudly in live/replay mode."""
    current = mode()
    if current != "dev_stub":
        raise RuntimeError(
            f"'{name}' returns fixture data and is not allowed when MG_MODE='{current}'. "
            "Use real providers, or set MG_MODE=dev_stub for development only."
        )


def banner() -> Optional[str]:
    """Text of the warning banner the UI must show (None in live mode)."""
    current = mode()
    if current == "dev_stub":
        return "FIXTURE - NOT LIVE"
    if current == "replay":
        return "HISTORICAL REPLAY - NOT LIVE"
    return None
