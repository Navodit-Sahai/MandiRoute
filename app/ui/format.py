"""
UI formatting and label helpers (Stream C).

Follows AGENT.md section 7 wording rules.
Pure functions, tested independently of Streamlit.
"""
from __future__ import annotations

import math
from typing import Any, Optional

FORBIDDEN_PHRASES: tuple[str, ...] = (
    "real-time prices",
    "predicted price range",
    "road flooded",
    "spoilage",
    "quality loss",
    "confidence 87%",
    "guaranteed",
    "ai-optimised profit",
    "unique / no competitor",
)

# Approved UI labels (AGENT.md §7)
TRAVEL_TIME_LABEL = "Estimated travel time (OSRM, no traffic)"
DELAY_STRESS_LABEL = "Assumed delay stress (adjustable)"
WINDOW_UNKNOWN_LABEL = "Closing time unknown - window not checked"
NO_FLIP_LABEL = "no flip within tested range"
COMMIT_APPROX_TEMPLATE = "Last feasible diversion waypoint under {scenario} (approximate)"
ROBUSTNESS_TEMPLATE = "A is best in {k} of 9 tested scenarios"
PRICE_REPORTED_TEMPLATE = "Price reported (record date: {date})"
MANDI_SOURCE_NOTE = "daily records, not real-time"
UNAVAILABLE = "unavailable"


def format_inr(val: float | int | None) -> str:
    """
    Format a rupee number using Indian numbering system:
    e.g. 51200 -> '₹51,200', 150000 -> '₹1,50,000', -5000 -> '-₹5,000'.
    None -> 'unavailable'.
    """
    if val is None:
        return UNAVAILABLE
    try:
        num = float(val)
    except (ValueError, TypeError):
        return UNAVAILABLE

    if math.isnan(num) or math.isinf(num):
        return UNAVAILABLE

    is_negative = num < 0
    abs_int = abs(int(round(num)))

    s = str(abs_int)
    if len(s) <= 3:
        formatted = s
    else:
        last3 = s[-3:]
        remaining = s[:-3]
        # Group remaining digits by 2s from right to left
        parts = []
        while remaining:
            parts.append(remaining[-2:])
            remaining = remaining[:-2]
        parts.reverse()
        formatted = ",".join(parts) + "," + last3

    prefix = "-₹" if is_negative else "₹"
    return f"{prefix}{formatted}"


def unavailable_if_none(val: Any) -> str:
    """Return 'unavailable' if val is None or empty, else str(val)."""
    if val is None:
        return UNAVAILABLE
    return str(val)


def scan_forbidden_words(text: str) -> list[str]:
    """Scan text for any prohibited marketing or hallucinated claims from AGENT.md §7."""
    lowered = text.lower()
    found = []
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            found.append(phrase)
    return found

