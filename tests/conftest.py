"""Shared test setup. FIXTURE helpers here are NOT observed data."""
from __future__ import annotations

import pytest

from app.data import http, mandi, routing


class FakeClock:
    """Deterministic monotonic clock; sleep() advances it instantly (no real waiting in tests)."""
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    for k in ("DATAGOV_KEY", "MG_CONTACT_EMAIL", "MG_PRICE_UNIT_VERIFIED", "DATAGOV_STATE_FILTER",
              "TG_TOKEN", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "ORS_KEY"):
        monkeypatch.delenv(k, raising=False)
    csv = tmp_path / "coords.csv"
    csv.write_text("mandi_id,market,district,state,lat,lon,precision,verified,source,note\n")
    monkeypatch.setenv("MG_COORDS_CSV", str(csv))
    clock = FakeClock()
    http.set_time_hooks(clock=clock.monotonic, sleep=clock.sleep)
    http.reset_limiters()
    routing.reset_breaker()
    mandi._sticky_filter_style = None
    yield clock
    http.reset_time_hooks()


@pytest.fixture
def clock(isolate) -> FakeClock:
    return isolate
