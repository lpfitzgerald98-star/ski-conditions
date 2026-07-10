"""Backtest / calibration helpers -- the tools for tuning the grade curve.

Nothing here is used by the live report path; it exists to answer "does the curve
grade history the way I expect?" and "what real amount does each grade mean?".
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import GRADE_THRESHOLDS, SEASON_COVERAGE_MIN, SEASON_METRIC
from ski.grading import grade_season_to_date, letter_grade
from ski.watercalendar import WATER_YEAR_START_MONTH, water_year, water_year_start


def completed_water_years(obs: pd.DataFrame,
                          wy_start_month: int = WATER_YEAR_START_MONTH) -> list[int]:
    d = obs["date"].dt.date
    return sorted({water_year(x, wy_start_month) for x in d})


def season_grades_by_year(
    obs: pd.DataFrame,
    metric: str = SEASON_METRIC,
    coverage_min: float = SEASON_COVERAGE_MIN,
    season_start_dowy: int = 1,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> pd.DataFrame:
    """Leave-one-out full-season grade for every year with adequate coverage.

    Each year is graded as-if-current (its total ranked against all OTHER years'
    full-season totals), so this shows exactly how the curve buckets real history.
    """
    rows = []
    for wy in completed_water_years(obs, wy_start_month):
        # Grade as of the last day of the water year (generalizes Sep 30 for the
        # Oct-start NH year to whatever the mountain's water year ends on).
        as_of = water_year_start(wy + 1, wy_start_month) - timedelta(days=1)
        g = grade_season_to_date(obs, as_of=as_of, metric=metric,
                                 coverage_min=coverage_min,
                                 season_start_dowy=season_start_dowy,
                                 wy_start_month=wy_start_month)
        if g.current_value is None or g.current_coverage is None:
            continue
        if g.current_coverage < coverage_min or g.n_years < 5:
            continue  # skip stub/partial years
        rows.append((wy, round(g.current_value, 1), g.percentile, g.grade, g.n_years))
    return pd.DataFrame(rows, columns=["water_year", "value", "percentile", "grade", "n_hist"])


def grade_distribution(graded: pd.DataFrame) -> Counter:
    return Counter(graded["grade"])


def curve_calibration(graded: pd.DataFrame) -> list[tuple[int, str, float]]:
    """For each grade cutoff, the real metric value at that percentile of history."""
    vals = graded["value"].to_numpy()
    out = []
    for min_p, grade in GRADE_THRESHOLDS:
        thresh = float(np.percentile(vals, min_p)) if min_p > 0 else float(vals.min())
        out.append((min_p, grade, round(thresh, 1)))
    return out
