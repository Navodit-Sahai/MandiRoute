"""
Unit tests for UI formatting and wording helpers (Stream C).
"""
from __future__ import annotations

import pytest

from app.ui.format import (
    COMMIT_APPROX_TEMPLATE,
    DELAY_STRESS_LABEL,
    FORBIDDEN_PHRASES,
    MANDI_SOURCE_NOTE,
    NO_FLIP_LABEL,
    PRICE_REPORTED_TEMPLATE,
    ROBUSTNESS_TEMPLATE,
    TRAVEL_TIME_LABEL,
    UNAVAILABLE,
    WINDOW_UNKNOWN_LABEL,
    format_inr,
    scan_forbidden_words,
    unavailable_if_none,
)


def test_format_inr_indian_grouping():
    assert format_inr(0) == "₹0"
    assert format_inr(500) == "₹500"
    assert format_inr(1500) == "₹1,500"
    assert format_inr(51200) == "₹51,200"
    assert format_inr(150000) == "₹1,50,000"
    assert format_inr(12345678) == "₹1,23,45,678"
    assert format_inr(-5000) == "-₹5,000"
    assert format_inr(None) == UNAVAILABLE
    assert format_inr(float("nan")) == UNAVAILABLE


def test_unavailable_if_none():
    assert unavailable_if_none(None) == "unavailable"
    assert unavailable_if_none("5.2 mm") == "5.2 mm"
    assert unavailable_if_none(42) == "42"


def test_no_forbidden_words_in_ui_string_constants():
    constants = [
        TRAVEL_TIME_LABEL,
        DELAY_STRESS_LABEL,
        WINDOW_UNKNOWN_LABEL,
        NO_FLIP_LABEL,
        COMMIT_APPROX_TEMPLATE.format(scenario="severe"),
        ROBUSTNESS_TEMPLATE.format(k=8),
        PRICE_REPORTED_TEMPLATE.format(date="2026-10-31"),
        MANDI_SOURCE_NOTE,
        UNAVAILABLE,
    ]
    for text in constants:
        matches = scan_forbidden_words(text)
        assert matches == [], f"Constant '{text}' contains forbidden phrase: {matches}"


def test_scan_forbidden_words_detects_violations():
    bad_text = "We provide real-time prices with guaranteed AI-optimised profit and spoilage prediction!"
    matches = scan_forbidden_words(bad_text)
    assert "real-time prices" in matches
    assert "guaranteed" in matches
    assert "ai-optimised profit" in matches
    assert "spoilage" in matches
