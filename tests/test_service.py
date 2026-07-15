"""Tests for the scoring service + render cache (ski/service.py, ski/cache.py).

No network, no pytest, no FastAPI. Run standalone:  python tests/test_service.py

The thing under test is the invariant that made #2 a bug: the overall score and
the within-region rank must come from one place, ride their own curves, and never
be recomputed by a caller.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import OFF_SEASON, OVERALL_GRADE_THRESHOLDS  # noqa: E402
from ski import cache  # noqa: E402
from ski.grading import letter_grade  # noqa: E402
from ski.pipeline import _carried_forward_cover  # noqa: E402
from ski.regions import country_code, country_of, region_of  # noqa: E402
from ski.score import apply_off_season_cap, is_in_season  # noqa: E402
from ski.service import (  # noqa: E402
    GRADE_COLORS,
    NA_COLOR,
    color_for,
    mountain_summary,
    rank_against,
    rank_within_regions,
)


def _row(key, region, score, in_season=True):
    """A rankable row. `in_season` defaults True: these fixtures exercise the
    ranking math, and only in-season mountains get ranked at all (the gate itself
    is covered separately below)."""
    return {"key": key, "name": f"{key} Mtn", "region": region, "score": score,
            "in_season": in_season}


# --- region taxonomy --------------------------------------------------------
def test_region_of_maps_state_codes():
    assert region_of("Alta, UT") == "Utah"
    assert region_of("Stowe, VT") == "Northeast"
    assert region_of("Whistler, BC") == "British Columbia"


def test_region_of_falls_back_without_swallowing():
    # An unmapped code becomes its own bucket, not someone else's.
    assert region_of("Somewhere, ZZ") == "ZZ"
    assert region_of("No Comma Here") == "Other"


def test_roster_regions_all_resolve():
    from config import MOUNTAINS
    for k in MOUNTAINS:
        assert mountain_summary(k)["region"] != "Other", f"{k} has no region"


def test_roster_countries_all_resolve():
    from config import MOUNTAINS
    for k in MOUNTAINS:
        s = mountain_summary(k)
        assert s["country"] != "Other", f"{k} has no country"
        assert len(s["country_code"]) == 3, f"{k} chip is {s['country_code']!r}"


def test_country_and_region_agree_on_the_same_code():
    assert country_of("Alta, UT") == "USA"
    assert country_of("Whistler, BC") == "Canada"
    assert country_of("Mt Hutt, NZ") == "New Zealand"
    assert country_code("New Zealand") == "NZL"
    # Regions may span countries (the Alps cover six), but every MOUNTAIN must
    # resolve to a real country with a real chip code -- the sidebar chip reads
    # the mountain, not the region, and "Other"/"—" there means the country
    # tables lag the roster.
    from config import MOUNTAINS
    for k in MOUNTAINS:
        s = mountain_summary(k)
        assert s["country"] != "Other", f"{k}: unmapped country"
        assert len(s["country_code"]) == 3, f"{k}: bad chip code {s['country_code']}"


# --- within-region ranking --------------------------------------------------
def test_ranks_against_own_region_only():
    rows = rank_within_regions([
        _row("a", "Utah", 90.0), _row("b", "Utah", 50.0), _row("c", "Utah", 10.0),
        _row("z", "Colorado", 1.0),
    ])
    by = {r["key"]: r for r in rows}
    # 'a' beats both other Utah mountains -> 100th; Colorado's 1.0 is irrelevant.
    assert by["a"]["region_score"] == 100
    assert by["b"]["region_score"] == 50    # beats 1 of 2 others
    assert by["c"]["region_score"] == 0


def test_mountain_never_ranks_against_itself():
    # Two identical scores: each beats 0 of the 1 other -> 0th, not 50th. If self
    # were left in the denominator both would read 0 of 2 and the math would still
    # "work" -- this pins the intended cohort (the OTHERS).
    rows = rank_within_regions([_row("a", "Utah", 50.0), _row("b", "Utah", 50.0)])
    assert [r["region_score"] for r in rows] == [0, 0]


def test_lone_mountain_in_region_gets_null_not_f():
    rows = rank_within_regions([_row("solo", "Alaska", 42.0)])
    assert rows[0]["region_score"] is None
    assert rows[0]["region_grade"] is None


def test_unscored_mountain_gets_null_rank():
    rows = rank_within_regions([
        _row("a", "Utah", 90.0), _row("b", "Utah", 50.0), _row("dead", "Utah", None),
    ])
    dead = next(r for r in rows if r["key"] == "dead")
    assert dead["region_score"] is None and dead["region_grade"] is None
    # ...and it doesn't inflate its neighbors' denominator either
    assert next(r for r in rows if r["key"] == "a")["region_score"] == 100


def test_region_grade_uses_the_percentile_curve():
    # 50th percentile is a B- on GRADE_THRESHOLDS (it would be a B+ on the
    # absolute OVERALL curve). This is the two-curves decision, pinned.
    rows = rank_within_regions([_row(k, "Utah", v) for k, v in
                                [("a", 10.0), ("b", 20.0), ("c", 30.0)]])
    b = next(r for r in rows if r["key"] == "b")
    assert b["region_score"] == 50
    assert b["region_grade"] == "B-"


# --- color scale ------------------------------------------------------------
def test_color_for_letters_not_numbers():
    assert color_for("A+") == GRADE_COLORS["A+"]
    assert color_for("F") == GRADE_COLORS["F"]
    assert color_for("N/A") == NA_COLOR
    assert color_for(None) == NA_COLOR


def test_every_grade_threshold_letter_has_a_color():
    from config import GRADE_THRESHOLDS, OVERALL_GRADE_THRESHOLDS
    letters = {g for _, g in GRADE_THRESHOLDS} | {g for _, g in OVERALL_GRADE_THRESHOLDS}
    missing = letters - set(GRADE_COLORS)
    assert not missing, f"letters with no color: {sorted(missing)}"


# --- render cache -----------------------------------------------------------
def _tmpdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def test_cache_roundtrip():
    path = _tmpdb()
    try:
        cache.put(path, "alta", date(2026, 1, 15), {"key": "alta", "score": 87.0})
        got = cache.get_all(path, date(2026, 1, 15))
        assert got["alta"]["score"] == 87.0
        assert got["alta"]["fetched_at"]           # stamped on write
        assert got["alta"]["cached_as_of"] == "2026-01-15"
    finally:
        os.unlink(path)


def test_cache_overwrites_same_day_in_place():
    path = _tmpdb()
    try:
        d = date(2026, 1, 15)
        cache.put(path, "alta", d, {"score": 1.0})
        cache.put(path, "alta", d, {"score": 2.0})
        assert cache.get_all(path, d)["alta"]["score"] == 2.0
    finally:
        os.unlink(path)


def test_cache_falls_back_to_most_recent_prior_day():
    # A first paint on a day nothing has been cached yet should still show
    # yesterday's map rather than an empty one.
    path = _tmpdb()
    try:
        cache.put(path, "alta", date(2026, 1, 10), {"score": 70.0})
        cache.put(path, "alta", date(2026, 1, 14), {"score": 80.0})
        got = cache.get_all(path, date(2026, 1, 15))
        assert got["alta"]["score"] == 80.0        # newest <= as_of, not the oldest
        assert got["alta"]["cached_as_of"] == "2026-01-14"
    finally:
        os.unlink(path)


def test_cache_ignores_future_rows():
    path = _tmpdb()
    try:
        cache.put(path, "alta", date(2026, 2, 1), {"score": 99.0})
        assert cache.get_all(path, date(2026, 1, 15)) == {}
    finally:
        os.unlink(path)


def test_corrupt_cache_row_is_a_miss_not_a_crash():
    path = _tmpdb()
    try:
        conn = cache.connect(path)
        conn.execute("INSERT INTO cached_scores VALUES ('alta','2026-01-15','{not json','x')")
        conn.commit(); conn.close()
        assert cache.get_all(path, date(2026, 1, 15)) == {}
    finally:
        os.unlink(path)


# --- carry-forward of a stale bare reading ----------------------------------
def _obs(rows):
    """rows: [(date, depth, swe, new_snow)]"""
    import pandas as pd
    return pd.DataFrame([
        {"date": pd.Timestamp(d), "snow_depth_inches": dp,
         "swe_inches": sw, "new_snow_24hr": ns} for d, dp, sw, ns in rows])


def test_stale_bare_reading_carries_forward():
    """Stratton's real shape: reports 0.0" through spring, then goes quiet.

    On Jul 10 its last reading was 0.0" on May 31 -- a known zero, 40 days stale.
    Snow can't appear without snowfall, so it's still bare, not "unknown".
    """
    obs = _obs([("2026-05-29", 0.0, None, 0.0),
                ("2026-05-30", 0.0, None, 0.0),
                ("2026-05-31", 0.0, None, 0.0)])
    assert _carried_forward_cover(obs, date(2026, 7, 10)) == 0.0


def test_carry_forward_is_voided_by_snowfall_since():
    obs = _obs([("2026-05-31", 0.0, None, 0.0),
                ("2026-06-20", None, None, 8.0)])   # it snowed after the reading
    assert _carried_forward_cover(obs, date(2026, 7, 10)) is None


def test_carry_forward_expires():
    """A station that dies in autumn holding 0" must not be called bare all winter."""
    obs = _obs([("2025-11-01", 0.0, None, 0.0)])
    assert _carried_forward_cover(obs, date(2025, 11, 20)) == 0.0   # 19d: still bare
    assert _carried_forward_cover(obs, date(2026, 1, 15)) is None   # 75d: unknown


def test_carry_forward_refuses_a_deep_stale_base():
    """A quiet station holding 40" may have melted out -- we don't know."""
    obs = _obs([("2026-03-01", 40.0, None, 0.0)])
    assert _carried_forward_cover(obs, date(2026, 4, 1)) is None


def test_carry_forward_uses_swe_when_no_depth_sensor():
    obs = _obs([("2026-05-31", None, 0.5, 0.0)])    # 0.5" SWE * 3.0 = 1.5" < 6"
    assert _carried_forward_cover(obs, date(2026, 6, 20)) == 1.5


# --- the pin/card agreement invariant --------------------------------------
def test_rank_against_excludes_empty_cohort():
    assert rank_against(50.0, []) == (None, None)
    assert rank_against(None, [1.0, 2.0]) == (None, None)


def test_rank_against_matches_rank_within_regions():
    # The one-mountain path (score_card -> rank_against) and the roster path
    # (rank_within_regions) must agree, or the card contradicts the pin again.
    rows = rank_within_regions([_row(k, "Utah", v) for k, v in
                                [("a", 90.0), ("b", 50.0), ("c", 10.0)]])
    for r in rows:
        peers = [x["score"] for x in rows if x["key"] != r["key"]]
        assert rank_against(r["score"], peers) == (r["region_score"], r["region_grade"])


def test_card_ranks_own_cached_score_not_a_fresh_one():
    """A rank is only meaningful inside one snapshot.

    The card is often fetched offline while the cache was built from live data.
    Ranking a freshly-computed score against differently-sourced peers put Alta at
    the 100th percentile of Utah while its pin said 86th. score_card must rank the
    mountain's OWN CACHED score so the card and the pin can never disagree.
    """
    # cached snapshot: alta sits mid-pack at 13.2
    peers = {
        "alta":     {"score": 13.2, "region": "Utah"},
        "snowbird": {"score": 15.0, "region": "Utah"},
        "brighton": {"score":  5.0, "region": "Utah"},
    }
    # a fresh offline score of 17.8 would beat 2 of 2 peers -> 100th.
    # the cached 13.2 beats 1 of 2 -> 50th. We must get 50th.
    pct, grade = rank_against(peers["alta"]["score"],
                              [v["score"] for k, v in peers.items() if k != "alta"])
    assert pct == 50, "cached-self ranking changed"
    fresh_pct, _ = rank_against(17.8, [v["score"] for k, v in peers.items() if k != "alta"])
    assert fresh_pct == 100, "fixture no longer reproduces the bug it pins"


# --- the in-season gate ------------------------------------------------------
def _row_s(key, region, score, in_season):
    return _row(key, region, score, in_season=in_season)


def test_is_in_season_thresholds():
    assert is_in_season(6.0, 0.0) is True        # depth exactly at the bar
    assert is_in_season(0.0, 3.0) is True        # fresh snow rescues a bare base
    assert is_in_season(5.9, 2.9) is False       # both just under
    assert is_in_season(None, None) is None      # no evidence either way
    assert is_in_season(0.0, None) is False      # a zero reading IS evidence
    assert is_in_season(None, 0.0) is False


def test_off_season_cap_clamps_without_reordering():
    cap = OFF_SEASON["overall_cap"]
    assert apply_off_season_cap(60.0, False) == cap     # a high score is clamped
    assert apply_off_season_cap(5.7, False) == 5.7      # a low one keeps its order
    assert apply_off_season_cap(60.0, True) == 60.0     # in-season untouched
    assert apply_off_season_cap(60.0, None) == 60.0     # unknown is not punished
    assert apply_off_season_cap(None, False) is None


def test_off_season_cap_lands_below_a_b_grade():
    """The cover gate's 0.35 floor still admits an overall of ~43, which reads 'B'.
    The cap has to sit low enough that an off-season mountain can never look good."""
    capped = apply_off_season_cap(100.0, False)
    assert letter_grade(capped, OVERALL_GRADE_THRESHOLDS) in ("D", "F")


def test_only_in_season_mountains_get_a_region_rank():
    """A rank needs positive evidence of cover.

    July 2026 really produced these: Palisades ranked 100th in Tahoe (an A+) with
    zero snow, and Stratton -- whose station stopped reporting on May 31, so its
    cover is unknown and the cover gate never engaged -- scored 60/100 and ranked
    100th in the Northeast. Both must come back with no rank at all.
    """
    rows = rank_within_regions([
        _row_s("palisades", "Tahoe", 7.9, False),     # off-season, bare
        _row_s("kirkwood",  "Tahoe", 5.0, False),
        _row_s("stratton",  "Northeast", 60.0, None),  # unknown: stale station
        _row_s("stowe",     "Northeast", 55.0, None),
        _row_s("mt_hutt",   "NZ", 66.2, True),
        _row_s("cardrona",  "NZ", 16.4, True),
    ])
    by = {r["key"]: r for r in rows}
    for k in ("palisades", "kirkwood", "stratton", "stowe"):
        assert by[k]["region_score"] is None, f"{k} kept a rank"
        assert by[k]["region_grade"] is None
    assert by["mt_hutt"]["region_score"] == 100      # in-season still ranks
    assert by["cardrona"]["region_score"] == 0


def test_off_season_peers_still_count_in_the_denominator():
    """An in-season mountain surrounded by dead ones IS the best local option."""
    rows = rank_within_regions([
        _row_s("alive", "Utah", 40.0, True),
        _row_s("dead1", "Utah", 5.0, False),
        _row_s("dead2", "Utah", 3.0, False),
    ])
    by = {r["key"]: r for r in rows}
    assert by["alive"]["region_score"] == 100        # ranked against the dead peers
    assert by["dead1"]["region_score"] is None


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
