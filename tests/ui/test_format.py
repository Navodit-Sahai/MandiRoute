"""
Unit tests for UI formatting and wording helpers (Stream C).
"""
from __future__ import annotations

from app.ui.format import (
    COMMIT_APPROX_TEMPLATE,
    DELAY_STRESS_LABEL,
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


def test_format_kind_tag():
    from app.ui.format import format_kind_tag

    assert format_kind_tag("observed") == "[observed]"
    assert format_kind_tag("computed") == "[computed]"
    assert format_kind_tag("forecast") == "[forecast]"
    assert format_kind_tag("proxy") == "[proxy]"
    assert format_kind_tag("user_input") == "[user_input]"
    assert format_kind_tag(None) == ":red[[untagged]]"
    assert format_kind_tag("") == ":red[[untagged]]"
    assert format_kind_tag("   ") == ":red[[untagged]]"


def test_format_source_time():
    from app.ui.format import format_source_time

    assert format_source_time("2026-10-31", None) == "2026-10-31"
    assert format_source_time("2026-10-31", "2026-10-31T15:00:00+00:00") == "2026-10-31"
    assert format_source_time(None, "2026-10-31T15:00:00+00:00") == "fetched 2026-10-31T15:00:00+00:00"
    assert format_source_time(None, None) == "unknown"


def test_format_location_precision():
    from app.ui.format import format_location_precision

    assert format_location_precision("market") == "market"
    assert format_location_precision("district_centroid") == "district centroid (approximate)"
    assert format_location_precision(None) == "unknown"
    assert format_location_precision("") == "unknown"


def test_what_if_formatting_and_constants():
    from app.ui.format import (
        WHAT_IF_TAG,
        WHAT_IF_TITLE,
        format_what_if_tag,
        render_red_box_html,
    )

    assert WHAT_IF_TITLE == "What-if scenario (user-set) - not observed"
    assert WHAT_IF_TAG == "[what_if]"
    assert format_what_if_tag() == "[what_if]"

    html = render_red_box_html(WHAT_IF_TITLE, "<p>content</p>")
    assert "border: 2px solid #d32f2f" in html
    assert WHAT_IF_TITLE in html
    assert "<p>content</p>" in html



def test_tag_for_engine_and_unexplained(monkeypatch):
    from app.ui.format import tag_for

    # Engine-sourced calculation with no evidence_id
    assert tag_for(51200, source="computed") == "[computed]"
    assert tag_for(51200, evidence_id=None, source="computed") == "[computed]"
    assert tag_for("Never feasible", evidence_id=None, source="computed") == "[computed]"
    assert tag_for(source="computed") == "[computed]"

    # Untagged only when neither evidence_id nor source is given
    assert tag_for(12345) == ":red[[untagged]]"
    assert tag_for(12345, evidence_id=None) == ":red[[untagged]]"
    assert tag_for(12345, evidence_id=None, source=None) == ":red[[untagged]]"
    assert tag_for() == ":red[[untagged]]"

    # Evidence ID lookup works as before
    monkeypatch.setattr(
        "app.ui.api_client.get_evidence_cached",
        lambda eid: {"kind": "observed"} if eid == "ev_obs" else {"kind": "forecast"} if eid == "ev_fc" else None,
    )
    assert tag_for(2000.0, evidence_id="ev_obs") == "[observed]"
    assert tag_for(5.0, evidence_id="ev_fc") == "[forecast]"
    assert tag_for(999.0, evidence_id="ev_missing") == ":red[[untagged]]"


def test_translate_top_caveat_all_cases():
    from app.ui.format import scan_forbidden_words, translate_top_caveat

    # Closing time unknown
    hi_c = translate_top_caveat(["latest arrival time unknown: APMC Panvel"], lang="hi")
    en_c = translate_top_caveat(["latest arrival time unknown: APMC Panvel"], lang="en")
    assert "mandi band hone ka time pata nahi" in hi_c
    assert "Market closing time is unknown" in en_c

    # Stale price
    hi_p = translate_top_caveat(["price record older than 2 days: Pune (3 d)"], lang="hi")
    en_p = translate_top_caveat(["price record older than 2 days: Pune (3 d)"], lang="en")
    assert "Bhav puraana hai" in hi_p
    assert "Price record is old" in en_p

    # Empty reasons
    assert "Koi badi samasya nahi hai" in translate_top_caveat([], lang="hi")
    assert "No major caveats" in translate_top_caveat([], lang="en")
    assert "Koi badi samasya nahi hai" in translate_top_caveat(None, lang="hi")

    # Unverified unit
    hi_u = translate_top_caveat(["price unit not verified: Nashik"], lang="hi")
    assert "Bhav ka unit verify nahi hua" in hi_u

    # District centroid location
    hi_loc = translate_top_caveat(["location is a district centroid or unknown: Nashik"], lang="hi")
    assert "exact location approximate hai" in hi_loc

    # Unknown reason fallback
    hi_other = translate_top_caveat(["custom anomaly detected"], lang="hi")
    assert "Dhyan dein: custom anomaly detected" in hi_other

    # Check forbidden words
    for text in (hi_c, en_c, hi_p, en_p, hi_u, hi_loc, hi_other):
        assert not scan_forbidden_words(text)


def test_format_plain_summary():
    from app.contracts.inputs import PriceBand
    from app.ui.format import format_plain_summary

    pb = PriceBand(low=800, typical=1000, high=1200, record_date="2026-10-31", evidence_ids=["ev1"])
    res_hi = format_plain_summary(
        grade="ACTIONABLE_WITH_CAVEATS",
        price_band=pb,
        reasons=["latest arrival time unknown: APMC Panvel"],
        lang="hi",
        market="APMC Panvel",
    )
    assert res_hi["grade_text"] == "Bhej sakte ho, par kuch baatein pata nahi (neeche dekhein)"
    assert "2026-10-31" in res_hi["price_line"] and "APMC Panvel" in res_hi["price_line"]
    assert "mandi band hone ka time pata nahi" in res_hi["caveat_text"]

    res_en = format_plain_summary(
        grade="ACTIONABLE",
        price_band=pb,
        reasons=[],
        lang="en",
        market="Pune",
    )
    assert res_en["grade_text"] == "OK to send"
    assert "2026-10-31" in res_en["price_line"] and "Pune" in res_en["price_line"]
    assert "No major caveats" in res_en["caveat_text"]

