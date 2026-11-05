"""Tests for scripts/station_health.py: spotting stations that stopped reporting snow."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from station_health import flagged, station_health  # noqa: E402

WINDOW = {"start": (12, 1), "end": (3, 31)}


def _obs(start, end, snow=True):
    days = pd.date_range(start, end)
    val = 1.0 if snow else np.nan
    return pd.DataFrame({"date": days, "snow_depth_inches": val, "swe_inches": np.nan,
                         "new_snow_24hr": np.nan, "mean_temp_f": 20.0})


def test_a_station_sending_only_empty_rows_counts_as_silent():
    obs = pd.concat([_obs("2024-12-01", "2025-02-15"), _obs("2025-02-16", "2026-11-05", snow=False)])
    h = station_health(obs, date(2026, 11, 5), WINDOW)
    assert h["days_silent"] == 628
    assert h["season_coverage"] == 0.0
    assert flagged(h, in_season_now=False).startswith("only 0%")


def test_a_healthy_station_is_not_flagged_out_of_season():
    obs = _obs("2025-10-01", "2026-04-15")
    h = station_health(obs, date(2026, 11, 5), WINDOW)
    assert h["season_coverage"] == 1.0
    assert flagged(h, in_season_now=False) is None


def test_a_station_gone_quiet_in_season_is_flagged():
    obs = _obs("2024-10-01", "2026-01-01")
    h = station_health(obs, date(2026, 2, 1), WINDOW)
    assert h["days_silent"] == 31
    assert flagged(h, in_season_now=True) == "silent 31 days in season"


def test_a_station_with_no_rows_never_reported():
    h = station_health(pd.DataFrame(), date(2026, 11, 5), WINDOW)
    assert flagged(h, in_season_now=True) == "never reported snow"
