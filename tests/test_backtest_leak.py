"""Leak tests for the point-in-time backtest harness (ski.backtest).

These are the guard rails the whole Phase-2 backtest rests on: if truncation
silently stopped working, every backtest number would be measuring the model's
foreknowledge instead of its skill.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ski import trip
from ski.backtest import (LeakageError, assert_no_leak, obs_asof,
                          outcome_window)


def _obs(years, daily_snow=1.0, depth=40.0):
    rows = []
    for y in years:
        d = date(y - 1, 12, 1)
        stop = date(y, 3, 31)
        while d <= stop:
            rows.append({"date": pd.Timestamp(d), "swe_inches": None,
                         "snow_depth_inches": depth, "new_snow_24hr": daily_snow})
            d = date.fromordinal(d.toordinal() + 1)
    return pd.DataFrame(rows)


def test_obs_asof_truncates():
    obs = _obs([2018, 2019, 2020, 2021])
    T = date(2019, 2, 1)
    cut = obs_asof(obs, T)
    assert cut["date"].max() <= pd.Timestamp(T)
    assert len(cut) < len(obs)


def test_assert_no_leak_passes_on_truncated_and_raises_on_full():
    obs = _obs([2018, 2019, 2020])
    T = date(2019, 2, 1)
    assert_no_leak(obs_asof(obs, T), T)          # clean
    with pytest.raises(LeakageError):
        assert_no_leak(obs, T)                    # full frame must trip it


def test_truncation_actually_changes_climatology():
    # The load-bearing test: if climatology were somehow insensitive to the
    # future years, truncation would be a no-op and the backtest meaningless.
    # Later years snow twice as hard, so including them must move the numbers.
    early = _obs([2016, 2017, 2018], daily_snow=1.0)
    late = _obs([2019, 2020, 2021], daily_snow=3.0)
    full = pd.concat([early, late], ignore_index=True)
    T = date(2018, 6, 1)                          # before the heavy years

    clim_full = trip.climatology(full, 10, 1, "new_snow")
    clim_pit = trip.climatology(obs_asof(full, T), 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2018, 2, 1), 10)

    assert clim_full[dowy]["fresh_in"] > clim_pit[dowy]["fresh_in"], \
        "climatology ignored the future years -- truncation would be meaningless"


def test_outcome_window_is_strictly_forward():
    obs = _obs([2019, 2020])
    T = date(2019, 2, 1)
    w = outcome_window(obs, T, days=7)
    assert w["date"].min() > pd.Timestamp(T)
    assert w["date"].max() <= pd.Timestamp(T) + pd.Timedelta(days=7)
