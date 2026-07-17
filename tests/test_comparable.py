"""Tests for ski.comparable -- the Global/Regional Comparable Score.

No network, no DB. Pure functions over synthetic rows, same fixture shape
service.score_mountain produces (key, region, in_season, abs_*_in).
"""

from __future__ import annotations

from ski.comparable import (attach_global_score, attach_regional_score,
                            score_population)


def _row(key, region, in_season=True, base=None, fresh=None, season=None, forecast=None):
    return {
        "key": key, "region": region, "in_season": in_season,
        "abs_base_in": base, "abs_fresh_in": fresh,
        "abs_season_in": season, "abs_forecast_in": forecast,
    }


# --- score_population --------------------------------------------------------
def test_higher_absolute_inputs_score_higher():
    rows = [
        _row("deep", "Utah", base=80, fresh=12, season=200, forecast=10),
        _row("thin", "Utah", base=5, fresh=0, season=20, forecast=0),
    ]
    scores = score_population(rows)
    assert scores["deep"] > scores["thin"]
    assert scores["thin"] == 0.0     # beats 0 of 1 other on every component
    assert scores["deep"] == 100.0


def test_off_season_never_ranks_or_pollutes_the_pool():
    rows = [
        _row("alive", "Utah", in_season=True, base=40, fresh=5, season=100, forecast=2),
        _row("dead", "Utah", in_season=False, base=100, fresh=100, season=100, forecast=100),
    ]
    scores = score_population(rows)
    assert scores["dead"] is None
    # 'alive' has no in-season peer, so it ranks against an empty cohort -> None,
    # NOT against the off-season mountain's inflated numbers.
    assert scores["alive"] is None


def test_unknown_in_season_also_excluded():
    rows = [
        _row("a", "Northeast", in_season=None, base=50, fresh=5, season=80, forecast=1),
        _row("b", "Northeast", in_season=True, base=40, fresh=4, season=70, forecast=1),
    ]
    scores = score_population(rows)
    assert scores["a"] is None


def test_missing_component_excluded_not_zeroed():
    # 'a' has no forecast input at all (e.g. offline scoring); it should still
    # score from its other three components, not get dragged toward 0.
    rows = [
        _row("a", "Utah", base=60, fresh=8, season=150, forecast=None),
        _row("b", "Utah", base=10, fresh=1, season=20, forecast=5),
    ]
    scores = score_population(rows)
    assert scores["a"] is not None
    assert scores["a"] > scores["b"]


def test_never_ranks_against_itself():
    # Two mountains tied on everything: each beats 0 of the 1 other -> 0th,
    # the same "others, not self" convention service.rank_against pins.
    rows = [
        _row("a", "Utah", base=50, fresh=5, season=100, forecast=2),
        _row("b", "Utah", base=50, fresh=5, season=100, forecast=2),
    ]
    scores = score_population(rows)
    assert scores["a"] == 0.0
    assert scores["b"] == 0.0


def test_lone_in_season_mountain_gets_none():
    rows = [_row("solo", "Alaska", base=50, fresh=5, season=100, forecast=2)]
    assert score_population(rows)["solo"] is None


# --- attach_global_score / attach_regional_score -----------------------------
def test_global_score_ranks_across_the_whole_roster():
    rows = [
        _row("utah_deep", "Utah", base=80, fresh=12, season=200, forecast=10),
        _row("utah_thin", "Utah", base=5, fresh=0, season=20, forecast=0),
        _row("vt_ok", "Northeast", base=20, fresh=3, season=60, forecast=1),
    ]
    attach_global_score(rows)
    by = {r["key"]: r for r in rows}
    assert by["utah_deep"]["global_score"] == 100.0
    # vt_ok beats only utah_thin (1 of 2) globally
    assert by["vt_ok"]["global_score"] == 50.0


def test_regional_score_is_scoped_per_region_unlike_global():
    """A Vermont resort having a great regional week can rank well regionally
    while sitting far behind globally against a Utah powder day -- the whole
    point of having both scores instead of one."""
    rows = [
        _row("utah_epic", "Utah", base=90, fresh=20, season=300, forecast=15),
        _row("vt_best", "Northeast", base=25, fresh=6, season=70, forecast=2),
        _row("vt_worst", "Northeast", base=10, fresh=1, season=30, forecast=0),
    ]
    attach_global_score(rows)
    attach_regional_score(rows)
    by = {r["key"]: r for r in rows}
    # vt_best tops its own region...
    assert by["vt_best"]["regional_score"] == 100.0
    # ...but trails utah_epic globally (only 1 of 2 in-season peers beaten).
    assert by["vt_best"]["global_score"] < by["utah_epic"]["global_score"]


def test_attach_functions_mutate_in_place_and_round():
    rows = [
        _row("a", "Utah", base=80, fresh=12, season=200, forecast=10),
        _row("b", "Utah", base=5, fresh=0, season=20, forecast=0),
    ]
    out = attach_global_score(rows)
    assert out is rows
    assert isinstance(rows[0]["global_score"], float)


def test_rows_missing_abs_fields_entirely_score_none():
    # e.g. the test_service.py fixtures, which don't carry abs_* at all.
    rows = [{"key": "a", "region": "Utah", "in_season": True, "score": 90.0},
           {"key": "b", "region": "Utah", "in_season": True, "score": 10.0}]
    attach_global_score(rows)
    attach_regional_score(rows)
    assert rows[0]["global_score"] is None
    assert rows[0]["regional_score"] is None
