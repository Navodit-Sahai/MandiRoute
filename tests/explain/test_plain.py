"""Plain-language layer: plain_grade + price_source_line (FIXTURE_ data only)."""
from __future__ import annotations

import pytest

from app.contracts.inputs import PriceBand
from app.explain import FORBIDDEN_PHRASES, plain_grade, price_source_line

FIXTURE_GRADES = ["ACTIONABLE", "ACTIONABLE_WITH_CAVEATS", "ADVISORY_ONLY", "ABSTAIN"]
FIXTURE_EXPECTED = {
    "hi": ["Theek hai, bhej sakte ho",
           "Bhej sakte ho, par kuch baatein pata nahi (neeche dekhein)",
           "Dhyan se - kai baatein pata nahi, aap khud faisla lein",
           "Abhi salah nahi de sakte - data kaafi nahi hai"],
    "en": ["OK to send", "OK to send, but some things are unknown",
           "Use caution - several things are unknown, decide yourself",
           "Cannot advise yet - not enough data"],
}


def _band(d: str) -> PriceBand:
    return PriceBand(low=800, typical=1000, high=1200, record_date=d, evidence_ids=["ev_fixture1"])


@pytest.mark.parametrize("lang", ["hi", "en"])
def test_plain_grade_all_four(lang):
    got = [plain_grade(g, lang=lang) for g in FIXTURE_GRADES]
    assert got == FIXTURE_EXPECTED[lang]
    assert len(set(got)) == 4


def test_plain_grade_default_is_hindi_and_unknown_raises():
    assert plain_grade("ACTIONABLE") == "Theek hai, bhej sakte ho"
    with pytest.raises(ValueError):
        plain_grade("MAYBE")
    with pytest.raises(ValueError):
        plain_grade("ACTIONABLE", lang="fr")


@pytest.mark.parametrize("lang", ["hi", "en"])
@pytest.mark.parametrize("d,mkt", [("2026-10-29", "FIXTURE_Pune"), ("2025-03-07", "FIXTURE_Nashik")])
def test_price_source_line_uses_real_fields(lang, d, mkt):
    line = price_source_line(_band(d), lang, market=mkt)
    assert d in line and mkt in line and "data.gov.in" in line
    other = "2026-10-29" if d != "2026-10-29" else "2025-03-07"
    assert other not in line  # no hardcoded date


def test_no_forbidden_words_either_language():
    texts = [plain_grade(g, lang=lg) for g in FIXTURE_GRADES for lg in ("hi", "en")]
    texts += [price_source_line(_band("2026-10-29"), lg, market="FIXTURE_Pune") for lg in ("hi", "en")]
    for t in texts:
        assert not [p for p in FORBIDDEN_PHRASES if p in t.lower()], t
