"""Tests for the JSON scorecard data contract (ski/card.py).

No network, no pytest, no FastAPI -- builds a tiny SQLite DB from synthetic
observations and asserts `scorecard()` returns a JSON-serializable dict with the
shape a frontend depends on. Run standalone:  python tests/test_card.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import MOUNTAINS, SCORE_PROFILES  # noqa: E402
from ski import card  # noqa: E402
from ski.card import _grade_json, _overall_by_profile, _round, scorecard  # noqa: E402
from ski.db import upsert_observations  # noqa: E402


# --- build a temp DB of synthetic history for a real mountain key -----------
def _seed_db(mountain_key: str = "alta") -> tuple[str, str]:
    """Write a few water years of obs for the mountain's station to a temp DB.

    Returns (db_path, mountain_key). Each year lays `total` inches as 1"/day of
    new snow from Oct 1, accumulating depth + SWE, so the season grade has real
    history to rank against.
    """
    station = MOUNTAINS[mountain_key]["snotel_station"]
    rows = []
    for wy, total in {2021: 200, 2022: 250, 2023: 300, 2024: 350, 2025: 180}.items():
        depth = 0.0
        start = date(wy - 1, 10, 1)
        for i in range(160):  # Oct 1 .. mid-Mar-ish
            new = 1.0 if i < int(total / 2) else 0.0
            depth += new
            d = start + pd.Timedelta(days=i)
            rows.append({"date": pd.Timestamp(d), "swe_inches": depth / 10.0,
                         "snow_depth_inches": depth, "new_snow_24hr": new})
    df = pd.DataFrame(rows)
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    upsert_observations(path, station, df)
    return path, mountain_key


def _card(**kw):
    path, key = _seed_db()
    try:
        return scorecard(key, db_path=path, as_of=date(2025, 1, 15),
                         use_network=False, **kw)
    finally:
        os.unlink(path)


# --- the contract: serializable + expected shape ----------------------------
def test_scorecard_is_json_serializable():
    c = _card()
    s = json.dumps(c)                 # must not raise
    assert json.loads(s) == c         # round-trips cleanly


def test_scorecard_top_level_shape():
    c = _card()
    for key in ("mountain", "as_of", "default_profile", "season_progress",
                "overall", "subscores", "grades", "forecast", "outlook",
                "conditions", "sources"):
        assert key in c, f"missing top-level key {key!r}"
    assert c["mountain"]["key"] == "alta"
    assert c["as_of"] == "2025-01-15"
    assert set(c["subscores"]) == {"season", "in_season", "forecast", "conditions"}
    assert set(c["grades"]) == {"season", "in_season", "base"}
    assert set(c["sources"]) == {"history", "forecast", "weather"}


def test_offline_forecast_and_weather_are_null():
    # use_network=False -> nothing incoming, no live weather, no outlook
    c = _card()
    assert c["forecast"] is None
    assert c["subscores"]["forecast"] is None
    assert c["conditions"]["weather"] is None
    assert c["conditions"]["weather_quality"] is None
    assert c["outlook"] is None
    # sources make the gap visible instead of silently renormalizing it away
    assert c["sources"]["history"] == "snotel"
    assert c["sources"]["forecast"] is None
    assert c["sources"]["weather"] is None


def test_overall_has_every_profile_plus_dynamic():
    c = _card()
    expected = {"dynamic", *SCORE_PROFILES}
    assert set(c["overall"]) == expected
    for prof, entry in c["overall"].items():
        assert "score" in entry and "grade" in entry
    assert "leaning" in c["overall"]["dynamic"]


def test_grades_carry_percentile_and_confidence():
    c = _card()
    season = c["grades"]["season"]
    assert season["grade"]                       # a letter
    assert 0 <= season["percentile"] <= 100
    assert season["n_years"] >= 1
    assert isinstance(season["low_confidence"], bool)


# --- helper units -----------------------------------------------------------
def test_round_passes_none_and_rounds_numbers():
    assert _round(None) is None
    assert _round(3.14159, 1) == 3.1
    assert _round("A+") == "A+"


def test_grade_json_handles_none():
    assert _grade_json(None) is None


def test_overall_by_profile_drops_dynamic_without_progress():
    subs = {"season": 50.0, "in_season": 50.0, "forecast": None, "conditions": 50.0}
    with_prog = _overall_by_profile(subs, season_progress=0.5)
    without = _overall_by_profile(subs, season_progress=None)
    assert "dynamic" in with_prog
    assert "dynamic" not in without
    assert set(without) == set(SCORE_PROFILES)


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
