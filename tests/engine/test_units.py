"""B1 tests: time, units and the worst-k maths. Numbers here are FIXTURES (invented for testing)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import units

IST = "Asia/Kolkata"


def test_seconds_hours_round_trip():
    assert units.seconds_to_hours(7200) == 2.0
    assert units.hours_to_seconds(1.5) == 5400.0


@pytest.mark.parametrize("text,expected", [("14:30", (14, 30)), ("00:00", (0, 0)), ("23:59", (23, 59)), (" 09:05 ", (9, 5))])
def test_parse_hhmm_good(text, expected):
    assert units.parse_hhmm(text) == expected


@pytest.mark.parametrize("bad", ["24:00", "12:60", "9:30", "1430", "ab:cd", "", "12:30:00", "-1:30"])
def test_parse_hhmm_bad(bad):
    with pytest.raises(ValueError):
        units.parse_hhmm(bad)


def test_naive_datetime_refused():
    with pytest.raises(ValueError):
        units.to_local(datetime(2030, 1, 1, 5, 0), IST)
    with pytest.raises(ValueError):
        units.delayed_arrival(datetime(2030, 1, 1, 5, 0), 3600, 0)


def test_delayed_arrival_formula():
    depart = datetime(2030, 1, 1, 5, 0, tzinfo=timezone.utc)
    assert units.delayed_arrival(depart, 3600, 0.0) == depart + timedelta(hours=1)
    assert units.delayed_arrival(depart, 3600, 0.5) == depart + timedelta(hours=1.5)     # 50% longer
    with pytest.raises(ValueError):
        units.delayed_arrival(depart, -1, 0.0)
    with pytest.raises(ValueError):
        units.delayed_arrival(depart, 10, -0.1)


def test_delayed_arrival_crosses_midnight():
    depart = datetime(2030, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert units.delayed_arrival(depart, 7200, 0).day == 2


def _ist(h, m, s=0):
    return datetime(2030, 1, 1, h, m, s, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def test_window_unknown_when_no_closing_time():
    assert units.window_status(_ist(10, 0), None, IST) == "unknown"


def test_window_met_exactly_at_and_before_closing():
    assert units.window_status(_ist(13, 59), "14:00", IST) == "met"
    assert units.window_status(_ist(14, 0, 0), "14:00", IST) == "met"


def test_window_missed_one_second_late():
    assert units.window_status(_ist(14, 0, 1), "14:00", IST) == "missed"


def test_window_uses_local_time_not_utc():
    # 08:00 UTC = 13:30 IST -> before a 14:00 IST closing time
    utc_arrival = datetime(2030, 1, 1, 8, 0, tzinfo=timezone.utc)
    assert units.window_status(utc_arrival, "14:00", IST) == "met"
    # 09:00 UTC = 14:30 IST -> after it
    assert units.window_status(utc_arrival.replace(hour=9), "14:00", IST) == "missed"


@pytest.mark.parametrize("n,q,k", [(9, 0.25, 3), (18, 0.25, 5), (4, 0.25, 1), (1, 0.25, 1), (9, 1.0, 9), (30, 0.1, 3)])
def test_lowest_k_count(n, q, k):
    assert units.lowest_k_count(n, q) == k


def test_lowest_k_count_rejects_bad_input():
    with pytest.raises(ValueError):
        units.lowest_k_count(0, 0.25)
    with pytest.raises(ValueError):
        units.lowest_k_count(9, 0)
    with pytest.raises(ValueError):
        units.lowest_k_count(9, 1.5)


def test_mean_of_lowest():
    assert units.mean_of_lowest([5, 1, 9, 3], 2) == 2.0      # (1+3)/2
    assert units.mean_of_lowest([7], 1) == 7.0
    with pytest.raises(ValueError):
        units.mean_of_lowest([], 1)
    with pytest.raises(ValueError):
        units.mean_of_lowest([1, 2], 3)
