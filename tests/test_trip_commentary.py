"""Tests for ski.trip_commentary -- the Trip Predictor's Part 1 (seasonal
pattern) commentary. Pure functions over synthetic climatology dicts, no DB.
"""

from __future__ import annotations

from datetime import date

from ski import trip_commentary as tc
from ski.trip import target_dowy


def _flat_climatology(base: float, fresh: float, season: float,
                      n_years: int = 30) -> dict[int, dict]:
    """A climatology where every day of the year reads identically -- useful
    for isolating the season-STAGE language from the base-TREND language."""
    return {d: {"base_in": base, "fresh_in": fresh, "season_in": season,
               "n_years": n_years} for d in range(1, 367)}


def _rising_climatology(n_years: int = 30) -> dict[int, dict]:
    """Base climbs steadily from day 1 to day 366 -- always in season, always
    a clear rising trend at any point far enough from the edges."""
    return {d: {"base_in": 10.0 + d * 0.5, "fresh_in": 5.0,
               "season_in": 20.0 + d * 0.3, "n_years": n_years}
            for d in range(1, 367)}


SEASON_WINDOW = {"start": (12, 1), "end": (4, 20)}  # Northern Hemisphere, like Alta


# --- off-season gate ---------------------------------------------------------
def test_off_season_date_is_never_called_early_season():
    # Flat, sub-gate climatology everywhere (base < 6, fresh < 3) -> genuinely
    # off-season at every dowy, including the "start" of season_window itself,
    # which season_progress alone would read as 0.0 ("early season").
    clim = _flat_climatology(base=1.0, fresh=0.5, season=2.0)
    text = tc.seasonal_pattern_text("x", "Testmountain, XX", 10, SEASON_WINDOW,
                                    clim, date(2026, 12, 1))
    assert "outside" in text.lower() or "season" in text.lower()
    assert "early" not in text.lower()


def test_in_season_date_is_not_flagged_off_season():
    clim = _rising_climatology()
    text = tc.seasonal_pattern_text("x", "Testmountain, XX", 10, SEASON_WINDOW,
                                    clim, date(2026, 2, 15))
    assert "outside" not in text.lower()


# --- possessive grammar -------------------------------------------------------
def test_possessive_uses_short_name_not_full_display_name():
    clim = _rising_climatology()
    # Run many dates so we hit a possessive template regardless of which the
    # seeded RNG happens to pick.
    hit_possessive = False
    for day in range(1, 29):
        text = tc.seasonal_pattern_text("wb", "Whistler Blackcomb, BC", 10,
                                        SEASON_WINDOW, clim, date(2026, 1, day))
        if "'s" in text:
            hit_possessive = True
            assert "BC's" not in text
            assert "Whistler Blackcomb's" in text
    assert hit_possessive, "no possessive template was ever selected across 28 samples"


# --- trend detection -----------------------------------------------------
def test_rising_trend_is_named():
    clim = _rising_climatology()
    dowy = target_dowy(date(2026, 2, 1), 10)
    trend = tc._base_trend(clim, dowy)
    assert trend == "rising"


def test_flat_climatology_reads_as_holding():
    clim = _flat_climatology(base=40.0, fresh=5.0, season=60.0)
    dowy = target_dowy(date(2026, 2, 1), 10)
    assert tc._base_trend(clim, dowy) == "holding"


def test_declining_trend_is_named():
    # Base falls steadily -- the mirror of the rising fixture.
    clim = {d: {"base_in": 100.0 - d * 0.5, "fresh_in": 5.0,
               "season_in": 60.0, "n_years": 30} for d in range(1, 367)}
    dowy = target_dowy(date(2026, 2, 1), 10)
    assert tc._base_trend(clim, dowy) == "declining"


def test_trend_wraps_across_the_year_boundary():
    # A target right at day 1 must still look "backward" past day 366, not
    # crash or silently skip the trend.
    clim = _rising_climatology()
    trend = tc._base_trend(clim, 1)
    assert trend in ("rising", "holding", "declining")  # doesn't raise, has an answer


# --- fallback for no / sparse history --------------------------------------
def test_no_history_at_all_gives_an_honest_fallback():
    text = tc.seasonal_pattern_text("x", "Nodata Mountain, XX", 10, SEASON_WINDOW,
                                    {}, date(2026, 1, 15))
    assert "nodata mountain" in text.lower()
    assert "historical" in text.lower() or "baseline" in text.lower()


def test_low_confidence_years_are_flagged_in_prose():
    clim = _rising_climatology(n_years=3)
    text = tc.seasonal_pattern_text("x", "Thinrecord, XX", 10, SEASON_WINDOW,
                                    clim, date(2026, 2, 1), low_confidence_years=10)
    assert "shorter station record" in text or "handful of years" in text


def test_implausible_base_never_quoted():
    # A glacier-depth-style reading (>250in) must never appear as a number.
    clim = _flat_climatology(base=1312.0, fresh=5.0, season=200.0)
    text = tc.seasonal_pattern_text("x", "Glacier, XX", 10, SEASON_WINDOW,
                                    clim, date(2026, 2, 1))
    assert "1312" not in text


# --- determinism / variety ----------------------------------------------
def test_same_mountain_same_date_is_deterministic():
    clim = _rising_climatology()
    a = tc.seasonal_pattern_text("x", "Testmountain, XX", 10, SEASON_WINDOW, clim, date(2026, 2, 1))
    b = tc.seasonal_pattern_text("x", "Testmountain, XX", 10, SEASON_WINDOW, clim, date(2026, 2, 1))
    assert a == b


def test_different_mountains_same_date_can_differ():
    clim = _rising_climatology()
    outputs = {
        tc.seasonal_pattern_text(key, f"{key.title()}, XX", 10, SEASON_WINDOW, clim, date(2026, 2, 1))
        for key in ("alpha", "bravo", "charlie", "delta", "echo")
    }
    assert len(outputs) > 1  # not all five mountains produced identical prose


def test_missing_season_window_does_not_crash():
    clim = _rising_climatology()
    text = tc.seasonal_pattern_text("x", "Nowindow, XX", 10, None, clim, date(2026, 2, 1))
    assert isinstance(text, str) and text
