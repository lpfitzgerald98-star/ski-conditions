"""Grade stability: a hysteresis band so the OVERALL letter doesn't flap
between adjacent grades on day-to-day noise.

`letter_grade` (ski/grading.py) is a stateless threshold lookup -- 39.4 -> B,
38.6 -> B-. A mountain sitting near a boundary can cross it back and forth
across several days from measurement noise (an obs revision, a forecast pull
landing a point either side of the line) while the sub-scores barely move.
That's a worse user experience than the noise itself: a grade is supposed to
mean something stable enough to plan a trip around.

This module adds ONE PIECE OF DELIBERATE STATE -- yesterday's STABLE grade for
a (mountain, profile) -- and requires the raw value to clear the boundary by a
margin (config.GRADE_HYSTERESIS_MARGIN) before the visible letter actually
flips, a classic hysteresis / Schmitt-trigger band. This is NOT smoothing: the
numeric `score` on the card is always the fresh, honest value; only the LETTER
lags behind by design, and only across an ADJACENT pair of grades -- a jump big
enough to skip a grade entirely (a real storm, a station coming back online)
is never held back, because that's news, not noise.

Distinct from ski/cache.py's `cached_scores`: that table is a pure RENDER
cache and must never feed grading (a live SSE update and a cached snapshot
must be able to disagree without corrupting each other). This table's whole
purpose is the opposite -- to be read back into grading, on purpose, as the
anchor for hysteresis.

SCOPE: only the OVERALL letter (the headline grade on the map pin / card),
only for LIVE scoring. It deliberately does NOT touch:
  * season/month/base grades (ski/grading.letter_grade against GRADE_THRESHOLDS)
    or the within-region rank grade (ski/service.rank_against) -- both would
    need their own state design (region rank in particular depends on the
    whole day's cohort, not just one mountain's own history) and are out of
    scope for this pass.
  * `retro=True` historical scoring. Settled history is immutable by design
    (ski/card.py) -- retrofitting hysteresis onto already-built dates would mean
    rebuilding all of them. A historical date shows the letter that day's own
    numbers earned, full stop; `stabilize` is simply not called on that path.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from config import DB_PATH, GRADE_HYSTERESIS_LOOKBACK_DAYS, GRADE_HYSTERESIS_MARGIN
from ski.grading import letter_grade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grade_stability (
    mountain_key TEXT NOT NULL,
    profile      TEXT NOT NULL,
    as_of        TEXT NOT NULL,   -- ISO date this row's grade was resolved for
    value        REAL,            -- the raw overall value that day (diagnostic)
    grade        TEXT NOT NULL,   -- the STABLE grade actually shown that day
    PRIMARY KEY (mountain_key, profile, as_of)
);
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.execute(_SCHEMA)
    return conn


def _boundary(prev_grade: str, raw_grade: str, thresholds: list[tuple[float, str]]) -> float | None:
    """The threshold value separating `prev_grade` and `raw_grade` when they are
    ADJACENT entries in `thresholds` (best -> worst order), or None when they
    aren't -- a jump spanning more than one grade is real signal, not noise,
    and hysteresis should never hold it back."""
    order = [g for _, g in thresholds]
    try:
        i_prev, i_raw = order.index(prev_grade), order.index(raw_grade)
    except ValueError:
        return None  # a grade string outside this curve (shouldn't happen)
    if abs(i_prev - i_raw) != 1:
        return None
    return thresholds[min(i_prev, i_raw)][0]  # the better grade's own min-value


def stabilize(
    mountain_key: str,
    as_of: date,
    profile: str,
    value: float | None,
    thresholds: list[tuple[float, str]],
    db_path: str | Path | None = None,
) -> str:
    """The letter to actually show for `value`, hysteresis-adjusted against the
    most recent prior stable grade for this (mountain, profile).

    Always persists today's resolved grade (INSERT OR REPLACE, so a same-day
    re-render/regrade updates in place without perturbing tomorrow's anchor).
    A missing or stale (older than GRADE_HYSTERESIS_LOOKBACK_DAYS) anchor just
    means today's raw grade stands -- no false stickiness from a gap in the
    build history (a skipped Action run, a new mountain).
    """
    raw = letter_grade(value, thresholds)
    if value is None:
        return raw  # "N/A" -- nothing to anchor or persist

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT as_of, grade FROM grade_stability "
            "WHERE mountain_key=? AND profile=? AND as_of<? "
            "ORDER BY as_of DESC LIMIT 1",
            (mountain_key, profile, as_of.isoformat()),
        ).fetchone()

        stable = raw
        if row is not None:
            prev_date, prev_grade = date.fromisoformat(row[0]), row[1]
            fresh_enough = (as_of - prev_date) <= timedelta(days=GRADE_HYSTERESIS_LOOKBACK_DAYS)
            if fresh_enough and prev_grade != raw:
                boundary = _boundary(prev_grade, raw, thresholds)
                if boundary is not None:
                    order = [g for _, g in thresholds]
                    moving_up = order.index(raw) < order.index(prev_grade)
                    cleared = (value >= boundary + GRADE_HYSTERESIS_MARGIN if moving_up
                              else value <= boundary - GRADE_HYSTERESIS_MARGIN)
                    stable = raw if cleared else prev_grade
                # boundary is None (non-adjacent jump) -> stable stays `raw`

        conn.execute(
            "INSERT OR REPLACE INTO grade_stability VALUES (?, ?, ?, ?, ?)",
            (mountain_key, profile, as_of.isoformat(), value, stable),
        )
        conn.commit()
        return stable
    finally:
        conn.close()
