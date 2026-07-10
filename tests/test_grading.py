"""Unit tests for the grading math -- runnable with no network.

Run from the project root:  python -m pytest tests/ -q
(or plain `python tests/test_grading.py` for a quick smoke run.)
"""

from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski.grading import (  # noqa: E402
    detect_storm_events,
    grade_base,
    grade_rolling_window,
    grade_season_to_date,
    historical_window_distribution,
    is_alert_worthy,
    letter_grade,
    percentile_rank,
    rolling_new_snow,
    season_adjusted_floor,
)
from ski.watercalendar import day_of_water_year, water_year  # noqa: E402


# --- percentile_rank matches the spec formula exactly ---
def test_percentile_rank_formula():
    hist = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # 6 values (1..6) are strictly below 7 -> 60%
    assert percentile_rank(7, hist) == 60.0


def test_percentile_rank_extremes():
    hist = [10, 20, 30, 40]
    assert percentile_rank(5, hist) == 0.0      # below everything
    assert percentile_rank(100, hist) == 100.0  # above everything


def test_percentile_rank_empty_and_nan():
    assert percentile_rank(5, []) is None
    assert percentile_rank(5, [np.nan, np.nan]) is None
    # NaNs are dropped, ranking uses the rest
    assert percentile_rank(25, [10, 20, np.nan, 40]) == pytest_approx(66.666, 0.01)


# --- letter grade boundaries follow the configured curve ---
def test_letter_grade_boundaries():
    assert letter_grade(100) == "A+"
    assert letter_grade(96) == "A+"
    assert letter_grade(95.9) == "A"
    assert letter_grade(88) == "A"
    assert letter_grade(80) == "A-"
    assert letter_grade(70) == "B+"
    assert letter_grade(58) == "B"
    assert letter_grade(46) == "B-"
    assert letter_grade(34) == "C+"
    assert letter_grade(24) == "C"
    assert letter_grade(16) == "C-"
    assert letter_grade(11) == "D"
    assert letter_grade(10.9) == "F"
    assert letter_grade(0) == "F"
    assert letter_grade(None) == "N/A"


# --- storm grading: real-storm baseline discriminates; all-windows saturates ---
def _storm_obs():
    """One dry autumn, then a season with a few storms of known sizes."""
    rows = []
    d0 = pd.Timestamp("2023-10-01")
    # 120 dry days (new_snow 0), then storms of 4,4,4,8,12,20 inches on spaced days
    depth = 0.0
    for i in range(120):
        rows.append({"date": d0 + pd.Timedelta(days=i), "swe_inches": 0.0,
                     "snow_depth_inches": 0.0, "new_snow_24hr": 0.0})
    sizes = [4, 4, 4, 4, 4, 8, 12, 20]
    for j, s in enumerate(sizes):
        day = d0 + pd.Timedelta(days=130 + j * 3)  # 3 days apart
        depth += s
        rows.append({"date": day, "swe_inches": depth / 12.0,
                     "snow_depth_inches": depth, "new_snow_24hr": float(s)})
    return pd.DataFrame(rows)


def test_storm_percentile_uses_real_storm_baseline():
    obs = _storm_obs()
    # 24hr distribution restricted to real storms (>=4") has 8 values: the sizes.
    storm_dist = historical_window_distribution(obs, 1, min_inches=4)
    assert len(storm_dist) == 8
    all_dist = historical_window_distribution(obs, 1, min_inches=0)
    # a 12" day is far more distinguished among ALL windows (mostly 0) ...
    p_all = percentile_rank(12, all_dist)
    p_storm = percentile_rank(12, storm_dist)
    assert p_all > p_storm  # all-windows saturates high; storm baseline spreads out


def test_alert_needs_floor_and_percentile():
    dist_all = [0, 0, 0, 0, 1, 2, 3]  # mostly dry
    # 12" clears an 8" floor and is top-decile of all windows -> alert
    assert is_alert_worthy(12, percentile_rank(12, dist_all), floor=8) is True
    # 6" is top-percentile here but below the 8" floor -> no alert
    assert is_alert_worthy(6, 100.0, floor=8) is False
    # ...but if a lean season drops the floor to 5, the same 6" now alerts
    assert is_alert_worthy(6, 100.0, floor=5) is True


def test_season_adjusted_floor():
    # base 8" floor. lean season (below 35th pct) -> 0.6x = 4.8"
    assert season_adjusted_floor(8.0, season_percentile=10) == pytest_approx(4.8, 0.001)
    # normal season -> unchanged
    assert season_adjusted_floor(8.0, season_percentile=50) == 8.0
    # no season info -> unchanged
    assert season_adjusted_floor(8.0, season_percentile=None) == 8.0


def test_base_grade_reads_current_value_vs_same_date():
    # Same-date snow depth: history 40-80", current year 90" -> top of the pack.
    import datetime as _dt
    rows = []
    for wy, depth in {2021: 40, 2022: 55, 2023: 70, 2024: 80, 2025: 90}.items():
        # put an observation right at ~Jan 15 (dowy 107) for each year
        d = _dt.date(wy - 1, 10, 1) + _dt.timedelta(days=106)
        rows.append({"date": pd.Timestamp(d), "swe_inches": depth / 10.0,
                     "snow_depth_inches": float(depth), "new_snow_24hr": 0.0})
    obs = pd.DataFrame(rows)
    g = grade_base(obs, as_of=_dt.date(2025, 1, 15), field_name="snow_depth_inches")
    assert g.current_value == 90.0
    assert g.n_years == 4
    assert g.percentile == 100.0
    assert g.grade == "A+"


def test_detect_storm_events_finds_biggest():
    obs = _storm_obs()
    events = detect_storm_events(obs, 24, water_year_filter=2024, min_inches=4, top_n=5)
    assert events, "expected some storms"
    assert events[0].total_inches == 20.0  # biggest first
    # biggest of the distribution -> top of the pack (can't be 100th since the
    # ranking distribution includes the event itself: 7 of 8 below -> 87.5)
    assert events[0].percentile >= 80
    assert [e.total_inches for e in events] == sorted(
        [e.total_inches for e in events], reverse=True)


# --- water-year calendar ---
def test_water_year_calendar():
    assert water_year(date(2024, 10, 1)) == 2025
    assert water_year(date(2024, 9, 30)) == 2024
    assert day_of_water_year(date(2024, 10, 1)) == 1
    assert day_of_water_year(date(2024, 10, 31)) == 31
    assert day_of_water_year(date(2025, 1, 1)) == 93  # Oct(31)+Nov(30)+Dec(31)+1


def test_month_day_to_dowy():
    from ski.watercalendar import month_day_to_dowy
    assert month_day_to_dowy(10, 1) == 1        # Oct 1 -> day 1
    assert month_day_to_dowy(12, 1) == 62       # Oct(31)+Nov(30)+1
    assert month_day_to_dowy(4, 20) == 202


def test_hemisphere_aware_water_year():
    from ski.watercalendar import day_of_water_year, water_year
    # Southern Hemisphere: water year starts in May, so the whole Jun-Oct season
    # sits in ONE water year (named for the calendar year it ends in).
    assert water_year(date(2024, 6, 1), start_month=5) == 2025
    assert water_year(date(2024, 8, 15), start_month=5) == 2025
    assert water_year(date(2024, 10, 1), start_month=5) == 2025   # still same season
    assert water_year(date(2024, 4, 30), start_month=5) == 2024   # prior water year
    assert day_of_water_year(date(2024, 5, 1), start_month=5) == 1
    # Default (Northern, Oct start) is unchanged
    assert water_year(date(2024, 10, 1)) == 2025


def test_sh_season_stays_in_one_water_year():
    # A Southern-Hemisphere resort (May water year): Jun 1 -> Oct 1 accumulation
    # should grade as one continuous season, not split across a boundary.
    import datetime as _dt
    rows = []
    for wy, total in {2021: 60, 2022: 80, 2023: 100, 2024: 70, 2025: 120}.items():
        start = _dt.date(wy - 1, 6, 1)   # Jun 1 of the season that ends in `wy`
        depth = 0.0
        for i in range(130):             # Jun 1 .. ~Oct 8
            new = 1.0 if i < int(total / 2) else 0.0
            depth += new
            rows.append({"date": pd.Timestamp(start + pd.Timedelta(days=i)),
                         "swe_inches": np.nan, "snow_depth_inches": depth,
                         "new_snow_24hr": new})
    obs = pd.DataFrame(rows)
    g = grade_season_to_date(obs, as_of=_dt.date(2024, 10, 1), metric="new_snow",
                             season_start_dowy=32, wy_start_month=5)
    assert g.current_water_year == 2025
    assert g.n_years == 4
    assert g.current_value is not None      # Oct 1 not lost across a boundary
    assert g.percentile is not None


def test_season_start_anchors_accumulation_and_coverage():
    # A station that only reports IN-SEASON (no Oct/Nov data). Anchoring the season
    # start at Dec 1 keeps coverage honest; the default Oct-1 start sinks it.
    import datetime as _dt
    rows = []
    for wy, dec_snow in {2020: 60, 2021: 70, 2022: 80, 2023: 90, 2024: 50}.items():
        # report only Dec 1 .. Feb 15 (dowy 62..138), 1"/day for dec_snow days
        for i in range(78):  # Dec 1 .. mid-Feb
            d = _dt.date(wy - 1, 12, 1) + pd.Timedelta(days=i)
            new = 1.0 if i < int(dec_snow / 2) else 0.0
            rows.append({"date": pd.Timestamp(d), "swe_inches": np.nan,
                         "snow_depth_inches": np.nan, "new_snow_24hr": new})
    obs = pd.DataFrame(rows)
    as_of = _dt.date(2024, 2, 15)
    oct1 = grade_season_to_date(obs, as_of=as_of, metric="new_snow")           # start Oct 1
    dec1 = grade_season_to_date(obs, as_of=as_of, metric="new_snow",
                                season_start_dowy=62)                          # start Dec 1
    # Oct-1 anchor: October/November are all missing -> coverage fails -> years drop
    assert dec1.n_years > oct1.n_years
    assert dec1.n_years == 4          # all four historical years now qualify
    assert dec1.percentile is not None


# --- season grade end to end on synthetic multi-year data ---
def _synthetic_obs(year_totals: dict[int, float], through_month=12, through_day=31):
    """Build daily obs where each water year accumulates `total` inches evenly
    over Oct 1 .. (through_month/through_day), 1 in of depth per snowy day.

    We distribute `total` as `total` days of exactly 1 inch new snow so the
    season-to-date sum through that date equals a known fraction.
    """
    rows = []
    for wy, total in year_totals.items():
        start = date(wy - 1, 10, 1)
        # one row per day; put `total` inches spread as 1"/day on the first
        # `int(total)` days, rest zero, so cumulative through the window is total.
        end = date(wy - 1, through_month, through_day) if through_month >= 10 else date(wy, through_month, through_day)
        n_days = (end - start).days + 1
        depth = 0.0
        for i in range(n_days):
            d = start + pd.Timedelta(days=i)
            new = 1.0 if i < int(total) else 0.0
            depth += new
            rows.append({"date": pd.Timestamp(d), "swe_inches": depth / 10.0,
                         "snow_depth_inches": depth, "new_snow_24hr": new})
    return pd.DataFrame(rows)


def test_season_grade_ranks_current_high():
    # 5 historical years at 50-90", current year (2025) at 200" -> top percentile.
    totals = {2020: 50, 2021: 60, 2022: 70, 2023: 80, 2024: 90, 2025: 200}
    obs = _synthetic_obs(totals, through_month=12, through_day=31)
    g = grade_season_to_date(obs, as_of=date(2024, 12, 31))
    assert g.current_water_year == 2025
    assert g.n_years == 5
    assert g.percentile == 100.0
    assert g.grade == "A+"
    assert g.low_confidence is True  # only 5 yrs < LOW_CONFIDENCE_YEARS


def test_season_grade_ranks_current_low():
    totals = {2020: 150, 2021: 160, 2022: 170, 2023: 180, 2024: 190, 2025: 40}
    obs = _synthetic_obs(totals, through_month=12, through_day=31)
    g = grade_season_to_date(obs, as_of=date(2024, 12, 31))
    assert g.percentile == 0.0
    assert g.grade == "F"


def test_sparse_history_year_is_skipped():
    # Give 2022 almost no data in the window -> it should be dropped for coverage.
    totals = {2020: 80, 2021: 90, 2023: 100, 2025: 120}
    obs = _synthetic_obs(totals, through_month=12, through_day=31)
    # blow a hole in 2022 by adding a year with only 2 days present
    start = date(2021, 10, 1)
    sparse = pd.DataFrame([
        {"date": pd.Timestamp(start), "swe_inches": 0.1, "snow_depth_inches": 1, "new_snow_24hr": 1.0},
        {"date": pd.Timestamp(start + pd.Timedelta(days=1)), "swe_inches": 0.2,
         "snow_depth_inches": 2, "new_snow_24hr": 1.0},
    ])
    obs = pd.concat([obs, sparse], ignore_index=True)
    g = grade_season_to_date(obs, as_of=date(2024, 12, 31))
    hist_years = {wy for wy, _ in g.historical}
    assert 2022 not in hist_years  # skipped for insufficient coverage
    assert {2020, 2021, 2023} <= hist_years


def test_rolling_month_is_date_matched():
    # Current year gets a big December; history has quiet Decembers -> hot month.
    totals = {2020: 60, 2021: 60, 2022: 60, 2023: 60, 2024: 60, 2025: 60}
    obs = _synthetic_obs(totals, through_month=12, through_day=31)
    # Grade the last 30 days ending Dec 31. In _synthetic_obs each year lays its
    # `total` inches as 1"/day on the first int(total) days from Oct 1, so by late
    # Dec all years are flat -> current sits mid-pack, not extreme.
    g = grade_rolling_window(obs, as_of=date(2024, 12, 31), window_days=30, metric="new_snow")
    assert g.effective_days == 30
    assert g.current_water_year == 2025
    assert g.n_years == 5
    assert g.percentile is not None


def test_rolling_month_clips_at_season_start():
    totals = {2024: 40, 2025: 40}
    obs = _synthetic_obs(totals, through_month=12, through_day=31)
    # 30-day window ending Oct 10 can only span 10 days (Oct 1-10).
    g = grade_rolling_window(obs, as_of=date(2024, 10, 10), window_days=30, metric="new_snow")
    assert g.effective_days == 10


def test_forecast_snow_parsing_and_windowing():
    from ski.sources.nws import SnowBlock, _iso_duration_hours, forecast_snow_total

    assert _iso_duration_hours("PT6H") == 6
    assert _iso_duration_hours("P1DT6H") == 30
    assert _iso_duration_hours("PT30M") == 0.5

    now = pd.Timestamp("2026-01-01T00:00:00+00:00")
    # 5" in the first 6h, 5" in hours 6-12, 10" in a block that starts at 24h (outside 24h window)
    blocks = [
        SnowBlock(now, now + pd.Timedelta(hours=6), 5.0),
        SnowBlock(now + pd.Timedelta(hours=6), now + pd.Timedelta(hours=12), 5.0),
        SnowBlock(now + pd.Timedelta(hours=24), now + pd.Timedelta(hours=30), 10.0),
    ]
    assert forecast_snow_total(blocks, 24, now) == 10.0   # first two blocks only
    assert forecast_snow_total(blocks, 72, now) == 20.0   # all three
    # half-overlap of a block is counted proportionally
    partial = [SnowBlock(now, now + pd.Timedelta(hours=12), 12.0)]
    assert forecast_snow_total(partial, 6, now) == 6.0


def test_absolute_anchors_and_cover_gate():
    from ski.score import conditions_score, cover_factor, depth_score, fresh_score, piecewise

    # piecewise interpolates and clamps
    assert piecewise(-5, [(0, 0), (10, 100)]) == 0
    assert piecewise(5, [(0, 0), (10, 100)]) == 50
    assert piecewise(99, [(0, 0), (10, 100)]) == 100
    # absolute depth: thin is thin anywhere
    assert depth_score(0) == 0
    assert depth_score(24) == 50
    assert depth_score(200) == 100
    assert depth_score(None) is None
    # fresh week: 0" is a groomer baseline, not zero
    assert fresh_score(0) == 35
    assert fresh_score(18) == 100
    # cover gate: 1.0 when deep or unknown, floor at zero cover
    assert cover_factor(None) == 1.0
    assert cover_factor(80) == 1.0
    assert cover_factor(0) == pytest_approx(0.35, 0.001)
    assert cover_factor(0) < cover_factor(24) < cover_factor(80)
    # conditions blends whatever is available, renormalized
    only_rel = conditions_score(80.0)
    assert only_rel == 80.0
    dry_week = conditions_score(80.0, fresh_7d_inches=0.0)  # groomer week drags it
    assert dry_week < only_rel
    powder_week = conditions_score(80.0, fresh_7d_inches=18.0)
    assert powder_week > dry_week
    assert conditions_score(None) is None
    # absolute depth lives ONLY in the cover gate now -- not a conditions input
    import inspect
    from ski.score import conditions_score as _cs
    assert "depth_inches" not in inspect.signature(_cs).parameters


def test_cover_gate_fixes_thin_base_inversion():
    from ski.score import cover_factor, overall_score
    # A thin-cover hill at its 90th percentile vs a deep mountain at its 55th.
    thin = {"season": 90.0, "in_season": 90.0, "forecast": None, "conditions": 60.0}
    deep = {"season": 55.0, "in_season": 55.0, "forecast": None, "conditions": 65.0}
    ungated_thin = overall_score(thin, "season").value
    ungated_deep = overall_score(deep, "season").value
    assert ungated_thin > ungated_deep          # the old, wrong ordering
    gated_thin = overall_score(thin, "season", cover=cover_factor(10)).value
    gated_deep = overall_score(deep, "season", cover=cover_factor(55)).value
    assert gated_deep > gated_thin              # the gate flips it


def test_weather_and_forecast_subscores():
    from ski.score import (forecast_score, temp_score, weather_quality, wind_score)

    assert temp_score(22) == 100.0            # in ideal band
    assert temp_score(48) == 0.0              # slush point
    assert temp_score(-5) == 45.0            # deep-cold floor
    assert wind_score(0) == 100.0
    assert wind_score(35) == 0.0
    # forecast drops out (None) when nothing's coming AND nothing threatens,
    # boosts when snow is coming
    assert forecast_score(None, has_incoming_snow=False) is None
    assert forecast_score(100.0, has_incoming_snow=True) == 100.0
    assert forecast_score(80.0, has_incoming_snow=True) == 90.0
    assert 0 <= weather_quality(25, 5, 20) <= 100


def test_thaw_index_and_forecast_downside():
    from ski.score import forecast_score, thaw_index

    # benign: cold and dry -> no threat; None inputs -> no threat (not "bad")
    assert thaw_index(0.0, 20.0) == 0.0
    assert thaw_index(None, None) == 0.0
    # rain ramps to a full penalty; warmth alone caps at warm_weight
    assert thaw_index(1.0, 20.0) == 1.0
    assert 0 < thaw_index(0.5, 20.0) < 1.0
    assert thaw_index(0.0, 55.0) == pytest_approx(0.5, 0.001)  # no taper w/o progress
    assert thaw_index(0.0, 47.5) < thaw_index(0.0, 55.0)
    # warm_zero raised to 40: a 40F day no longer registers as a thaw
    assert thaw_index(0.0, 40.0) == 0.0
    # a dry-but-thawing forecast now DRAGS instead of dropping out
    assert forecast_score(None, has_incoming_snow=False, thaw=1.0) == 0.0
    assert forecast_score(None, has_incoming_snow=False, thaw=0.4) == 30.0
    # wet storm: snow boost nets against the thaw
    wet = forecast_score(80.0, has_incoming_snow=True, thaw=0.4)
    dry = forecast_score(80.0, has_incoming_snow=True, thaw=0.0)
    assert wet == pytest_approx(70.0, 0.001) and wet < dry
    # zero thaw + no snow still drops out entirely
    assert forecast_score(None, has_incoming_snow=False, thaw=0.0) is None


def test_thaw_warmth_tapers_with_season_progress():
    from ski.score import thaw_index
    # the SAME warm day is a real threat early, nearly moot in spring
    early = thaw_index(0.0, 55.0, season_progress=0.0)
    late = thaw_index(0.0, 55.0, season_progress=1.0)
    assert early == pytest_approx(0.5, 0.001)     # full warm weight at season start
    assert late == pytest_approx(0.1, 0.001)      # residual (1 - 0.8) at season end
    assert 0.1 < thaw_index(0.0, 55.0, season_progress=0.5) < 0.5
    # rain is NOT tapered -- rain-on-snow is bad in any month
    assert thaw_index(1.0, 20.0, season_progress=1.0) == 1.0


def test_refreeze_index_and_apply():
    from ski.score import apply_refreeze, refreeze_index
    # a recent thaw that refroze, no new snow -> a crust
    crust = refreeze_index(rain_72h_in=0.6, tmax_72h_f=44.0, tmin_24h_f=20.0,
                           fresh_7d_inches=0.0)
    assert crust > 0.5
    # never thawed -> no crust regardless of how cold it got
    assert refreeze_index(0.0, 20.0, 5.0, 0.0) == 0.0
    # thawed but never refroze (stayed warm) -> no locked-in crust
    assert refreeze_index(0.6, 44.0, 40.0, 0.0) == 0.0
    # fresh snow resurfaces the crust: a foot of new snow heals it
    healed = refreeze_index(0.6, 44.0, 20.0, fresh_7d_inches=12.0)
    assert healed == 0.0
    assert refreeze_index(0.6, 44.0, 20.0, 5.0) < crust     # partial heal
    # None inputs are benign, not bad
    assert refreeze_index(None, None, None, None) == 0.0
    # apply_refreeze scales conditions down, floored at (1 - max_penalty)
    assert apply_refreeze(80.0, 0.0) == 80.0
    assert apply_refreeze(80.0, 1.0) == pytest_approx(80.0 * 0.6, 0.001)
    assert apply_refreeze(None, 1.0) is None


def test_overall_score_weighting_and_neutral_forecast():
    from ski.score import overall_score

    subs = {"season": 20.0, "in_season": 40.0, "forecast": 50.0, "conditions": 90.0}
    weekend = overall_score(subs, "weekend")
    season = overall_score(subs, "season")
    assert weekend.value > season.value          # conditions-weighted vs season-weighted
    # weekend: power-mean over the non-forecast subs (conditions40/in_season20/
    # season5, p=0.5); the NEUTRAL forecast adds exactly 0 on top
    exp = ((40 * 90 ** 0.5 + 20 * 40 ** 0.5 + 5 * 20 ** 0.5) / 65) ** 2
    assert weekend.value == pytest_approx(exp, 0.05)


def test_neutral_forecast_contributes_exactly_zero():
    from ski.score import overall_score
    core = {"season": 80.0, "in_season": 70.0, "conditions": 90.0}
    without = overall_score({**core, "forecast": None}, "weekend")
    with_neutral = overall_score({**core, "forecast": 50.0}, "weekend")
    # a neutral forecast no longer drags the power mean down
    assert with_neutral.value == pytest_approx(without.value, 0.001)
    # ...while snow boosts and thaw penalizes, symmetrically around neutral
    boosted = overall_score({**core, "forecast": 90.0}, "weekend")
    dragged = overall_score({**core, "forecast": 10.0}, "weekend")
    assert boosted.value > without.value > dragged.value
    assert (boosted.value - without.value) == pytest_approx(
        without.value - dragged.value, 0.001)


def test_overall_skips_missing_subscores():
    from ski.score import overall_score
    subs = {"season": None, "in_season": None, "forecast": 50.0, "conditions": 80.0}
    o = overall_score(subs, "weekend")
    # only conditions in the core mean; the neutral forecast delta is 0
    assert o.value == pytest_approx(80.0, 0.05)


def test_power_mean_is_stricter_at_the_bottom_only():
    from ski.score import overall_score
    bad = {"season": 27.8, "in_season": 2.8, "forecast": None, "conditions": 4.3}
    good = {"season": 88.9, "in_season": 50.0, "forecast": None, "conditions": 73.9}
    # strict (p=0.5) pulls the bad case well below the plain average...
    assert overall_score(bad, "season", exponent=0.5).value < \
        overall_score(bad, "season", exponent=1.0).value - 2
    # ...but barely moves the good case
    assert abs(overall_score(good, "season", exponent=0.5).value -
               overall_score(good, "season", exponent=1.0).value) < 2


def test_dynamic_weights_shift_with_season_progress():
    from ski.score import dynamic_weights
    early = dynamic_weights(0.0)
    late = dynamic_weights(1.0)
    assert early["season"] > late["season"]          # early leans on the season
    assert late["conditions"] > early["conditions"]  # late leans on conditions
    mid = dynamic_weights(0.5)
    assert late["conditions"] > mid["conditions"] > early["conditions"]


def test_season_progress_both_hemispheres():
    from ski.watercalendar import season_progress
    import datetime as _dt
    # Northern Hemisphere Dec 1 -> Apr 20 (wraps the New Year)
    n_start, n_end = (12, 1), (4, 20)
    assert season_progress(_dt.date(2026, 7, 1), n_start, n_end) == 0.0   # off-season
    assert season_progress(_dt.date(2026, 12, 31), n_start, n_end) > 0.0  # early winter
    mid = season_progress(_dt.date(2026, 2, 1), n_start, n_end)
    assert 0.0 < mid < 1.0                                                # midwinter
    assert season_progress(_dt.date(2026, 4, 10), n_start, n_end) > 0.8   # near close
    # Southern Hemisphere Jun 15 -> Oct 10 (no wrap) -- same code, no changes
    s_start, s_end = (6, 15), (10, 10)
    assert 0.0 < season_progress(_dt.date(2026, 8, 1), s_start, s_end) < 1.0  # their midwinter
    assert season_progress(_dt.date(2026, 1, 1), s_start, s_end) == 0.0   # their summer


# tiny approx helper so we don't force a pytest import at module load
def pytest_approx(value, tol):
    class _A:
        def __eq__(self, other):
            return abs(other - value) <= tol
    return _A()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
