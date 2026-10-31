"""Pure helpers for Stream A: distances, India bounding box, slugs, state-name normalisation.

No I/O except reading the human-reviewed neighbour-state file (data/reference/neighbour_states.json).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

REF_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"
# Mainland India + islands, deliberately generous. Catches swapped lat/lon and foreign hits.
INDIA_LAT = (6.0, 37.6)
INDIA_LON = (68.0, 97.5)
EARTH_R_KM = 6371.0088
# India has no DST, so a fixed +05:30 offset is exact and avoids the `tzdata` dependency on Windows.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def today_ist(now: datetime) -> date:
    return now.astimezone(IST).date()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def in_india(lat: float, lon: float) -> bool:
    return INDIA_LAT[0] <= lat <= INDIA_LAT[1] and INDIA_LON[0] <= lon <= INDIA_LON[1]


def norm(s: str | None) -> str:
    """Lowercase, accent-free, alphanumerics + single spaces. Used for matching only."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _slug_part(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm(s))).strip("-")


def mandi_id(state: str, district: str, market: str) -> str:
    """Stable id: slug(state|district|market). Parts joined by '__'; no ':' or '~' ever appears
    (so option/route ids can safely start with it, see assemble.mandi_ids_from_option_ids)."""
    return "__".join(_slug_part(x) or "unknown" for x in (state, district, market))


def clean_market_name(market: str) -> str:
    """'Pimpalgaon Baswant(Niphad)' -> 'Pimpalgaon Baswant'; drops APMC/Mandi noise words."""
    m = re.sub(r"\(.*?\)", " ", market or "")
    m = re.sub(r"(?i)\b(apmc|mandi|market yard|krishi utpanna bazar samiti|ubs)\b", " ", m)
    return re.sub(r"\s+", " ", m).strip(" ,-")


_GENERIC_HINT = ("sandhai", "market", "mandi", "apmc", "yard", "samiti", "ubs", "uzhavar", "rm")


def bracket_hint(market: str) -> str | None:
    """Place name inside brackets, e.g. 'Pune(Moshi)' -> 'Moshi'. None if absent or a generic market word
    (e.g. 'Perundurai(Uzhavar Sandhai )'). Agmarknet often puts the REAL yard location there."""
    import re as _re
    m = _re.search(r"\(([^)]*)\)", market or "")
    h = _re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if not h or any(w in norm(h).split() for w in _GENERIC_HINT):
        return None
    return h


# Data.gov.in / Nominatim spell some states differently. Canonical key = norm(); value = canonical display.
_STATE_ALIASES = {
    "chattisgarh": "Chhattisgarh", "chhattisgarh": "Chhattisgarh",
    "uttrakhand": "Uttarakhand", "uttaranchal": "Uttarakhand",
    "orissa": "Odisha",
    "keralam": "Kerala",                 # data.gov.in renamed the state (seen live 2026-10-31)
    "pondicherry": "Puducherry",
    "nct of delhi": "Delhi", "delhi": "Delhi", "new delhi": "Delhi",
    "jammu and kashmir": "Jammu and Kashmir", "jammu kashmir": "Jammu and Kashmir",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "andaman nicobar": "Andaman and Nicobar Islands",
}


def canonical_state(s: str | None) -> str:
    n = norm(s)
    return _STATE_ALIASES.get(n, (s or "").strip().title() if n else "")


def same_state(a: str | None, b: str | None) -> bool:
    return bool(norm(a)) and norm(canonical_state(a)) == norm(canonical_state(b))


def state_in_text(state: str, text: str) -> bool:
    """True if the state (or one of its spellings) occurs in free text such as a display_name."""
    t = norm(text)
    cs = norm(canonical_state(state))
    variants = {cs, norm(state)} | {k for k, v in _STATE_ALIASES.items() if norm(v) == cs}
    return any(v and re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", t) for v in variants)


@lru_cache(maxsize=1)
def _neighbour_map() -> dict[str, list[str]]:
    p = REF_DIR / "neighbour_states.json"
    if not p.is_file():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {norm(canonical_state(k)): v for k, v in raw.get("neighbours", {}).items()}


def states_for(origin_state: str) -> list[str]:
    """Origin state first, then its human-reviewed neighbours (canonical names, no duplicates)."""
    out = [canonical_state(origin_state)]
    for n in _neighbour_map().get(norm(canonical_state(origin_state)), []):
        c = canonical_state(n)
        if c not in out:
            out.append(c)
    return out
