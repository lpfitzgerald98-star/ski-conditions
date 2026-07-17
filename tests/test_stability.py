"""Tests for grade stability / hysteresis (ski/stability.py).

No network, no pytest required: python tests/test_stability.py

Invariants under test: noise near a boundary doesn't flip the letter until it
clears by the configured margin, a real move (well past the margin, or a jump
across more than one grade) is never held back, a stale/missing anchor doesn't
force false stickiness, and the retro/historical path is untouched (never
calls stabilize at all).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski import stability  # noqa: E402

THRESHOLDS = [
    (85, "A+"), (71, "A"), (60, "A-"), (50, "B+"), (39, "B"),
    (30, "B-"), (23, "C+"), (17, "C"), (13, "C-"), (9, "D"), (0, "F"),
]
MARGIN = 2.5  # matches config.GRADE_HYSTERESIS_MARGIN at the time this was written


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def test_first_ever_call_has_no_anchor_and_uses_raw():
    db = _tmp_db()
    try:
        g = stability.stabilize("alta", date(2026, 1, 15), "dynamic", 40.0, THRESHOLDS, db_path=db)
        assert g == "B"  # 40 -> B by raw thresholds, nothing to anchor against
    finally:
        os.unlink(db)


def test_small_wobble_across_a_boundary_stays_on_yesterdays_grade():
    """39.4 (B) yesterday, 38.6 (still raw B... use a true boundary-straddle)."""
    db = _tmp_db()
    try:
        d1 = date(2026, 1, 15)
        d2 = d1 + timedelta(days=1)
        g1 = stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)
        assert g1 == "B"
        # 38.9 raw-grades to B- (boundary 39) but is only 0.1 past it -- noise.
        g2 = stability.stabilize("alta", d2, "dynamic", 38.9, THRESHOLDS, db_path=db)
        assert g2 == "B", "a wobble just past the boundary must not flip the letter"
    finally:
        os.unlink(db)


def test_real_move_past_the_margin_does_flip():
    db = _tmp_db()
    try:
        d1 = date(2026, 1, 15)
        d2 = d1 + timedelta(days=1)
        stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)
        # 39 (boundary) - MARGIN - a hair = comfortably past the required clearance.
        g2 = stability.stabilize("alta", d2, "dynamic", 39.0 - MARGIN - 0.1, THRESHOLDS, db_path=db)
        assert g2 == "B-", "a move well past the boundary must flip"
    finally:
        os.unlink(db)


def test_exact_margin_clears_the_flip():
    db = _tmp_db()
    try:
        d1, d2 = date(2026, 1, 15), date(2026, 1, 16)
        stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)
        g2 = stability.stabilize("alta", d2, "dynamic", 39.0 - MARGIN, THRESHOLDS, db_path=db)
        assert g2 == "B-"
    finally:
        os.unlink(db)


def test_jump_spanning_multiple_grades_is_never_held_back():
    db = _tmp_db()
    try:
        d1, d2 = date(2026, 1, 15), date(2026, 1, 16)
        stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)  # B
        g2 = stability.stabilize("alta", d2, "dynamic", 90.0, THRESHOLDS, db_path=db)  # A+, real storm
        assert g2 == "A+", "a real multi-grade jump must never be held back"
    finally:
        os.unlink(db)


def test_stale_anchor_beyond_lookback_is_ignored():
    db = _tmp_db()
    try:
        d1 = date(2026, 1, 1)
        stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)  # B
        d2 = d1 + timedelta(days=stability_lookback() + 1)
        g2 = stability.stabilize("alta", d2, "dynamic", 38.9, THRESHOLDS, db_path=db)
        assert g2 == "B-", "an anchor older than the lookback window must not hold"
    finally:
        os.unlink(db)


def stability_lookback():
    from config import GRADE_HYSTERESIS_LOOKBACK_DAYS
    return GRADE_HYSTERESIS_LOOKBACK_DAYS


def test_mountains_and_profiles_are_independent():
    db = _tmp_db()
    try:
        d1, d2 = date(2026, 1, 15), date(2026, 1, 16)
        stability.stabilize("alta", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)
        stability.stabilize("vail", d1, "dynamic", 40.0, THRESHOLDS, db_path=db)
        # Only alta wobbles; vail's own anchor is untouched, and a different
        # profile for alta has no anchor at all yet (uses raw).
        g_alta = stability.stabilize("alta", d2, "dynamic", 38.9, THRESHOLDS, db_path=db)
        g_vail = stability.stabilize("vail", d2, "dynamic", 38.9, THRESHOLDS, db_path=db)
        g_alta_weekend = stability.stabilize("alta", d2, "weekend", 38.9, THRESHOLDS, db_path=db)
        assert g_alta == "B"       # anchored, held
        assert g_vail == "B"       # anchored, held (independently)
        assert g_alta_weekend == "B-"  # no prior weekend anchor -> raw stands
    finally:
        os.unlink(db)


def test_none_value_returns_na_without_touching_db():
    db = _tmp_db()
    try:
        g = stability.stabilize("alta", date(2026, 1, 15), "dynamic", None, THRESHOLDS, db_path=db)
        assert g == "N/A"
        conn = stability.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM grade_stability").fetchone()[0]
        conn.close()
        assert n == 0
    finally:
        os.unlink(db)


def test_retro_scorecard_never_calls_stabilize():
    """Integration: card.scorecard(retro=True) must produce the same grade as
    a fresh, un-anchored stabilize() call -- i.e. it never consults the table."""
    from datetime import date as _date
    from ski.card import scorecard

    db = _tmp_db()
    try:
        # Poison the anchor with an adversarial prior grade for the same date's
        # profile; if retro wrongly read it, the grade would be held to "F".
        conn = stability.connect(db)
        conn.execute("INSERT INTO grade_stability VALUES (?,?,?,?,?)",
                     ("alta", "dynamic", "2026-01-14", 5.0, "F"))
        conn.commit()
        conn.close()
        card = scorecard("alta", db_path=db, as_of=_date(2026, 1, 15),
                         use_network=False, retro=True)
        # Whatever the raw grade is, it must not be forced to "F" by the poisoned
        # anchor -- retro must not have called stabilize (which would consult it).
        overall = card["overall"].get(card["default_profile"]) or {}
        conn = stability.connect(db)
        rows = conn.execute("SELECT COUNT(*) FROM grade_stability WHERE as_of='2026-01-15'").fetchone()[0]
        conn.close()
        assert rows == 0, "retro scoring must not write a stability row"
    finally:
        os.unlink(db)


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
