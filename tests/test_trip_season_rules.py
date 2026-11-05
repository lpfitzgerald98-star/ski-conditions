"""Tests for how the rankings treat the season: a common start date for season
totals, and a mountain's calendar deciding who ranks on the trip list.

No network, no DB: synthetic climatology and real config season windows.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from config import SEASON_TOTAL_START_DOWY
from ski import pipeline, trip
from ski.grading import grade_season_to_date
from ski.trip_commentary import seasonal_pattern_text
from ski.watercalendar import in_season_window

META = {"station": "s", "wy_start": 10, "region": "R"}


def _winter(years, spring_temp_f=50.0):
    """Daily obs Oct 1 -> May 31: cold through Feb, warm from March on."""
    rows = []
    for y in years:
        for d in pd.date_range(f"{y - 1}-10-01", f"{y}-05-31"):
            warm = d.month in (3, 4, 5)
            rows.append({"date": d, "swe_inches": np.nan, "snow_depth_inches": 40.0,
                         "new_snow_24hr": 1.0, "mean_temp_f": spring_temp_f if warm else 15.0})
    return pd.DataFrame(rows)


def _flat_clim(base, fresh=1.0):
    return {d: {"base_in": base, "fresh_in": fresh, "season_in": 100.0, "quality": 80.0,
                "preservation": 80.0, "consistency": 80.0, "n_years": 20}
            for d in range(1, trip.DOWY_MAX + 1)}


def _rows(target, *keys, base=40.0, clim=None):
    meta = {k: META for k in keys}
    return {r["key"]: r for r in trip.roster_baseline_rows(
        target, list(keys), meta, {"s": _flat_clim(base) if clim is None else clim})}


def test_a_later_listed_opening_no_longer_means_a_smaller_season_total():
    obs = _winter([2021, 2022, 2023])
    jan = trip.target_dowy(date(2023, 1, 15), 10)
    from_oct = trip.climatology(obs, 10, SEASON_TOTAL_START_DOWY, "new_snow")
    from_dec = trip.climatology(obs, 10, 62, "new_snow")
    assert from_oct[jan]["season_in"] > from_dec[jan]["season_in"]


SNOW_REPORTER = {"data_source": "acis", "season_window": {"start": (12, 1), "end": (4, 10)}}


def test_the_live_season_total_counts_snow_before_the_listed_opening():
    obs = _winter([2021, 2022, 2023])
    as_of = date(2022, 12, 31)    # Oct 1 -> Dec 31 is 92 days at 1" a day
    assert pipeline.comparable_season_in(obs, SNOW_REPORTER, as_of, 1.0) == 92.0


def test_the_live_season_total_exists_before_the_listed_opening():
    obs = _winter([2021, 2022, 2023])
    assert pipeline.comparable_season_in(obs, SNOW_REPORTER, date(2022, 11, 15), 1.0) == 46.0


def test_the_live_season_total_applies_the_siting_factor():
    obs = _winter([2021, 2022, 2023])
    assert pipeline.comparable_season_in(obs, SNOW_REPORTER, date(2022, 12, 31), 1.5) == 138.0


def test_the_live_season_total_is_none_without_observations():
    assert pipeline.comparable_season_in(pd.DataFrame(), SNOW_REPORTER, date(2022, 12, 31), 1.0) is None


def test_the_season_grade_keeps_the_mountains_own_start():
    obs = _winter([2021, 2022, 2023])
    start = pipeline.mountain_season_start(SNOW_REPORTER)
    grade = grade_season_to_date(obs, as_of=date(2022, 12, 31), metric="new_snow",
                                 season_start_dowy=start, wy_start_month=10)
    assert grade.current_value == 31.0


def test_in_season_window_wraps_the_new_year():
    assert in_season_window(date(2027, 1, 5), (12, 1), (4, 20)) is True
    assert in_season_window(date(2026, 12, 1), (12, 1), (4, 20)) is True
    assert in_season_window(date(2027, 4, 20), (12, 1), (4, 20)) is True
    assert in_season_window(date(2027, 4, 21), (12, 1), (4, 20)) is False
    assert in_season_window(date(2026, 11, 30), (12, 1), (4, 20)) is False


def test_in_season_window_within_one_year():
    assert in_season_window(date(2026, 8, 1), (6, 15), (10, 1)) is True
    assert in_season_window(date(2026, 12, 1), (6, 15), (10, 1)) is False


def test_a_closed_mountain_is_unranked_however_deep_the_typical_base():
    rows = _rows(date(2027, 5, 1), "alta", "mammoth", "arapahoe_basin", base=100.0)   # Alta ends Apr 20
    assert rows["alta"]["baseline_score"] is None
    assert rows["mammoth"]["baseline_score"] is not None


def test_the_may_list_follows_real_closing_dates():
    rows = _rows(date(2027, 5, 1), "schweitzer", "baker", "snowbird", "whistler_blackcomb", base=100.0)
    assert rows["schweitzer"]["baseline_score"] is None
    assert rows["baker"]["baseline_score"] is None
    assert rows["snowbird"]["baseline_score"] is not None
    assert rows["whistler_blackcomb"]["baseline_score"] is not None


def test_a_southern_mountain_is_unranked_in_the_northern_winter():
    rows = _rows(date(2026, 12, 1), "las_lenas", "alta", base=100.0)
    assert rows["las_lenas"]["baseline_score"] is None


def test_an_open_mountain_stays_ranked_after_its_station_melts_out():
    rows = _rows(date(2027, 4, 5), "fernie", "alta", base=0.0)      # Fernie ends Apr 15
    assert rows["fernie"]["baseline_score"] is not None


def test_a_station_with_no_reading_stays_unranked():
    empty = {d: {"base_in": None, "fresh_in": None} for d in range(1, trip.DOWY_MAX + 1)}
    rows = _rows(date(2027, 2, 1), "fernie", clim=empty)
    assert rows["fernie"]["baseline_score"] is None


def test_a_glacier_with_no_fixed_season_goes_by_its_snow():
    assert trip.open_for_trip("zermatt", date(2026, 7, 1)) is None
    meta = {"zermatt": META, "alta": META, "mammoth": META}
    rows = {r["key"]: r for r in trip.roster_baseline_rows(
        date(2027, 5, 1), ["zermatt", "alta", "mammoth"], meta, {"s": _flat_clim(100.0)})}
    assert rows["zermatt"]["baseline_score"] is not None


def test_the_write_up_agrees_with_the_ranking_on_a_melted_out_station():
    text = seasonal_pattern_text("fernie", "Fernie, BC", 10, {"start": (11, 25), "end": (4, 15)},
                                 _flat_clim(0.0, fresh=0.0), date(2027, 4, 5))
    assert "off-season" not in text.lower() and "0 inches" not in text


def test_the_write_up_calls_a_closed_date_off_season_however_deep_the_base():
    window = {"start": (12, 1), "end": (4, 20)}
    open_text = seasonal_pattern_text("alta", "Alta, UT", 10, window, _flat_clim(100.0), date(2027, 2, 1))
    closed_text = seasonal_pattern_text("alta", "Alta, UT", 10, window, _flat_clim(100.0), date(2027, 5, 1))
    assert "100 inches" in open_text
    assert "100 inches" not in closed_text
