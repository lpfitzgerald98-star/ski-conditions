"""Tests for the station-siting calibration (pipeline.siting_factor +
trip.climatology's siting_factor param). No network, no DB -- synthetic obs
frames, same fixture style as test_trip.py.

The problem being solved: many resorts' stations are proxies (Jackson Hole
reads Phillips Bench, Stowe a valley COOP) that under-catch what the ski
terrain gets, mis-ranking those mountains on quantity. The fix calibrates the
RANKING quantity inputs toward the resort's published annual snowfall
(config.SNOWFALL_NORMALS), shrunk by trust and clamped
(config.SITING_CALIBRATION).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from config import SITING_CALIBRATION, SNOWFALL_NORMALS
from ski import pipeline, trip


def _obs(years, daily_snow=1.0, depth=40.0, start=(12, 1), end=(3, 31)):
    """Winter obs frames: `daily_snow` inches of new snow every day Dec-Mar --
    an annual total of ~121 * daily_snow inches per water year."""
    rows = []
    for y in years:
        d = date(y - 1, *start)
        stop = date(y, *end)
        while d <= stop:
            rows.append({"date": pd.Timestamp(d), "swe_inches": None,
                         "snow_depth_inches": depth, "new_snow_24hr": daily_snow})
            d = date.fromordinal(d.toordinal() + 1)
    return pd.DataFrame(rows)


YEARS = [2016, 2017, 2018, 2019, 2020, 2021]   # > min_years


# --- station_annual_snowfall_in ---------------------------------------------
def test_station_annual_recovers_synthetic_total():
    obs = _obs(YEARS, daily_snow=2.0)          # ~242 in / winter
    total, n = pipeline.station_annual_snowfall_in(obs, 10, "new_snow")
    assert n >= SITING_CALIBRATION["min_years"]
    assert 230 <= total <= 250


def test_station_annual_needs_min_years():
    obs = _obs([2020, 2021], daily_snow=2.0)   # 2 < min_years
    total, n = pipeline.station_annual_snowfall_in(obs, 10, "new_snow")
    assert total is None and n == 2


# --- siting_factor -----------------------------------------------------------
@pytest.fixture
def fake_normal(monkeypatch):
    """Temporarily register a published normal for a synthetic key."""
    def _set(key, inches):
        monkeypatch.setitem(SNOWFALL_NORMALS, key, inches)
    return _set


def test_no_published_normal_means_no_correction(fake_normal):
    obs = _obs(YEARS, daily_snow=2.0)
    assert pipeline.siting_factor("no_such_resort_key", obs, 10, "new_snow") == 1.0


def test_undercounting_proxy_station_corrects_upward(fake_normal):
    # Station measures ~242 in; resort publishes 484 (a 2.0x Stowe-style gap).
    # trust=0.8 -> factor = 1 + 0.8*(2.0-1) = 1.8.
    fake_normal("proxy_resort", 484)
    obs = _obs(YEARS, daily_snow=2.0)
    f = pipeline.siting_factor("proxy_resort", obs, 10, "new_snow")
    assert 1.7 <= f <= 1.9


def test_well_sited_station_stays_near_one(fake_normal):
    fake_normal("good_resort", 242)            # published == measured
    obs = _obs(YEARS, daily_snow=2.0)
    f = pipeline.siting_factor("good_resort", obs, 10, "new_snow")
    assert 0.95 <= f <= 1.05


def test_pathological_ratio_is_clamped(fake_normal):
    # A 5x published/measured gap (marketing fantasy or broken station) must
    # clamp at clamp_hi, not swing the ranking 5x.
    fake_normal("fantasy_resort", 242 * 5)
    obs = _obs(YEARS, daily_snow=2.0)
    f = pipeline.siting_factor("fantasy_resort", obs, 10, "new_snow")
    assert f == SITING_CALIBRATION["clamp_hi"]
    # And the downside clamp: published far BELOW measured.
    fake_normal("snowpocket_resort", 60)
    f2 = pipeline.siting_factor("snowpocket_resort", obs, 10, "new_snow")
    assert f2 == SITING_CALIBRATION["clamp_lo"]


def test_thin_history_never_calibrates(fake_normal):
    fake_normal("thin_resort", 484)
    obs = _obs([2020, 2021], daily_snow=2.0)   # too few years
    assert pipeline.siting_factor("thin_resort", obs, 10, "new_snow") == 1.0


# --- climatology application -------------------------------------------------
def test_climatology_siting_scales_quantity_not_character():
    obs = _obs(YEARS, daily_snow=2.0)
    plain = trip.climatology(obs, 10, 1, "new_snow")
    scaled = trip.climatology(obs, 10, 1, "new_snow", siting_factor=1.5)
    dowy = trip.target_dowy(date(2020, 2, 1), 10)
    p, s = plain[dowy], scaled[dowy]
    # quantity series scale by the factor...
    assert s["base_in"] == pytest.approx(p["base_in"] * 1.5)
    assert s["fresh_in"] == pytest.approx(p["fresh_in"] * 1.5)
    assert s["season_in"] == pytest.approx(p["season_in"] * 1.5)
    # ...but scale-invariant/intensive signals are untouched (CV is unchanged
    # by a multiplier; density is a ratio; preservation is temperature-based).
    assert s["consistency"] == p["consistency"]
    assert s["quality"] == p["quality"]
