"""Audit Part 1 fixes: data-age surfacing, the stale-cover cap safety net, and
the per-mountain base-offset (valley-station under-read) mechanism.

These target the two silent-failure classes the grading audit flagged:
  - a station that has gone entirely SILENT still riding a season percentile
    (guarded by score.apply_stale_cap + pipeline.observation_age_days), and
  - a valley COOP station's under-read base tripping the absolute gates
    (corrected opt-in by settled_cover_depth(base_offset_in=...)).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from config import STALE_UNKNOWN_COVER_CAP
from ski.pipeline import observation_age_days, settled_cover_depth
from ski.score import apply_stale_cap


def _obs(rows: list[tuple[str, float | None, float | None, float | None]]) -> pd.DataFrame:
    """(date, snow_depth_in, swe_in, new_snow_24hr) -> canonical obs frame."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows]),
            "snow_depth_inches": [r[1] for r in rows],
            "swe_inches": [r[2] for r in rows],
            "new_snow_24hr": [r[3] for r in rows],
        }
    )


# --- observation_age_days --------------------------------------------------
def test_age_counts_from_last_usable_row():
    obs = _obs([("2026-01-01", 40.0, None, 0.0), ("2026-01-10", 42.0, None, 2.0)])
    assert observation_age_days(obs, date(2026, 1, 15)) == 5


def test_age_ignores_all_null_rows():
    # A row that exists but carries no usable field must not count as "reported".
    obs = _obs([("2026-01-10", 42.0, None, 2.0), ("2026-01-20", None, None, None)])
    assert observation_age_days(obs, date(2026, 1, 25)) == 15


def test_age_none_when_never_reported():
    assert observation_age_days(_obs([]), date(2026, 1, 15)) is None
    obs = _obs([("2026-01-20", 40.0, None, 0.0)])  # only rows AFTER as_of
    assert observation_age_days(obs, date(2026, 1, 15)) is None


# --- apply_stale_cap -------------------------------------------------------
def test_stale_cap_bites_only_when_stale_and_cover_unknown():
    # stale + no cover reading -> capped
    assert apply_stale_cap(80.0, stale=True, cover_known=False) == STALE_UNKNOWN_COVER_CAP


def test_stale_cap_inert_when_cover_known():
    # we know the base -> the season percentile isn't the only evidence
    assert apply_stale_cap(80.0, stale=True, cover_known=True) == 80.0


def test_stale_cap_inert_when_fresh():
    assert apply_stale_cap(80.0, stale=False, cover_known=False) == 80.0


def test_stale_cap_is_min_not_assignment():
    # an already-lower score keeps its value and still sorts
    assert apply_stale_cap(9.0, stale=True, cover_known=False) == 9.0


def test_stale_cap_passes_none_through():
    assert apply_stale_cap(None, stale=True, cover_known=False) is None


# --- base_offset_in --------------------------------------------------------
def test_offset_lifts_a_positive_measured_depth():
    obs = _obs([("2026-01-14", 3.0, None, 0.0)])  # valley station reads 3"
    plain = settled_cover_depth(obs, date(2026, 1, 15))
    lifted = settled_cover_depth(obs, date(2026, 1, 15), base_offset_in=10.0)
    assert plain == 3.0
    assert lifted == 13.0  # now clears the 6" in-season gate


def test_offset_never_manufactures_summer_cover():
    # A station reading 0" is genuinely bare; the offset must NOT lift it.
    obs = _obs([("2026-07-05", 0.0, None, 0.0)])
    assert settled_cover_depth(obs, date(2026, 7, 6), base_offset_in=10.0) == 0.0


def test_offset_default_is_inert():
    obs = _obs([("2026-01-14", 30.0, None, 0.0)])
    assert settled_cover_depth(obs, date(2026, 1, 15)) == \
        settled_cover_depth(obs, date(2026, 1, 15), base_offset_in=0.0)


def test_offset_applies_to_swe_proxy():
    # no depth sensor, SWE 10" -> 30" settled (x3), +5 offset -> 35"
    obs = _obs([("2026-01-14", None, 10.0, 0.0)])
    assert settled_cover_depth(obs, date(2026, 1, 15), base_offset_in=5.0) == 35.0
