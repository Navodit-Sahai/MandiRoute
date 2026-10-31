"""MG_MODE guard (AGENT.md NN-2). Shared, frozen after G0."""
from __future__ import annotations
import os
from typing import Literal

_VALID = ("live", "dev_stub", "replay")


def mode() -> Literal["live", "dev_stub", "replay"]:
    m = os.environ.get("MG_MODE", "live").strip().lower()
    if m not in _VALID:
        raise RuntimeError(f"MG_MODE must be one of {_VALID}, got {m!r}")
    return m  # type: ignore[return-value]


def require_stub_allowed(name: str) -> None:
    if mode() != "dev_stub":
        raise RuntimeError(f"stub provider {name!r} is only allowed when MG_MODE=dev_stub (current: {mode()})")


def banner() -> str | None:
    m = mode()
    if m == "dev_stub":
        return "FIXTURE \u2014 NOT LIVE"
    if m == "replay":
        return "HISTORICAL REPLAY \u2014 NOT LIVE"
    return None
