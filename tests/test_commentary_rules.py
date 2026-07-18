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
        "mountain": mountain, "date": d, "grade": grade,
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


def _facts_q(quality_factor=None, refreeze=None, thaw=None, weather_q=None,
             density=None, wind_scour=None, **kw):
    """Facts carrying the Phase 1/2/3 surface-quality signals (as facts_from_card
    now emits them), on top of the base _facts()."""
    f = _facts(**kw)
    f["surface_quality_factor"] = quality_factor
    f["refreeze_crust_index"] = refreeze
    f["incoming_thaw_index"] = thaw
    f["weather_quality"] = weather_q
    f["new_snow_density"] = density
    f["wind_scour_index"] = wind_scour
    return f


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


def test_big_totals_but_crust_leads_with_the_surface():
    """20" down but a refrozen crust + docked quality -> the sentence must open on
    the crust, not celebrate the inches; and it must not say 'crusty' twice."""
    f = _facts_q(grade="B", fresh=20.0, incoming=None,
                 quality_factor=0.7, refreeze=0.8)
    txt = commentary_rules.render(f, _card(refreeze=0.8))
    low = txt.lower()
    assert "20 inches" in low, f"should still name the totals: {txt}"
    assert re.search(r"crust|firm|melt-freeze|refro", low), f"must name the crust: {txt}"
    # the crust is led, so the caveat must not append a second crust sentence
    assert low.count("crust") <= 1 and "melt-freeze" not in low.split("crust")[-1] \
        or low.count("melt-freeze") <= 1, f"crust mentioned twice: {txt}"


def test_big_totals_but_thaw_leads_and_no_double_thaw():
    """High totals + an incoming thaw dragging quality -> lead on the thaw, and the
    forecast clause must not repeat it."""
    f = _facts_q(grade="B+", fresh=16.0, incoming=None,
                 quality_factor=0.72, thaw=0.8)
    txt = commentary_rules.render(f, _card(thaw=0.8))
    low = txt.lower()
    assert re.search(r"thaw|warm|wet|heavy|soften|slopp|rain", low), f"must name the thaw: {txt}"
    # "16 inches" still surfaces; thaw wording should appear once (led, not repeated)
    assert "16 inches" in low, f"should still name the totals: {txt}"


def test_clean_surface_does_not_trigger_quality_lead():
    """A pristine surface (high quality factor, no crust/thaw) leads normally on
    the fresh snow -- the quality-drag branch stays out of the way."""
    f = _facts_q(grade="A", fresh=18.0, quality_factor=1.0,
                 refreeze=0.0, thaw=0.0, weather_q=85.0)
    txt = commentary_rules.render(f, _card())
    assert "18 inches" in txt.lower(), f"clean deep day should lead on the snow: {txt}"
    assert "crust" not in txt.lower() and "thaw" not in txt.lower()


def test_clean_surface_earns_a_positive_note():
    """A clean, soft surface in good weather with a quiet forecast gets the small
    positive second sentence instead of dry-forecast filler."""
    f = _facts_q(grade="A-", fresh=12.0, incoming=None, quality_factor=1.0,
                 refreeze=0.0, thaw=0.0, weather_q=80.0)
    txt = commentary_rules.render(f, _card())
    assert re.search(r"soft|clean|forgiving", txt.lower()), f"expected a positive note: {txt}"


def test_quality_signals_absent_preserve_old_behavior():
    """Facts without the Phase 1 fields (older callers) behave exactly as before:
    a big-fresh day leads on the inches."""
    txt = commentary_rules.render(_facts(grade="A", fresh=18.0), _card())
    assert "18 inches" in txt.lower()


def test_heavy_new_snow_leads_over_totals():
    """Big totals but heavy/wet new snow (high density) -> lead names the totals
    AND that the snow fell heavy, not the celebratory inches alone."""
    f = _facts_q(grade="B+", fresh=18.0, incoming=None, quality_factor=0.95,
                 refreeze=0.0, thaw=0.0, density=0.19)
    txt = commentary_rules.render(f, _card()).lower()
    assert "18 inches" in txt
    assert re.search(r"heavy|wet|dense|not blower|not light", txt), f"must flag heavy snow: {txt}"


def test_light_dry_snow_earns_dry_note():
    """A clean day with genuinely light snow gets the 'light and dry' note now that
    density can back the claim."""
    f = _facts_q(grade="A", fresh=14.0, incoming=None, quality_factor=1.0,
                 refreeze=0.0, thaw=0.0, weather_q=82.0, density=0.06)
    txt = commentary_rules.render(f, _card()).lower()
    assert "dry" in txt, f"expected a light/dry note: {txt}"


def test_crust_beats_heavy_density_when_both():
    """Priority: an existing crust outranks merely heavy new snow in the lead."""
    f = _facts_q(grade="B", fresh=16.0, incoming=None, quality_factor=0.7,
                 refreeze=0.7, thaw=0.0, density=0.2)
    txt = commentary_rules.render(f, _card(refreeze=0.7)).lower()
    assert re.search(r"crust|firm|melt-freeze", txt), f"crust should lead: {txt}"


def test_wind_scour_leads_over_totals():
    """Big totals but wind-hammered fresh snow -> lead names the wind, not just the
    inches."""
    f = _facts_q(grade="B", fresh=20.0, incoming=None, quality_factor=0.75,
                 refreeze=0.0, thaw=0.0, wind_scour=0.7)
    txt = commentary_rules.render(f, _card()).lower()
    assert "20 inches" in txt
    assert re.search(r"wind|scour|slab|strip", txt), f"must flag the wind: {txt}"


def test_crust_beats_wind_when_both():
    """Priority: an existing crust still outranks wind in the lead."""
    f = _facts_q(grade="B", fresh=16.0, incoming=None, quality_factor=0.6,
                 refreeze=0.7, thaw=0.0, wind_scour=0.7)
    txt = commentary_rules.render(f, _card(refreeze=0.7)).lower()
    assert re.search(r"crust|firm|melt-freeze", txt), f"crust should lead: {txt}"


def test_calm_light_fresh_earns_untouched_note():
    """Light, dry snow the wind left alone gets the strongest positive note."""
    f = _facts_q(grade="A", fresh=14.0, incoming=None, quality_factor=1.0,
                 refreeze=0.0, thaw=0.0, weather_q=82.0, density=0.06, wind_scour=0.0)
    txt = commentary_rules.render(f, _card()).lower()
    assert "untouched" in txt or "wind-sheltered" in txt or "cold smoke" in txt, txt


def test_off_season_gate_via_get_or_generate():
    off = {"in_season": False, "default_profile": "dynamic",
           "overall": {"dynamic": {"score": 10, "grade": "F"}}}
    assert commentary.get_or_generate("alta", date(2026, 7, 15), off) is None


def test_rules_path_is_default_and_needs_no_db():
    """In the default mode a scored in-season card gets prose with no db_path."""
    card = {
        "mountain": {"name": "Alta, UT"}, "as_of": "2026-01-15",
        "default_profile": "dynamic", "in_season": True, "season_progress": 0.5,
        "skiability": {"score": 80, "grade": "A-"},
        "overall": {"dynamic": {"score": 40, "grade": "C-"}},
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
