"""Tests for the AI grade commentary (ski/commentary.py).

No network, no API key, no pytest. Run standalone:  python tests/test_commentary.py

The invariants under test: the off-season gate (no prose without positive
evidence of cover), the per-(mountain, day) cache including grade-change
invalidation, and graceful None when generation isn't possible -- a build
without credentials must not fail and must not cache an absence.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski import commentary  # noqa: E402


def _card(in_season=True, score=55.0, grade="B+"):
    return {
        "mountain": {"name": "Alta, UT"},
        "as_of": "2026-02-15",
        "default_profile": "dynamic",
        "in_season": in_season,
        "season_progress": 0.55,
        # commentary explains the ABSOLUTE skiability grade, not the
        # self-relative `overall` -- keep both present, like a real card, but
        # they intentionally differ so a test asserting on one can't pass by
        # accident against the other.
        "skiability": {"score": score, "grade": grade},
        "overall": {"dynamic": {"score": 40.0, "grade": "C-"}},
        "grades": {
            "season": {"percentile": 82},
            "in_season": {"percentile": 74},
            "base": {"grade": "A-"},
        },
        "conditions": {"fresh_7d": 18.0, "base_depth": 92},
        "forecast": {"inches": 6.0, "window_hours": 72, "alert": False},
    }


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def test_facts_use_only_card_numbers():
    facts = commentary.facts_from_card(_card())
    assert facts["grade"] == "B+"
    assert facts["fresh_snow_last_7_days_inches"] == 18.0
    assert facts["season_to_date_percentile_vs_history"] == 82
    assert facts["incoming_snow_inches"] == 6.0


def test_off_season_and_unscored_are_gated():
    assert commentary.facts_from_card(_card(in_season=False)) is None
    assert commentary.facts_from_card(_card(in_season=None)) is None
    assert commentary.facts_from_card(_card(score=None)) is None


def _force_ai_mode():
    """The cache tests below exercise the AI path specifically; select it
    regardless of the configured default (which is "rules"). Returns the previous
    value so the caller can restore it."""
    prev = commentary.COMMENTARY_MODE
    commentary.COMMENTARY_MODE = "ai"
    return prev


def test_cache_round_trip_one_generation_per_day():
    db = _tmp_db()
    calls = []
    real = commentary.generate
    mode = _force_ai_mode()
    commentary.generate = lambda facts: calls.append(1) or "Fresh and deep."
    try:
        d = date(2026, 2, 15)
        t1 = commentary.get_or_generate("alta", d, _card(), db_path=db)
        t2 = commentary.get_or_generate("alta", d, _card(), db_path=db)
        assert t1 == t2 == "Fresh and deep."
        assert len(calls) == 1, "second call must come from the cache"
    finally:
        commentary.generate = real
        commentary.COMMENTARY_MODE = mode
        os.unlink(db)


def test_grade_change_invalidates_cached_prose():
    """Live rescoring can move the letter within a day; stale prose explaining
    the old grade must not survive it."""
    db = _tmp_db()
    real = commentary.generate
    mode = _force_ai_mode()
    outputs = iter(["Was a B+.", "Now an A-."])
    commentary.generate = lambda facts: next(outputs)
    try:
        d = date(2026, 2, 15)
        assert commentary.get_or_generate("alta", d, _card(grade="B+"), db_path=db) == "Was a B+."
        assert commentary.get_or_generate("alta", d, _card(grade="A-"), db_path=db) == "Now an A-."
    finally:
        commentary.generate = real
        commentary.COMMENTARY_MODE = mode
        os.unlink(db)


def test_generation_failure_yields_none_and_caches_nothing():
    db = _tmp_db()
    real = commentary.generate
    mode = _force_ai_mode()
    commentary.generate = lambda facts: None
    try:
        d = date(2026, 2, 15)
        assert commentary.get_or_generate("alta", d, _card(), db_path=db) is None
        # A later run WITH credentials must be able to fill the gap.
        commentary.generate = lambda facts: "Filled in later."
        assert commentary.get_or_generate("alta", d, _card(), db_path=db) == "Filled in later."
    finally:
        commentary.generate = real
        commentary.COMMENTARY_MODE = mode
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
