"""Tests for the rules-based commentary engine (ski/commentary_rules.py).

No network, no key, no pytest required:  python tests/test_commentary_rules.py

Invariants under test: it never invents a number that isn't in the facts, it is
deterministic per (mountain, day), it varies across mountains, it respects the
off-season gate through get_or_generate, and it always returns one-or-two
non-empty sentences for a scored in-season card.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski import commentary, commentary_rules  # noqa: E402


def _facts(mountain="Alta, UT", d="2026-01-15", grade="A", fresh=18.0, base=60.0,
           season=90.0, month=None, incoming=8.0, hrs=48, alert=False, progress=0.5):
    return {
        "mountain": mountain, "date": d, "overall_grade": grade,
        "fresh_snow_last_7_days_inches": fresh, "base_depth_inches": base,
        "season_to_date_percentile_vs_history": season,
        "last_30_days_percentile_vs_history": month,
        "incoming_snow_inches": incoming, "incoming_snow_window_hours": hrs,
        "storm_alert": alert, "season_progress_fraction": progress,
    }


def _card(**kw):
    return {"outlook": {"thaw_index": kw.get("thaw"),
                        "refreeze_index": kw.get("refreeze")},
            "stale": kw.get("stale", False), "data_age_days": kw.get("age")}


def test_deterministic_per_mountain_day():
    f = _facts()
    a = commentary_rules.render(f, _card())
    b = commentary_rules.render(f, _card())
    assert a == b, "same inputs must yield identical text"


def test_varies_across_mountains():
    outs = {commentary_rules.render(_facts(mountain=m), _card())
            for m in ["Alta", "Vail", "Taos", "Aspen", "Snowbird", "Telluride",
                      "Stowe", "Whistler"]}
    assert len(outs) >= 4, f"too repetitive across mountains: {outs}"


def test_one_or_two_sentences_nonempty():
    for grade in ["A+", "A-", "B", "C+", "C-", "D", "F"]:
        txt = commentary_rules.render(_facts(grade=grade), _card())
        assert txt and txt[0].isupper()
        # 1 or 2 sentences: count terminal punctuation, allowing a bracketed note.
        n = len(re.findall(r"[.!?]['\")\]]?(?:\s|$)", txt))
        assert 1 <= n <= 2, f"{grade!r} -> {n} sentences: {txt}"


def test_only_states_numbers_it_was_given():
    # No base number given -> the string must not fabricate an inch count for base.
    f = _facts(fresh=None, base=None, incoming=None, season=40.0)
    txt = commentary_rules.render(f, _card())
    assert "inch" not in txt, f"invented an inch figure from nulls: {txt}"


def test_incoming_number_matches_facts():
    txt = commentary_rules.render(_facts(fresh=3.0, incoming=11.0, alert=True), _card())
    assert "11 inches" in txt and "storm" in txt.lower()


def test_thaw_produces_downside_clause():
    txt = commentary_rules.render(
        _facts(grade="A-", fresh=16.0, incoming=None), _card(thaw=0.8))
    assert re.search(r"thaw|soft|warm|slopp|heavy|rain", txt.lower())


def test_stale_note_when_no_forecast():
    txt = commentary_rules.render(
        _facts(grade="B+", fresh=None, base=None, incoming=None, season=74.0),
        _card(stale=True, age=9))
    assert "9 days old" in txt


def test_implausible_base_never_leads():
    # Reanalysis over a glacier can report tens of metres; prose must not say it.
    f = _facts(mountain="Zermatt", grade="B-", fresh=5.5, base=1312.0,
               season=68.0, incoming=None)
    txt = commentary_rules.render(f, _card())
    assert "1312" not in txt and "1,312" not in txt


def test_off_season_gate_via_get_or_generate():
    off = {"in_season": False, "default_profile": "dynamic",
           "overall": {"dynamic": {"score": 10, "grade": "F"}}}
    assert commentary.get_or_generate("alta", date(2026, 7, 15), off) is None


def test_rules_path_is_default_and_needs_no_db():
    """In the default mode a scored in-season card gets prose with no db_path."""
    card = {
        "mountain": {"name": "Alta, UT"}, "as_of": "2026-01-15",
        "default_profile": "dynamic", "in_season": True, "season_progress": 0.5,
        "overall": {"dynamic": {"score": 80, "grade": "A-"}},
        "grades": {"season": {"percentile": 88}, "in_season": {"percentile": 80},
                   "base": {"grade": "A"}},
        "conditions": {"fresh_7d": 14.0, "base_depth": 55},
        "forecast": {"inches": 4.0, "window_hours": 48, "alert": False},
        "outlook": {"thaw_index": 0.0, "refreeze_index": 0.0},
        "stale": False, "data_age_days": 1,
    }
    assert commentary.COMMENTARY_MODE == "rules"
    txt = commentary.get_or_generate("alta", date(2026, 1, 15), card)
    assert txt and "A-" in txt


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
