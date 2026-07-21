"""Part 2 (forecast integration) tests:
  - score.phase_adjusted_snow_in    -- temperature-based precip-phase correction
  - pipeline.weighted_incoming_percentile -- 24/48/72h horizon blend
  - ski.forecast_log                -- record / read round trip
  - pipeline.forecast_accuracy      -- backtest report

No network. Uses a real (temp) SQLite file via ski.db so weighted_incoming_
percentile can read station history and forecast_log can round-trip.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date

import pandas as pd

from config import MOUNTAINS, SEASON_SWE_TO_SNOWFALL_RATIO
from ski import forecast_log
from ski.grading import SeasonGrade
from ski.db import upsert_observations
from ski.pipeline import (combine_forecast_percentile, forecast_accuracy,
                          medium_range_percentile, season_snow_equivalent_in,
                          weighted_incoming_percentile)
from ski.score import phase_adjusted_snow_in
from ski.sources.outlook import MediumRangeBand, Outlook


# --- phase_adjusted_snow_in -------------------------------------------------
def test_phase_full_credit_when_cold():
    assert phase_adjusted_snow_in(10.0, 20.0) == 10.0


def test_phase_zero_credit_when_warm():
    assert phase_adjusted_snow_in(10.0, 40.0) == 0.0


def test_phase_partial_credit_between():
    # snow_full=32, rain_full=38 -> 35F is the midpoint -> half credit
    assert phase_adjusted_snow_in(10.0, 35.0) == 5.0


def test_phase_missing_temp_is_full_credit():
    assert phase_adjusted_snow_in(10.0, None) == 10.0


def test_phase_zero_snow_stays_zero():
    assert phase_adjusted_snow_in(0.0, 20.0) == 0.0


# --- weighted_incoming_percentile + forecast_log ----------------------------
def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _seed_station(db_path, station_id, n_days=400):
    """A synthetic station history with a real spread of 24h/48h storm windows,
    so historical_window_distribution has something non-trivial to rank against."""
    dates = pd.date_range("2020-10-01", periods=n_days, freq="D")
    import numpy as np
    rng = np.random.default_rng(0)
    new_snow = np.clip(rng.normal(0.3, 1.5, n_days), 0, None)
    depth = np.cumsum(new_snow) * 0.6
    df = pd.DataFrame({
        "date": dates,
        "swe_inches": depth * 0.3,
        "snow_depth_inches": depth,
        "new_snow_24hr": new_snow,
    })
    upsert_observations(db_path, station_id, df)


def test_weighted_percentile_blends_horizons_and_derates_warm_ones():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        _seed_station(db_path, station)

        # A big, cold 24h storm; a smaller but WARM 72h total that should be
        # phase-derated toward zero and therefore barely move the blend.
        outlook = Outlook(
            provider="test",
            snow_in={24: 12.0, 48: 12.0, 72: 20.0},
            tmax_by_window={24: 15.0, 48: 20.0, 72: 40.0},  # 72h is rain-warm
        )
        pct, has_snow, per_horizon = weighted_incoming_percentile(key, outlook, db_path)
        assert has_snow is True
        assert pct is not None
        by_hz = {p["horizon_hours"]: p for p in per_horizon}
        assert by_hz[24]["predicted_inches"] == 12.0          # cold -> full credit
        assert by_hz[72]["predicted_inches"] == 0.0            # 40F -> derated to 0
        assert by_hz[24]["predicted_percentile"] is not None
    finally:
        os.remove(db_path)


def test_weighted_percentile_none_with_no_history():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        outlook = Outlook(provider="test", snow_in={24: 5.0, 48: 5.0, 72: 5.0},
                          tmax_by_window={24: 10.0, 48: 10.0, 72: 10.0})
        pct, has_snow, per_horizon = weighted_incoming_percentile(key, outlook, db_path)
        assert pct is None
        assert len(per_horizon) == 3
    finally:
        os.remove(db_path)


# --- medium-range blend -----------------------------------------------------
def test_medium_range_percentile_ranks_against_matching_window_length():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        _seed_station(db_path, station)

        mr = MediumRangeBand(low_in=1.0, mid_in=2.0, high_in=3.0,
                             horizon_hours=240, confidence="very_low", weight_factor=0.4)
        pct = medium_range_percentile(key, mr, db_path)
        assert pct is not None
        assert 0.0 <= pct <= 100.0
    finally:
        os.remove(db_path)


def test_medium_range_percentile_none_without_band_or_history():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        assert medium_range_percentile(key, None, db_path) is None

        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        mr = MediumRangeBand(low_in=1.0, mid_in=2.0, high_in=3.0,
                             horizon_hours=240, confidence="very_low", weight_factor=0.4)
        # no seeded history for this station -> no baseline to rank against
        assert medium_range_percentile(key, mr, db_path) is None
    finally:
        os.remove(db_path)


def test_combine_forecast_percentile_none_medium_range_is_a_noop():
    assert combine_forecast_percentile(70.0, None, 0.5) == 70.0


def test_combine_forecast_percentile_falls_back_to_medium_range_alone():
    assert combine_forecast_percentile(None, 80.0, 0.5) == 80.0


def test_combine_forecast_percentile_nudges_but_never_dominates():
    near_term = 40.0
    blended = combine_forecast_percentile(near_term, 100.0, mr_weight_factor=1.0, base_weight=0.12)
    # a full-weight, maximally-different medium-range read should still only
    # nudge the near-term blend, not swing it anywhere close to 100.
    assert near_term < blended < near_term + 10


def test_combine_forecast_percentile_tapers_with_weight_factor():
    strong = combine_forecast_percentile(40.0, 100.0, mr_weight_factor=1.0, base_weight=0.12)
    weak = combine_forecast_percentile(40.0, 100.0, mr_weight_factor=0.4, base_weight=0.12)
    assert 40.0 < weak < strong


# --- season_snow_equivalent_in -----------------------------------------------
def _season_grade(metric, current_value):
    return SeasonGrade(
        metric=metric, units="in", as_of=date(2026, 1, 10), day_of_water_year=100,
        current_water_year=2026, current_value=current_value, percentile=50.0,
        grade="B", n_years=10, low_confidence=False, current_coverage=1.0,
    )


def test_season_snow_equivalent_converts_swe_gain_to_fresh_snowfall():
    # Cumulative SWE -> fresh SNOWFALL inches (~10:1), NOT settled depth (3:1) -- so
    # SWE stations land on the same "inches that fell" scale as snowfall networks.
    sg = _season_grade("swe_gain", 10.0)
    assert season_snow_equivalent_in(sg) == 10.0 * SEASON_SWE_TO_SNOWFALL_RATIO


def test_season_snow_equivalent_passes_new_snow_through():
    sg = _season_grade("new_snow", 42.0)
    assert season_snow_equivalent_in(sg) == 42.0


def test_season_snow_equivalent_none_without_a_value():
    sg = _season_grade("swe_gain", None)
    assert season_snow_equivalent_in(sg) is None
    assert season_snow_equivalent_in(None) is None


# --- forecast_log ------------------------------------------------------------
def test_forecast_log_round_trip():
    db_path = _tmp_db()
    try:
        forecast_log.record(db_path, "alta", date(2026, 1, 10), 24, 8.0, 72.0, 15.0)
        forecast_log.record(db_path, "alta", date(2026, 1, 10), 72, 15.0, 60.0, 25.0)
        df = forecast_log.read_log(db_path, mountain_key="alta")
        assert len(df) == 2
        assert set(df["horizon_hours"]) == {24, 72}
    finally:
        os.remove(db_path)


def test_forecast_log_first_of_day_wins():
    db_path = _tmp_db()
    try:
        forecast_log.record(db_path, "alta", date(2026, 1, 10), 24, 8.0, 72.0, 15.0)
        forecast_log.record(db_path, "alta", date(2026, 1, 10), 24, 99.0, 99.0, 99.0)
        df = forecast_log.read_log(db_path, mountain_key="alta")
        assert len(df) == 1
        assert df.iloc[0]["predicted_inches"] == 8.0
    finally:
        os.remove(db_path)


# --- forecast_accuracy backtest ---------------------------------------------
def test_forecast_accuracy_empty_with_no_log():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        _seed_station(db_path, station)
        df = forecast_accuracy(key, db_path)
        assert df.empty
    finally:
        os.remove(db_path)


def test_forecast_accuracy_compares_predicted_to_actual():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        _seed_station(db_path, station)

        made_for = date(2020, 10, 5)          # inside the seeded history
        forecast_log.record(db_path, key, made_for, 24, 5.0, 70.0, 15.0)
        df = forecast_accuracy(key, db_path, as_of=date(2026, 1, 1))
        assert len(df) == 1
        row = df.iloc[0]
        assert row["predicted_inches"] == 5.0
        assert row["actual_inches"] >= 0.0
        assert row["error_inches"] is not None
    finally:
        os.remove(db_path)


def test_forecast_accuracy_skips_unelapsed_horizon():
    db_path = _tmp_db()
    try:
        key = next(iter(MOUNTAINS))
        m = MOUNTAINS[key]
        station = m.get("snotel_station") or m.get("acis_sid") or m.get("cdec_station") \
            or m.get("eccc_station") or m.get("bcsws_station") or m.get("openmeteo_id")
        _seed_station(db_path, station)

        forecast_log.record(db_path, key, date(2026, 1, 10), 24, 5.0, 70.0, 15.0)
        df = forecast_accuracy(key, db_path, as_of=date(2026, 1, 10))  # horizon not elapsed
        assert df.empty
    finally:
        os.remove(db_path)
