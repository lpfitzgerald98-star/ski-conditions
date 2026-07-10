"""Percentile / letter-grade computation -- runs on READ, never on write.

Snowfall is right-skewed, so everything here is percentile/median based. We do
NOT use mean/std-dev z-scores anywhere.

Two separate questions, two separate code paths that never share a number:
  - grade_season_to_date  -> "how good is this season so far vs history"
  - grade_storm / detect_storm_events -> "is something big happening right now"

Season grading is configurable via the daily metric it cumulates
(config.SEASON_METRIC): "swe_gain" (snow water accumulated; full station record)
or "new_snow" (depth-change inches; intuitive but shorter, undercounts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from config import (
    GRADE_THRESHOLDS,
    LOW_CONFIDENCE_YEARS,
    SEASON_COVERAGE_MIN,
    SEASON_METRIC,
    STORM_ALERT_SEASON_SCALING,
    STORM_THRESHOLDS,
)
from ski.watercalendar import WATER_YEAR_START_MONTH, day_of_water_year, water_year

# Human-facing units per season metric.
_METRIC_UNITS = {"swe_gain": "in water", "new_snow": "in snow"}


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def percentile_rank(current_value: float, historical_values) -> float | None:
    """Percent of historical observations strictly below the current value.

    percentile = (# historical below current) / (total) * 100

    Returns None if there is nothing to rank against.
    """
    historical_values = np.asarray(historical_values, dtype=float)
    historical_values = historical_values[~np.isnan(historical_values)]
    if historical_values.size == 0:
        return None
    return float((historical_values < current_value).sum()) / historical_values.size * 100.0


def letter_grade(percentile: float | None, thresholds=GRADE_THRESHOLDS) -> str:
    """Map a percentile to a letter grade using the configured curve."""
    if percentile is None:
        return "N/A"
    for min_p, grade in thresholds:
        if percentile >= min_p:
            return grade
    return thresholds[-1][1]


# ---------------------------------------------------------------------------
# Shared prep
# ---------------------------------------------------------------------------
def _prepare(obs: pd.DataFrame, wy_start_month: int = WATER_YEAR_START_MONTH) -> pd.DataFrame:
    """Return a sorted copy with water-year / day-of-water-year columns added.

    `wy_start_month` is the month the accumulation water year begins (10 = Oct 1
    for the Northern Hemisphere; Southern-Hemisphere mountains pass ~5 so their
    Jun-Oct season falls in one water year instead of splitting across Oct 1).

    Vectorized on purpose. This is the hottest function in the codebase: a single
    scorecard calls it 8 times over the station's whole history, and doing it with
    `.map(day_of_water_year)` meant ~360k per-row Python calls per mountain --
    ~1s of CPU each, which is most of a live roster refresh. The identities below
    are exactly `watercalendar.water_year` / `day_of_water_year`, which remain the
    scalar reference implementation (and are what the tests check this against).
    """
    df = obs.copy()
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    d = df["date"].dt.normalize()

    # water_year: year + 1 once the calendar month reaches the start month.
    wy = d.dt.year + (d.dt.month >= wy_start_month).astype("int64")
    # day_of_water_year: days since (wy - 1)-start_month-01, 1-based.
    starts = pd.to_datetime(dict(year=wy - 1,
                                 month=wy_start_month,
                                 day=1))
    df["wy"] = wy
    df["dowy"] = (d - starts).dt.days + 1
    return df


def _daily_increment(df: pd.DataFrame, metric: str) -> pd.Series:
    """Per-day contribution to the season total for the chosen metric.

    Both variants only credit change across *consecutive* days (a gap -> NaN, not
    a fabricated multi-day dump), and never diff across the Oct 1 water-year
    boundary.
    """
    if metric == "new_snow":
        # Pre-derived at ingest (positive consecutive-day snow-depth change).
        return df["new_snow_24hr"]
    if metric == "swe_gain":
        day_gap = df.groupby("wy")["date"].diff().dt.days
        delta = df.groupby("wy")["swe_inches"].diff()
        return delta.clip(lower=0).where(day_gap == 1)
    raise ValueError(f"unknown season metric '{metric}'")


# ---------------------------------------------------------------------------
# Season-to-date grade
# ---------------------------------------------------------------------------
@dataclass
class SeasonGrade:
    metric: str
    units: str
    as_of: date
    day_of_water_year: int
    current_water_year: int
    current_value: float | None
    percentile: float | None
    grade: str
    n_years: int
    low_confidence: bool
    current_coverage: float | None
    historical: list = field(default_factory=list)  # [(wy, value)]

    def summary(self) -> str:
        cv = "n/a" if self.current_value is None else f"{self.current_value:.1f} {self.units}"
        pct = "n/a" if self.percentile is None else f"{self.percentile:.0f}th pct"
        flag = "  [LOW CONFIDENCE]" if self.low_confidence else ""
        return (
            f"Season-to-date ({self.metric}) as of {self.as_of} "
            f"(day {self.day_of_water_year} of WY{self.current_water_year}): "
            f"{cv} -> {pct} -> grade {self.grade} "
            f"(vs {self.n_years} historical yrs){flag}"
        )


def _window_by_year(obs: pd.DataFrame, lo_dowy: int, hi_dowy: int, metric: str,
                    wy_start_month: int = WATER_YEAR_START_MONTH) -> pd.DataFrame:
    """Per water year: metric total over day-of-water-year [lo, hi], and coverage.

    Coverage denominator is the number of days the window spans, so a year is
    penalized for missing days inside it (and skipped later if too sparse).
    """
    df = _prepare(obs, wy_start_month)
    df["inc"] = _daily_increment(df, metric)
    window = df[(df["dowy"] >= lo_dowy) & (df["dowy"] <= hi_dowy)]
    denom = max(1, hi_dowy - lo_dowy + 1)

    def agg(group: pd.DataFrame) -> pd.Series:
        vals = group["inc"]
        present = int(vals.notna().sum())
        total = float(vals.sum(skipna=True)) if present else np.nan
        return pd.Series({"total": total, "present": present})

    out = window.groupby("wy", group_keys=False).apply(agg, include_groups=False)
    if out.empty:
        return out
    out["coverage"] = out["present"] / denom
    return out


def _season_to_date_by_year(
    obs: pd.DataFrame, cutoff_dowy: int, metric: str, start_dowy: int = 1,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> pd.DataFrame:
    """Per water year: season total over day-of-water-year [start, cutoff], and
    coverage. `start_dowy` defaults to 1 (Oct 1) but can be set to the mountain's
    season start so accumulation and the coverage denominator both begin when the
    resort's winter actually does."""
    return _window_by_year(obs, start_dowy, cutoff_dowy, metric, wy_start_month)


def grade_season_to_date(
    obs: pd.DataFrame,
    as_of: date | None = None,
    metric: str = SEASON_METRIC,
    coverage_min: float = SEASON_COVERAGE_MIN,
    low_confidence_years: int = LOW_CONFIDENCE_YEARS,
    season_start_dowy: int = 1,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> SeasonGrade:
    """Grade this season's cumulative total against history at the same day-of-season.

    `season_start_dowy` is where the season accumulation begins (day-of-water-year;
    1 = Oct 1). Anchoring it at the resort's real season start (rather than always
    Oct 1) keeps the coverage denominator honest for stations that only report
    in-season -- otherwise their missing shoulder-season days sink the coverage
    check. Percentile ranking is relative, so shifting the start moves current and
    historical totals together and leaves ranks largely intact.

    `wy_start_month` sets the water-year boundary (10 = Oct for the Northern
    Hemisphere, ~5 for the Southern) so a season never splits across it.
    """
    as_of = as_of or date.today()
    cutoff = day_of_water_year(as_of, wy_start_month)
    cur_wy = water_year(as_of, wy_start_month)
    units = _METRIC_UNITS.get(metric, "in")

    empty = SeasonGrade(
        metric=metric, units=units, as_of=as_of, day_of_water_year=cutoff,
        current_water_year=cur_wy, current_value=None, percentile=None,
        grade="N/A", n_years=0, low_confidence=True, current_coverage=None,
    )
    if obs is None or obs.empty or cutoff < season_start_dowy:
        return empty

    per_year = _season_to_date_by_year(obs, cutoff, metric, start_dowy=season_start_dowy,
                                       wy_start_month=wy_start_month)
    if per_year.empty:
        return empty

    current_value = current_coverage = None
    if cur_wy in per_year.index:
        current_value = float(per_year.loc[cur_wy, "total"])
        current_coverage = float(per_year.loc[cur_wy, "coverage"])

    hist = per_year.drop(index=cur_wy, errors="ignore")
    hist = hist[(hist["coverage"] >= coverage_min) & hist["total"].notna()]
    historical_pairs = [(int(wy), float(v)) for wy, v in hist["total"].items()]
    historical_values = [v for _, v in historical_pairs]

    pct = percentile_rank(current_value, historical_values) if current_value is not None else None
    n_years = len(historical_values)

    return SeasonGrade(
        metric=metric, units=units, as_of=as_of, day_of_water_year=cutoff,
        current_water_year=cur_wy, current_value=current_value, percentile=pct,
        grade=letter_grade(pct), n_years=n_years,
        low_confidence=n_years < low_confidence_years,
        current_coverage=current_coverage, historical=sorted(historical_pairs),
    )


# ---------------------------------------------------------------------------
# Rolling "hot month" grade (trailing window vs the same window in history)
# ---------------------------------------------------------------------------
@dataclass
class MonthGrade:
    metric: str
    units: str
    as_of: date
    window_days: int
    effective_days: int          # days actually in the window (clipped at season start)
    current_water_year: int
    current_value: float | None
    percentile: float | None
    grade: str
    n_years: int
    low_confidence: bool
    current_coverage: float | None
    historical: list = field(default_factory=list)

    def summary(self) -> str:
        cv = "n/a" if self.current_value is None else f"{self.current_value:.1f} {self.units}"
        pct = "n/a" if self.percentile is None else f"{self.percentile:.0f}th pct"
        flag = "  [LOW CONFIDENCE]" if self.low_confidence else ""
        return (
            f"Last {self.effective_days}d ({self.metric}) ending {self.as_of}: "
            f"{cv} -> {pct} -> grade {self.grade} "
            f"(vs same window in {self.n_years} yrs){flag}"
        )


def grade_rolling_window(
    obs: pd.DataFrame,
    as_of: date | None = None,
    window_days: int = 30,
    metric: str = SEASON_METRIC,
    coverage_min: float = SEASON_COVERAGE_MIN,
    low_confidence_years: int = LOW_CONFIDENCE_YEARS,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> MonthGrade:
    """Grade the trailing `window_days` total against the SAME calendar window
    (same day-of-water-year range) in every historical year.

    Date-matched -- unlike storm grading, which pools all windows. Answers "is
    this a hot month for *this* time of year". Near season start the window is
    clipped to Oct 1 so it isn't unfairly diluted by pre-season days.
    """
    as_of = as_of or date.today()
    hi = day_of_water_year(as_of, wy_start_month)
    lo = max(1, hi - window_days + 1)
    eff = hi - lo + 1
    cur_wy = water_year(as_of, wy_start_month)
    units = _METRIC_UNITS.get(metric, "in")

    empty = MonthGrade(
        metric=metric, units=units, as_of=as_of, window_days=window_days,
        effective_days=eff, current_water_year=cur_wy, current_value=None,
        percentile=None, grade="N/A", n_years=0, low_confidence=True,
        current_coverage=None,
    )
    if obs is None or obs.empty:
        return empty

    per_year = _window_by_year(obs, lo, hi, metric, wy_start_month)
    if per_year.empty:
        return empty

    current_value = current_coverage = None
    if cur_wy in per_year.index:
        current_value = float(per_year.loc[cur_wy, "total"])
        current_coverage = float(per_year.loc[cur_wy, "coverage"])

    hist = per_year.drop(index=cur_wy, errors="ignore")
    hist = hist[(hist["coverage"] >= coverage_min) & hist["total"].notna()]
    historical_pairs = [(int(wy), float(v)) for wy, v in hist["total"].items()]
    historical_values = [v for _, v in historical_pairs]

    pct = percentile_rank(current_value, historical_values) if current_value is not None else None
    n_years = len(historical_values)

    return MonthGrade(
        metric=metric, units=units, as_of=as_of, window_days=window_days,
        effective_days=eff, current_water_year=cur_wy, current_value=current_value,
        percentile=pct, grade=letter_grade(pct), n_years=n_years,
        low_confidence=n_years < low_confidence_years,
        current_coverage=current_coverage, historical=sorted(historical_pairs),
    )


# ---------------------------------------------------------------------------
# Base grade ("deep base for this time of year?")
# ---------------------------------------------------------------------------
@dataclass
class BaseGrade:
    field_name: str              # 'snow_depth_inches' or 'swe_inches'
    units: str
    as_of: date
    day_of_water_year: int
    current_water_year: int
    current_value: float | None
    percentile: float | None
    grade: str
    n_years: int
    low_confidence: bool
    tolerance_days: int
    historical: list = field(default_factory=list)

    def summary(self) -> str:
        cv = "n/a" if self.current_value is None else f"{self.current_value:.0f} {self.units}"
        pct = "n/a" if self.percentile is None else f"{self.percentile:.0f}th pct"
        flag = "  [LOW CONFIDENCE]" if self.low_confidence else ""
        nice = "snow depth" if self.field_name.startswith("snow_depth") else "SWE"
        return (
            f"Current {nice} as of {self.as_of} "
            f"(day {self.day_of_water_year} of WY{self.current_water_year}): "
            f"{cv} -> {pct} -> grade {self.grade} "
            f"(vs same date in {self.n_years} yrs){flag}"
        )


def _value_at_dowy(obs: pd.DataFrame, field_name: str, target_dowy: int,
                   tolerance_days: int,
                   wy_start_month: int = WATER_YEAR_START_MONTH) -> dict[int, float]:
    """Per water year: the `field_name` value on the observation whose
    day-of-water-year is closest to `target_dowy` (within tolerance)."""
    df = _prepare(obs, wy_start_month)
    df = df.dropna(subset=[field_name])
    df = df[(df["dowy"] - target_dowy).abs() <= tolerance_days].copy()
    if df.empty:
        return {}
    df["dist"] = (df["dowy"] - target_dowy).abs()
    idx = df.groupby("wy")["dist"].idxmin()
    picked = df.loc[idx]
    return {int(wy): float(v) for wy, v in zip(picked["wy"], picked[field_name])}


def grade_base(
    obs: pd.DataFrame,
    as_of: date | None = None,
    field_name: str = "snow_depth_inches",
    tolerance_days: int = 3,
    low_confidence_years: int = LOW_CONFIDENCE_YEARS,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> BaseGrade:
    """Grade the CURRENT snowpack (depth or SWE) against the same calendar date in
    history. This is a stock, not a flow -- it reflects melt/settling, so it can
    diverge from the season-snowfall grade after a warm, sunny stretch."""
    as_of = as_of or date.today()
    target = day_of_water_year(as_of, wy_start_month)
    cur_wy = water_year(as_of, wy_start_month)
    units = "in depth" if field_name.startswith("snow_depth") else "in water"

    empty = BaseGrade(
        field_name=field_name, units=units, as_of=as_of, day_of_water_year=target,
        current_water_year=cur_wy, current_value=None, percentile=None,
        grade="N/A", n_years=0, low_confidence=True, tolerance_days=tolerance_days,
    )
    if obs is None or obs.empty:
        return empty

    vals = _value_at_dowy(obs, field_name, target, tolerance_days, wy_start_month)
    current_value = vals.get(cur_wy)
    historical_pairs = sorted((wy, v) for wy, v in vals.items() if wy != cur_wy)
    historical_values = [v for _, v in historical_pairs]

    pct = percentile_rank(current_value, historical_values) if current_value is not None else None
    n_years = len(historical_values)
    return BaseGrade(
        field_name=field_name, units=units, as_of=as_of, day_of_water_year=target,
        current_water_year=cur_wy, current_value=current_value, percentile=pct,
        grade=letter_grade(pct), n_years=n_years,
        low_confidence=n_years < low_confidence_years,
        tolerance_days=tolerance_days, historical=historical_pairs,
    )


# ---------------------------------------------------------------------------
# Storm grade (separate from season grading)
# ---------------------------------------------------------------------------
@dataclass
class StormGrade:
    window_hours: int
    end_date: date
    total_inches: float
    percentile: float | None
    grade: str
    alert: bool
    n_windows: int  # size of the historical window distribution ranked against


def rolling_new_snow(obs: pd.DataFrame, window_days: int) -> pd.Series:
    """Trailing `window_days`-day new-snow totals, indexed by end date.

    The series is reindexed to a gap-free daily calendar first, so a missing day
    inside a window yields NaN (we don't sum across holes) and every window is a
    true fixed-length time window.
    """
    df = _prepare(obs)
    s = df.set_index("date")["new_snow_24hr"].astype(float)
    s = s[~s.index.duplicated(keep="last")]
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    s = s.reindex(full)
    return s.rolling(window_days, min_periods=window_days).sum()


def historical_window_distribution(
    obs: pd.DataFrame, window_days: int, min_inches: float = 0.0
) -> np.ndarray:
    """Historical `window_days` totals (not date-matched).

    With `min_inches` > 0, restrict to windows that were actual snow events -- the
    baseline the storm LETTER grade ranks against. With 0, every window including
    dry days -- the baseline the ALERT percentile uses.
    """
    vals = rolling_new_snow(obs, window_days).dropna().to_numpy()
    if min_inches > 0:
        vals = vals[vals >= min_inches]
    return vals


def default_alert_floor(window_hours: int, base=None) -> float:
    """Base absolute floor for a window, before any season adjustment.

    `base` (a scalar or {hours: inches} dict, e.g. a mountain's storm_floor_inches)
    overrides the global default.
    """
    floors = base if base is not None else STORM_THRESHOLDS["min_inches"]
    if isinstance(floors, dict):
        return float(floors.get(window_hours, next(iter(floors.values()))))
    return float(floors)


def season_adjusted_floor(
    base_floor: float,
    season_percentile: float | None,
    scaling: dict = STORM_ALERT_SEASON_SCALING,
) -> float:
    """Scale the floor by how the season is going: lower in lean years (you're
    hungry for any snow), optionally higher in fat ones (small storms are routine)."""
    if season_percentile is None:
        return base_floor
    if season_percentile < scaling["lean_below_pct"]:
        return base_floor * scaling["lean_factor"]
    if season_percentile >= scaling["fat_above_pct"]:
        return base_floor * scaling["fat_factor"]
    return base_floor


def is_alert_worthy(
    total_inches: float,
    all_windows_percentile: float | None,
    floor: float,
    min_percentile: float = STORM_THRESHOLDS["min_percentile"],
) -> bool:
    """Alert only when BOTH the (possibly adjusted) floor and the all-windows
    percentile clear."""
    if all_windows_percentile is None:
        return False
    return total_inches >= floor and all_windows_percentile >= min_percentile


def grade_storm(
    total_inches: float,
    obs: pd.DataFrame,
    window_hours: int,
    end_date: date,
    alert_floor: float | None = None,
) -> StormGrade:
    """Grade one storm: letter vs real-storm windows, alert vs all windows + floor.

    `alert_floor` overrides the default absolute floor (callers pass a mountain-
    and season-adjusted value); when None, the global default for the window.
    """
    window_days = max(1, window_hours // 24)
    baseline = STORM_THRESHOLDS["grade_baseline_min_inches"]
    storm_dist = historical_window_distribution(obs, window_days, baseline)
    all_dist = historical_window_distribution(obs, window_days, 0.0)
    grade_pct = percentile_rank(total_inches, storm_dist)
    alert_pct = percentile_rank(total_inches, all_dist)
    floor = alert_floor if alert_floor is not None else default_alert_floor(window_hours)
    return StormGrade(
        window_hours=window_hours,
        end_date=end_date,
        total_inches=float(total_inches),
        percentile=grade_pct,
        grade=letter_grade(grade_pct),
        alert=is_alert_worthy(total_inches, alert_pct, floor),
        n_windows=int(storm_dist.size),
    )


def detect_storm_events(
    obs: pd.DataFrame,
    window_hours: int,
    water_year_filter: int | None = None,
    min_inches: float = STORM_THRESHOLDS["grade_baseline_min_inches"],
    top_n: int = 10,
    alert_floor: float | None = None,
    wy_start_month: int = WATER_YEAR_START_MONTH,
) -> list[StormGrade]:
    """Find the biggest distinct storms (peaks >= min_inches, separated so one
    storm isn't counted twice) and grade each against all history.

    If `water_year_filter` is given, only peaks ending in that water year are
    returned (e.g. "last season's storms").
    """
    window_days = max(1, window_hours // 24)
    roll = rolling_new_snow(obs, window_days).dropna()
    if roll.empty:
        return []

    # Grade every event against the FULL historical baselines, but restrict
    # peak-detection to the requested season first (else a quiet season's storms
    # get shadowed by all-time peaks and vanish before ranking).
    baseline = STORM_THRESHOLDS["grade_baseline_min_inches"]
    storm_dist = historical_window_distribution(obs, window_days, baseline)
    all_dist = historical_window_distribution(obs, window_days, 0.0)

    season = roll
    if water_year_filter is not None:
        keep = roll.index.map(lambda ts: water_year(ts.date(), wy_start_month) == water_year_filter)
        season = roll[keep.to_numpy()]

    candidates = season[season >= min_inches].sort_values(ascending=False)

    chosen: list[pd.Timestamp] = []
    for ts in candidates.index:
        if all(abs((ts - c).days) >= window_days for c in chosen):
            chosen.append(ts)
        if len(chosen) >= top_n:
            break

    floor = alert_floor if alert_floor is not None else default_alert_floor(window_hours)
    events = []
    for ts in chosen:
        total = float(roll.loc[ts])
        grade_pct = percentile_rank(total, storm_dist)
        alert_pct = percentile_rank(total, all_dist)
        events.append(StormGrade(
            window_hours=window_hours, end_date=ts.date(), total_inches=total,
            percentile=grade_pct, grade=letter_grade(grade_pct),
            alert=is_alert_worthy(total, alert_pct, floor),
            n_windows=int(storm_dist.size),
        ))

    events.sort(key=lambda e: e.total_inches, reverse=True)
    return events[:top_n]
