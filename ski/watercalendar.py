"""Water-year calendar helpers.

Snow accumulates over a *water year*, not a calendar year. By NRCS convention a
water year starts Oct 1 and is named for the year it ends in -- water year 2025
runs Oct 1 2024 -> Sep 30 2025.

Grading compares "the same day of the season across all historical years", so we
need a stable day-of-water-year index (1 on Oct 1) to line years up.
"""

from __future__ import annotations

from datetime import date, timedelta

WATER_YEAR_START_MONTH = 10  # October
WATER_YEAR_START_DAY = 1


def water_year(d: date, start_month: int = WATER_YEAR_START_MONTH) -> int:
    """Water year that date `d` belongs to (named for the ending calendar year).

    `start_month` defaults to 10 (Oct 1, Northern Hemisphere); pass ~5 for the
    Southern Hemisphere so a Jun-Oct season sits inside one water year."""
    if d.month >= start_month:
        return d.year + 1
    return d.year


def water_year_start(wy: int, start_month: int = WATER_YEAR_START_MONTH) -> date:
    """First day of the given water year (start_month/1 of the prior year)."""
    return date(wy - 1, start_month, WATER_YEAR_START_DAY)


def day_of_water_year(d: date, start_month: int = WATER_YEAR_START_MONTH) -> int:
    """1-based day index within the water year (the start day -> 1).

    Note this can be 366 in a water year containing a Feb 29; that's fine, it
    still lines dates up consistently year to year.
    """
    return (d - water_year_start(water_year(d, start_month), start_month)).days + 1


def date_from_day_of_water_year(wy: int, dowy: int,
                                start_month: int = WATER_YEAR_START_MONTH) -> date:
    """Inverse of `day_of_water_year` for a specific water year."""
    return water_year_start(wy, start_month) + timedelta(days=dowy - 1)


def month_day_to_dowy(month: int, day: int,
                      start_month: int = WATER_YEAR_START_MONTH) -> int:
    """Day-of-water-year for a calendar (month, day), independent of year.

    Uses a fixed non-leap reference year so Feb 29 never shifts the index. With
    the default Oct start: (10, 1) -> 1, (12, 1) -> 62, (4, 20) -> 202.
    """
    _REF = 2023  # non-leap
    return day_of_water_year(date(_REF, month, day), start_month)


# ---------------------------------------------------------------------------
# Ski-season progress (hemisphere/calendar agnostic)
# ---------------------------------------------------------------------------
def _season_bounds(as_of: date, start_md: tuple[int, int], end_md: tuple[int, int]):
    """Concrete (start_date, end_date) of the season cycle relevant to `as_of`.

    Works whether the season stays within one calendar year (Southern Hemisphere,
    e.g. Jun->Oct) or wraps the New Year (Northern Hemisphere, e.g. Dec->Apr).
    """
    wraps = start_md > end_md  # e.g. (12,1) > (4,20) -> spans year boundary
    y = as_of.year
    if wraps:
        end_prev = date(y, *end_md)
        if as_of <= end_prev:
            return date(y - 1, *start_md), end_prev
        return date(y, *start_md), date(y + 1, *end_md)
    return date(y, *start_md), date(y, *end_md)


def season_progress(as_of: date, start_md: tuple[int, int], end_md: tuple[int, int]) -> float:
    """Fraction of the core season elapsed as of `as_of`, clamped to [0, 1].

    0.0 before/at the season start (preseason), 1.0 at/after the end (spring/
    off-season). Independent of hemisphere -- driven only by the given month/day
    window, so it's correct for any mountain's calendar.
    """
    start, end = _season_bounds(as_of, start_md, end_md)
    if as_of <= start:
        return 0.0
    if as_of >= end:
        return 1.0
    return (as_of - start).days / (end - start).days
