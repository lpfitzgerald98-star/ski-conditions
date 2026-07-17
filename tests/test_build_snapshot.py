"""Tests for scripts/build_snapshot.py's row builder.

No network, no DB, no pytest required: python tests/test_build_snapshot.py

`_row_from_card`'s docstring says it "mirrors service.score_mountain's row
exactly" -- that was untrue in practice: service.score_mountain picked up the
comparable-score fields (abs_base_in / abs_fresh_in / abs_season_in /
abs_forecast_in) when ski.comparable shipped, but this script's own
row-builder was never updated, so global_score/regional_score were silently
None on every row of the deployed STATIC site (the live API path was fine;
only the file this script writes -- what GitHub Pages actually serves -- was
broken). This test encodes the fix as an invariant so the two row-builders
can't drift apart again without a visible failure.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from build_snapshot import _row_from_card  # noqa: E402


def _synthetic_card(comparable_inputs=None):
    """A minimal scorecard()-shaped dict -- only the fields _row_from_card
    actually reads, matching ski/card.py's real output shape."""
    return {
        "overall": {"dynamic": {"score": 62.0, "grade": "B"}},
        "skiability": {"score": 71.0, "grade": "A-"},
        "in_season": True,
        "cover_depth": 40.0,
        "grades": {
            "season": {"grade": "B+"},
            "base": {"grade": "B-"},
        },
        "conditions": {"base_depth": 40.0, "fresh_7d": 5.0},
        "season_progress": 0.5,
        "forecast": {"inches": 6.0, "alert": False},
        "comparable_inputs": comparable_inputs or {},
    }


def test_row_carries_comparable_score_inputs():
    card = _synthetic_card({"base_in": 40.0, "fresh_in": 3.0,
                            "season_in": 120.0, "forecast_in": 6.0})
    row = _row_from_card("alta", card, "dynamic")
    assert row["abs_base_in"] == 40.0
    assert row["abs_fresh_in"] == 3.0
    assert row["abs_season_in"] == 120.0
    assert row["abs_forecast_in"] == 6.0


def test_missing_comparable_inputs_yields_none_not_a_crash():
    card = _synthetic_card(comparable_inputs={})
    row = _row_from_card("alta", card, "dynamic")
    assert row["abs_base_in"] is None
    assert row["abs_fresh_in"] is None
    assert row["abs_season_in"] is None
    assert row["abs_forecast_in"] is None


def test_row_still_carries_the_original_fields():
    """The fix must be additive -- nothing about the existing row shape moved."""
    card = _synthetic_card()
    row = _row_from_card("alta", card, "dynamic")
    assert row["in_season"] is True
    assert row["status"] == "live"


def test_row_headline_is_skiability_not_relative_overall():
    """The pin/leaderboard headline (`score`/`grade`) is absolute skiability --
    the honest "ski right now" grade -- with the self-relative `overall` kept
    alongside as `overall_score`/`overall_grade` historical context, never
    dropped (see ski/card.py's skiability field, config.SKIABILITY_GRADE_THRESHOLDS)."""
    card = _synthetic_card()
    row = _row_from_card("alta", card, "dynamic")
    assert row["score"] == 71.0
    assert row["grade"] == "A-"
    assert row["overall_score"] == 62.0
    assert row["overall_grade"] == "B"


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
