"""
B2 tests. The temperature/day numbers come from the real UC Davis table in
data/reference/ripening_ucdavis.json. Trip lengths and settings are FIXTURES.
"""
import pytest

from app.config import Settings
from app.contracts import RipenessStage as S
from app.contracts import VehicleType as V
from app.engine.ripening import MissingTemperatureError, days_to_red, load_table, ripening_exposure


def cfg_with_zero_offset() -> Settings:
    """FIXTURE settings: no cargo offset, so cargo temperature = outside temperature (easy to reason about)."""
    return Settings(offsets_c={"open": [0.0], "covered": [0.0], "refrigerated": [0.0]})


# ---------------------------------------------------------------- the table itself
def test_table_is_well_formed():
    t = load_table()
    assert t["temps_c"] == sorted(t["temps_c"])
    assert set(t["days_to_full_red"]) == {"MG", "BREAKER", "TURNING", "PINK"}


def test_exact_table_points_return_table_values():
    t = load_table()
    for stage, days in t["days_to_full_red"].items():
        for temp, d in zip(t["temps_c"], days):
            assert days_to_red(stage, temp) == pytest.approx(d)


def test_never_extrapolates():
    assert days_to_red(S.PINK, 12.4) is None
    assert days_to_red(S.PINK, 25.1) is None
    assert days_to_red(S.PINK, -5) is None


def test_interpolated_value_lies_between_neighbours():
    d = days_to_red(S.PINK, 21.0)          # between 4 days (20 C) and 3 days (22.5 C)
    assert 3.0 < d < 4.0


def test_unknown_stage_is_an_error():
    with pytest.raises(ValueError):
        days_to_red("LIGHT_RED", 20)


def test_rate_vs_day_interpolation_differ_by_at_most_4_percent():
    """Documented decision D-05: the interpolation choice must not matter much."""
    t = load_table()
    temps = t["temps_c"]
    for stage, days in t["days_to_full_red"].items():
        for i in range(len(temps) - 1):
            mid = (temps[i] + temps[i + 1]) / 2
            by_days = (days[i] + days[i + 1]) / 2
            by_rate = days_to_red(stage, mid)
            assert abs(by_days - by_rate) / by_days <= 0.0401


# ---------------------------------------------------------------- golden trip results
@pytest.mark.parametrize("temp,expected_pct", [(20.0, 10.4), (22.0, 13.2), (25.0, 20.8)])
def test_golden_pink_10_hours(temp, expected_pct):
    r = ripening_exposure(S.PINK, [(10.0, temp)], V.OPEN, None, 25.0, cfg_with_zero_offset())
    assert r.by_offset[0].consumed_pct == pytest.approx(expected_pct, abs=0.05)
    assert r.flag == "ok"


def test_golden_outside_table_is_none_and_flagged():
    r = ripening_exposure(S.PINK, [(10.0, 26.0)], V.OPEN, None, 25.0, cfg_with_zero_offset())
    assert r.by_offset[0].consumed_pct is None
    assert r.by_offset[0].out_of_range is True
    assert r.worst_consumed_pct is None
    assert r.flag == "out_of_range"


def test_segments_add_up():
    cfg = cfg_with_zero_offset()
    whole = ripening_exposure(S.PINK, [(4.0, 20.0), (6.0, 20.0)], V.OPEN, None, 99, cfg)
    one = ripening_exposure(S.PINK, [(10.0, 20.0)], V.OPEN, None, 99, cfg)
    assert whole.by_offset[0].consumed_pct == pytest.approx(one.by_offset[0].consumed_pct)


def test_hotter_means_more_consumed_and_earlier_stage_means_less():
    cfg = cfg_with_zero_offset()
    cool = ripening_exposure(S.PINK, [(10.0, 18.0)], V.OPEN, None, 99, cfg).worst_consumed_pct
    warm = ripening_exposure(S.PINK, [(10.0, 24.0)], V.OPEN, None, 99, cfg).worst_consumed_pct
    green = ripening_exposure(S.MATURE_GREEN, [(10.0, 24.0)], V.OPEN, None, 99, cfg).worst_consumed_pct
    assert warm > cool
    assert green < warm


def test_over_limit_flag():
    r = ripening_exposure(S.PINK, [(10.0, 25.0)], V.OPEN, None, 15.0, cfg_with_zero_offset())    # 20.8% > 15%
    assert r.flag == "over_limit"


def test_offset_range_is_reported_for_every_assumed_offset():
    cfg = Settings(offsets_c={"open": [0.0, 3.0, 6.0], "covered": [0.0], "refrigerated": [0.0]})
    r = ripening_exposure(S.PINK, [(10.0, 17.0)], V.OPEN, None, 99, cfg)
    assert [o.offset_c for o in r.by_offset] == [0.0, 3.0, 6.0]
    pcts = [o.consumed_pct for o in r.by_offset]
    assert pcts == sorted(pcts)                       # more offset -> more ripening
    assert r.worst_consumed_pct == max(pcts)


def test_one_offset_out_of_range_flags_whole_result_but_keeps_worst_of_the_valid_ones():
    cfg = Settings(offsets_c={"open": [0.0, 10.0], "covered": [0.0], "refrigerated": [0.0]})
    r = ripening_exposure(S.PINK, [(10.0, 18.0)], V.OPEN, None, 99, cfg)     # 18 ok, 28 outside table
    assert r.by_offset[0].consumed_pct is not None
    assert r.by_offset[1].consumed_pct is None
    assert r.flag == "out_of_range"
    assert r.worst_consumed_pct == r.by_offset[0].consumed_pct


# ---------------------------------------------------------------- refrigerated trucks
def test_refrigerated_uses_setpoint_not_outside_weather():
    cfg = cfg_with_zero_offset()
    cold_day = ripening_exposure(S.PINK, [(10.0, 10.0)], V.REFRIGERATED, 15.0, 99, cfg)
    hot_day = ripening_exposure(S.PINK, [(10.0, 40.0)], V.REFRIGERATED, 15.0, 99, cfg)
    assert cold_day.by_offset[0].consumed_pct == pytest.approx(hot_day.by_offset[0].consumed_pct)
    assert cold_day.by_offset[0].cargo_temp_c == 15.0


def test_refrigerated_needs_setpoint():
    with pytest.raises(ValueError):
        ripening_exposure(S.PINK, [(10.0, 20.0)], V.REFRIGERATED, None, 99, cfg_with_zero_offset())


def test_golden_mature_green_below_12_5_is_chilling_risk():
    r = ripening_exposure(S.MATURE_GREEN, [(10.0, 20.0)], V.REFRIGERATED, 10.0, 99, cfg_with_zero_offset())
    assert r.flag == "chilling_risk"                  # wins over out_of_range (setpoint 10 is also below the table)


def test_chilling_rule_only_for_mature_green():
    r = ripening_exposure(S.PINK, [(10.0, 20.0)], V.REFRIGERATED, 10.0, 99, cfg_with_zero_offset())
    assert r.flag == "out_of_range"


def test_mature_green_at_or_above_threshold_is_not_chilling():
    r = ripening_exposure(S.MATURE_GREEN, [(10.0, 20.0)], V.REFRIGERATED, 12.5, 99, cfg_with_zero_offset())
    assert r.flag == "ok"


# ---------------------------------------------------------------- bad input
def test_missing_temperature_is_refused_not_guessed():
    with pytest.raises(MissingTemperatureError):
        ripening_exposure(S.PINK, [(5.0, 20.0), (5.0, None)], V.OPEN, None, 25.0, cfg_with_zero_offset())


def test_empty_or_negative_segments_refused():
    cfg = cfg_with_zero_offset()
    with pytest.raises(ValueError):
        ripening_exposure(S.PINK, [], V.OPEN, None, 25.0, cfg)
    with pytest.raises(ValueError):
        ripening_exposure(S.PINK, [(-1.0, 20.0)], V.OPEN, None, 25.0, cfg)


def test_zero_hour_segment_outside_range_does_not_break_result():
    r = ripening_exposure(S.PINK, [(0.0, 40.0), (10.0, 20.0)], V.OPEN, None, 99, cfg_with_zero_offset())
    assert r.by_offset[0].consumed_pct == pytest.approx(10.4, abs=0.05)
    assert r.flag == "ok"
