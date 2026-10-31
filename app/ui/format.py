"""
UI formatting and label helpers (Stream C).

Follows AGENT.md section 7 wording rules.
Pure functions, tested independently of Streamlit.
"""
from __future__ import annotations

import math
from typing import Any

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
WHAT_IF_TITLE = "What-if scenario (user-set) - not observed"
WHAT_IF_TAG = "[what_if]"


def format_inr(val: float | None) -> str:
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

    if math.isnan(num):
        return UNAVAILABLE

    is_negative = num < 0
    num = abs(num)

    # Convert to integer string
    s = str(int(round(num)))
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


def format_kind_tag(kind: str | None) -> str:
    """
    Format a kind tag from an evidence record:
    e.g. 'observed' -> '[observed]', 'computed' -> '[computed]'.
    If kind is None, empty, or missing: returns ':red[[untagged]]' (never a guessed tag).
    """
    if not kind or not str(kind).strip():
        return ":red[[untagged]]"
    return f"[{kind.strip()}]"


def tag_for(
    value: Any = None,
    evidence_id: str | None = None,
    source: str | None = None,
) -> str:
    """
    Format kind tag for a value:
    - Returns '[computed]' (or format_kind_tag(source)) when source is explicitly given (e.g. source='computed').
    - If evidence_id is given (and source is None), resolves the evidence kind via api_client if available.
    - Returns ':red[[untagged]]' only for genuinely unexplained numbers (neither evidence_id nor source given).
    """
    if source and str(source).strip():
        return format_kind_tag(source)

    if evidence_id and str(evidence_id).strip():
        from app.ui import api_client

        try:
            ev = api_client.get_evidence_cached(evidence_id.strip())
            if ev and ev.get("kind"):
                return format_kind_tag(ev.get("kind"))
        except Exception:
            pass
        return ":red[[untagged]]"

    return ":red[[untagged]]"


def format_source_time(as_of: str | None, fetched_at: str | None) -> str:
    """
    Format source timestamp:
    returns as_of if available, else f'fetched {fetched_at}', else 'unknown'.
    """
    if as_of and str(as_of).strip():
        return str(as_of).strip()
    if fetched_at and str(fetched_at).strip():
        return f"fetched {str(fetched_at).strip()}"
    return "unknown"


def format_location_precision(precision: str | None) -> str:
    """
    Format candidate location precision:
    'market' -> 'market'
    'district_centroid' -> 'district centroid (approximate)'
    None/empty -> 'unknown'
    """
    if not precision:
        return "unknown"
    p = str(precision).strip().lower()
    if p == "market":
        return "market"
    if p == "district_centroid":
        return "district centroid (approximate)"
    return str(precision)


def scan_forbidden_words(text: str) -> list[str]:
    """Scan text for any prohibited marketing or hallucinated claims from AGENT.md §7."""
    lowered = text.lower()
    found = []
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            found.append(phrase)
    return found


def format_what_if_tag() -> str:
    """Return the required what-if tag."""
    return WHAT_IF_TAG


def render_red_box_html(title: str, content: str) -> str:
    """Format the red-bordered what-if scenario container per AGENT.md §7."""
    return (
        f'<div style="border: 2px solid #d32f2f; border-radius: 8px; padding: 16px; margin: 16px 0; background-color: #fff8f8;">'
        f'<h4 style="color: #d32f2f; margin-top: 0; margin-bottom: 8px;">{title}</h4>'
        f'{content}'
        f'</div>'
    )
