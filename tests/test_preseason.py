"""Tests for the pre-season grade (score.preseason_score + the calendar window).

No network. Run standalone:  python tests/test_preseason.py
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from build_snapshot import _row_from_card  # noqa: E402
from config import GLOBAL_SCORE_WEIGHTS  # noqa: E402
from ski.comparable import score_population  # noqa: E402
from ski.score import in_preseason_window, preseason_score  # noqa: E402
from ski.watercalendar import days_from_season_start, month_day_label  # noqa: E402


def test_before_the_season_start_is_not_open_however_deep_the_snow():
    assert in_preseason_window(-27, True) is True
    assert in_preseason_window(-27, False) is True


def test_too_far_before_the_season_start_is_not_preseason():
    assert in_preseason_window(-90, False) is False


def test_after_the_start_only_an_unskiable_mountain_stays_preseason():
    assert in_preseason_window(10, True) is False
    assert in_preseason_window(10, False) is True
    assert in_preseason_window(45, False) is False


def test_preseason_mountain_is_left_out_of_the_live_ranking():
    def row(key, base, pre):
        return {"key": key, "in_season": True, "abs_base_in": base, "preseason_grade": pre}
    scores = score_population([row("open_a", 40.0, None), row("open_b", 20.0, None),
                               row("not_open", 90.0, "A")], GLOBAL_SCORE_WEIGHTS)
    assert scores["not_open"] is None
    assert scores["open_a"] > scores["open_b"]


def test_month_day_label():
    assert month_day_label((12, 1)) == "Dec 1"


def test_dusting_gets_no_grade():
    assert preseason_score(1.0, 0.5, 0.0) is None


def test_unknown_base_gets_no_grade_even_after_a_storm():
    assert preseason_score(None, 12.0, 0.0) is None


def test_fresh_snow_alone_can_clear_the_minimum():
    assert preseason_score(1.2, 2.3, 0.0) is not None


def test_more_snow_grades_higher():
    thin = preseason_score(4.0, 1.0, 0.0)
    deep = preseason_score(28.0, 20.0, 0.0)
    assert deep.value > thin.value
    assert thin.grade == "C-"
    assert deep.grade == "A"


def test_forecast_adds_to_the_grade():
    assert preseason_score(6.0, 0.0, 12.0).value > preseason_score(6.0, 0.0, 0.0).value


def test_unknown_fresh_and_forecast_drop_out_instead_of_counting_as_zero():
    # 12" of base is 58 on the base curve; with nothing else known, that's the score.
    assert round(preseason_score(12.0, None, None).value, 6) == 58.0
    assert preseason_score(12.0, 0.0, 0.0).value < 58.0


def test_days_from_season_start_northern_hemisphere():
    assert days_from_season_start(date(2026, 11, 4), (12, 1)) == -27
    assert days_from_season_start(date(2026, 12, 11), (12, 1)) == 10


def test_days_from_season_start_crosses_new_year():
    assert days_from_season_start(date(2027, 1, 5), (12, 1)) == 35


def test_days_from_season_start_southern_hemisphere_in_november_is_far_away():
    assert abs(days_from_season_start(date(2026, 11, 4), (6, 15))) > 90


def _card(preseason):
    return {
        "overall": {"dynamic": {"score": 5.0, "grade": "F"}},
        "skiability": {"score": 5.0, "grade": "F"},
        "grades": {"season": None, "base": None},
        "in_season": False,
        "cover_depth": 4.0,
        "conditions": {"base_depth": 4.0, "fresh_7d": 1.0},
        "season_progress": 0.0,
        "forecast": None,
        "comparable_inputs": {},
        "preseason": preseason,
    }


def test_row_carries_the_preseason_grade():
    row = _row_from_card("alta", _card({"score": 21.0, "grade": "C-"}), "dynamic")
    assert row["preseason_score"] == 21.0
    assert row["preseason_grade"] == "C-"


def test_row_without_a_preseason_grade_is_null():
    row = _row_from_card("alta", _card(None), "dynamic")
    assert row["preseason_score"] is None
    assert row["preseason_grade"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
