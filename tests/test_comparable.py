"""Tests for ski.comparable -- the Global/Regional Comparable Score.

No network, no DB. Pure functions over synthetic rows, same fixture shape
service.score_mountain produces (key, region, in_season, abs_*_in).
"""

from __future__ import annotations

from ski.comparable import (attach_global_score, attach_regional_score,
                            score_population)


def _row(key, region, in_season=True, base=None, fresh=None, season=None,
         forecast=None, quality=None):
    return {
        "key": key, "region": region, "in_season": in_season,
        "abs_base_in": base, "abs_fresh_in": fresh,
        "abs_season_in": season, "abs_forecast_in": forecast,
        "abs_quality": quality,
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


# --- Phase 4: SnowQuality as a leaderboard component -------------------------
def test_quality_breaks_ties_on_equal_quantity():
    """Two mountains identical on every inches input -> the one with better snow
    quality now ranks higher. This is the whole point of Phase 4: the leaderboard
    is no longer pure quantity."""
    rows = [
        _row("dry", "Utah", base=50, fresh=12, season=100, forecast=5, quality=90),
        _row("hammered", "Utah", base=50, fresh=12, season=100, forecast=5, quality=30),
    ]
    scores = score_population(rows)
    assert scores["dry"] > scores["hammered"]


def test_quality_lifts_score_but_stays_moderate():
    """At the current MODERATE weight, better quality measurably lifts a mountain's
    rank, but does NOT yet override a clear quantity lead -- the deliberate staged
    setting (user decision, 2026-07-17: start moderate, ramp to strong only after
    validating the reorderings). Same roster + mountain, better quality -> higher."""
    def pop(q):
        return [
            _row("subject", "Utah", base=50, fresh=11, season=100, forecast=4, quality=q),
            _row("rival", "Utah", base=52, fresh=13, season=105, forecast=6, quality=20),
        ]
    hi = score_population(pop(95))["subject"]
    lo = score_population(pop(20))["subject"]
    assert hi > lo, (hi, lo)   # quality has a real, positive effect
    # ...but at moderate weight it can't yet flip a mountain behind on all 4 inches
    assert score_population(pop(95))["subject"] < score_population(pop(95))["rival"]


def test_quality_missing_drops_out_not_zeroed():
    """A mountain with no quality read (no recent storm / offline) still ranks on
    its inches, rather than being dragged toward zero for the missing component."""
    rows = [
        _row("no_quality", "Utah", base=60, fresh=8, season=150, forecast=3, quality=None),
        _row("has_quality", "Utah", base=10, fresh=1, season=20, forecast=1, quality=80),
    ]
    scores = score_population(rows)
    assert scores["no_quality"] is not None and scores["has_quality"] is not None
    # deep-but-unrated still beats thin-but-rated on the strength of its inches
    assert scores["no_quality"] > scores["has_quality"]


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
