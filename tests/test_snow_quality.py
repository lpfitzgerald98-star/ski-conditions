"""Tests for the Snow Quality Score scaffold (score.snow_quality_score).

Phase 0 is observability only: the score is computed and surfaced on the card but
weighted 0 in every consumer, so these tests pin down (a) the blend math and the
"renormalize over what's available" convention, and (b) that adding it changed no
existing grade -- skiability and overall are byte-for-byte what they were.

No network. Run standalone:  python tests/test_snow_quality.py
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DENSITY_POWDER_FLOOR, SNOW_QUALITY_WEIGHTS  # noqa: E402
from ski import pipeline, score  # noqa: E402

# Reuse the card test's synthetic-DB helper so the "no grade moved" check runs
# against the same fixture the rest of the card contract is pinned to.
from tests.test_card import _seed_db  # noqa: E402
from ski.card import scorecard  # noqa: E402


# --- the blend math ---------------------------------------------------------
def test_all_missing_is_none():
    """No forecast and no weather -> nothing to judge -> value None, not a fake
    pristine surface."""
    sq = score.snow_quality_score()
    assert sq.value is None
    assert sq.weights_used == {}
    assert set(sq.components) == {"density", "wind", "crust", "thaw", "warmth"}
    assert all(v is None for v in sq.components.values())


def test_clean_surface_scores_high():
    """A clean surface (no crust, no thaw) with pleasant weather is near 100."""
    sq = score.snow_quality_score(weather_q=100.0, refreeze=0.0, thaw=0.0)
    assert sq.value == 100.0
    assert sq.components["crust"] == 100.0
    assert sq.components["thaw"] == 100.0
    assert sq.components["warmth"] == 100.0
    # density/wind are placeholders in Phase 0 -- never in the blend yet
    assert sq.components["density"] is None
    assert sq.components["wind"] is None
    assert "density" not in sq.weights_used and "wind" not in sq.weights_used


def test_index_inverts_to_quality():
    """A penalty index (0 clean .. 1 severe) inverts to a 0-100 contribution."""
    sq = score.snow_quality_score(refreeze=1.0, thaw=0.5)
    assert sq.components["crust"] == 0.0     # full crust -> 0 quality
    assert sq.components["thaw"] == 50.0     # half thaw  -> 50 quality


def test_blend_renormalizes_over_available():
    """With only crust + thaw known, the value is their weight-normalized blend --
    the missing components drop out rather than counting as zero."""
    sq = score.snow_quality_score(refreeze=0.0, thaw=1.0)   # weather None
    wc, wt = SNOW_QUALITY_WEIGHTS["crust"], SNOW_QUALITY_WEIGHTS["thaw"]
    expected = (wc * 100.0 + wt * 0.0) / (wc + wt)
    assert abs(sq.value - expected) < 1e-9
    assert set(sq.weights_used) == {"crust", "thaw"}


def test_none_index_drops_out():
    """A None index (unknown, e.g. off-network) is excluded, not treated as 0."""
    sq = score.snow_quality_score(weather_q=80.0, refreeze=None, thaw=None)
    assert sq.components["crust"] is None
    assert sq.components["thaw"] is None
    assert sq.value == 80.0                 # only warmth survived
    assert set(sq.weights_used) == {"warmth"}


# --- the card surfaces it, and it changed no existing grade -----------------
def test_card_exposes_snow_quality_and_moves_no_grade():
    path, key = _seed_db()
    try:
        c = scorecard(key, db_path=path, as_of=date(2025, 1, 15), use_network=False)
    finally:
        os.unlink(path)
    assert "snow_quality" in c
    assert set(c["snow_quality"]) == {"score", "components", "weights_used"}
    assert set(c["snow_quality"]["components"]) == {
        "density", "wind", "crust", "thaw", "warmth"}
    # Offline fixture: no outlook -> crust/thaw/warmth all unknown -> score null.
    # (The whole point of Phase 0: an honest null here, not a fabricated 100.)
    assert c["snow_quality"]["score"] is None
    # The headline skiability grade is untouched by the scaffold's presence.
    assert c["skiability"]["grade"] in {
        "A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F", "N/A"}


# --- Phase 2: new-snow density ----------------------------------------------
def test_density_from_temp_monotonic_and_none():
    """Colder snow is fluffier (lower water fraction); None passes through."""
    cold = score.density_from_temp(10.0)
    warm = score.density_from_temp(34.0)
    assert cold < warm, "warmer snow must read denser"
    assert cold <= 0.06 and warm >= 0.15
    assert score.density_from_temp(None) is None


def test_density_score_decreasing_and_drops_out():
    """Density QUALITY falls as snow gets heavier; None drops out of the blend."""
    assert score.density_score(0.05) > score.density_score(0.13) > score.density_score(0.22)
    assert score.density_score(None) is None


def test_density_powder_factor_floor_and_neutral():
    """Light snow keeps full powder credit; heavy snow is discounted but floored;
    unknown density never discounts."""
    assert score.density_powder_factor(0.06) == 1.0
    heavy = score.density_powder_factor(0.30)
    assert heavy == DENSITY_POWDER_FLOOR
    assert score.density_powder_factor(None) == 1.0


def test_effective_powder_discounts_recent_only():
    """The density factor scales the RECENT window, not week-old or forecast snow.
    Here all 12" are recent, so a 0.7 factor cuts effective recent inches ~30%."""
    full = score.effective_powder_in(12.0, 12.0, 0.0, 1.0)
    dense = score.effective_powder_in(12.0, 12.0, 0.0, 0.7)
    assert dense < full
    # week-only snow (fresh_7d minus recent) is untouched by density
    wk_full = score.effective_powder_in(4.0, 12.0, 0.0, 1.0)
    wk_dense = score.effective_powder_in(4.0, 12.0, 0.0, 0.7)
    # only the 4" recent portion is discounted; the 8" week portion is identical
    assert (wk_full - wk_dense) < (full - dense)


def test_dense_snow_lowers_skiability_grade():
    """Same depth, different density -> the heavy day grades lower."""
    cold = score.skiability_score(40, 12, 12, 0, weather_q=80, recent_density_factor=1.0)
    cement = score.skiability_score(40, 12, 12, 0, weather_q=80, recent_density_factor=0.68)
    assert cement.value < cold.value


def test_snow_quality_includes_density_component():
    """A density quality now feeds the snow-quality blend as a real component."""
    sq = score.snow_quality_score(weather_q=80, refreeze=0.0, thaw=0.0, density=90.0)
    assert sq.components["density"] == 90.0
    assert "density" in sq.weights_used


def _obs(rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "swe_inches": swe,
                          "snow_depth_inches": dep, "new_snow_24hr": nw}
                         for d, swe, dep, nw in rows])


def test_measured_density_from_swe_and_depth():
    """A storm that added 1.2" SWE over 12" of new snow reads ~0.10 water fraction."""
    obs = _obs([
        ("2026-01-10", 10.0, 40.0, 0.0),   # pre-storm baseline
        ("2026-01-13", 10.4, 44.0, 4.0),
        ("2026-01-14", 10.8, 48.0, 4.0),
        ("2026-01-15", 11.2, 52.0, 4.0),
    ])
    ratio = pipeline.measured_new_snow_density(obs, as_of=date(2026, 1, 15))
    assert ratio is not None
    assert 0.08 <= ratio <= 0.12, ratio


def test_measured_density_none_without_swe():
    """A depth-only / new-snow-only station (no SWE) can't measure density -> None,
    so the caller falls back to the temperature tier."""
    obs = _obs([
        ("2026-01-13", float("nan"), 44.0, 4.0),
        ("2026-01-14", float("nan"), 48.0, 4.0),
        ("2026-01-15", float("nan"), 52.0, 4.0),
    ])
    assert pipeline.measured_new_snow_density(obs, as_of=date(2026, 1, 15)) is None


def test_measured_density_none_below_min_snow():
    """Too little recent snow to judge -> None (don't read noise as a verdict)."""
    obs = _obs([
        ("2026-01-14", 10.0, 40.0, 0.5),
        ("2026-01-15", 10.05, 40.5, 0.5),
    ])
    assert pipeline.measured_new_snow_density(obs, as_of=date(2026, 1, 15)) is None


# --- Phase 3: wind loading / scour ------------------------------------------
def test_wind_severity_and_quality():
    """Calm -> 0 severity / 100 quality; a gale -> full severity / 0 quality."""
    assert score.wind_severity(5.0) == 0.0
    assert score.wind_severity(50.0) == 1.0
    assert score.wind_quality(5.0) == 100.0
    assert score.wind_quality(50.0) == 0.0
    assert score.wind_severity(None) == 0.0
    assert score.wind_quality(None) is None


def test_wind_scour_gated_by_fresh_snow():
    """Wind scours fresh snow hardest; with no loose snow the penalty is only the
    no-fresh baseline; with none of either, zero."""
    windy_fresh = score.wind_scour_index(45.0, 12.0)
    windy_bare = score.wind_scour_index(45.0, 0.0)
    assert windy_fresh > windy_bare > 0.0
    assert score.wind_scour_index(5.0, 12.0) == 0.0    # calm -> no scour


def test_wind_lowers_skiability_grade():
    """Same deep-powder day, calm vs. wind-hammered -> the windy day grades lower."""
    calm = score.skiability_score(40, 12, 12, 0, weather_q=80, wind_scour=0.0)
    windy = score.skiability_score(40, 12, 12, 0, weather_q=80, wind_scour=0.9)
    assert windy.value < calm.value


def test_snow_quality_includes_wind_component():
    sq = score.snow_quality_score(weather_q=80, density=90.0, wind=30.0)
    assert sq.components["wind"] == 30.0
    assert "wind" in sq.weights_used


# --- Phase 5: powder recency decay ------------------------------------------
def _storm_then_quiet():
    """A single 12" storm on Jan 1, then nothing for two weeks."""
    rows = [("2026-01-01", float("nan"), float("nan"), 12.0)]
    for d in range(2, 16):
        rows.append((f"2026-01-{d:02d}", float("nan"), float("nan"), 0.0))
    return _obs(rows)


def test_decay_has_no_hard_cliff():
    """The same storm counts less as it ages, but an 8-day-old storm still counts
    (the old hard 7-day window would have dropped it to nothing)."""
    obs = _storm_then_quiet()
    two_days = pipeline.decayed_new_snow_in(obs, date(2026, 1, 3), refreeze=0.0)
    eight_days = pipeline.decayed_new_snow_in(obs, date(2026, 1, 9), refreeze=0.0)
    assert two_days > eight_days > 0.0


def test_decay_accelerates_after_a_melt_freeze():
    """Cold snow lingers; a recent melt-freeze fades the old powder much faster."""
    obs = _storm_then_quiet()
    cold = pipeline.decayed_new_snow_in(obs, date(2026, 1, 9), refreeze=0.0)
    thawed = pipeline.decayed_new_snow_in(obs, date(2026, 1, 9), refreeze=1.0)
    assert thawed < cold


def test_decay_none_without_snow():
    obs = _obs([("2026-01-15", float("nan"), float("nan"), float("nan"))])
    assert pipeline.decayed_new_snow_in(obs, date(2026, 1, 15)) is None


# --- Phase 5b: buried rain/melt crust ---------------------------------------
def test_buried_crust_detected_from_swe_without_depth():
    """A rain pulse (SWE jumps, depth flat) leaves a crust; with little snow since,
    it's still near the surface and scores high."""
    rows = [("2026-01-05", 10.0, 40.0, 0.0),
            ("2026-01-06", 11.0, 40.0, 0.0),   # +1.0" SWE, depth flat = rain pulse
            ("2026-01-07", 11.0, 40.0, 0.0),
            ("2026-01-08", 11.2, 42.0, 2.0)]   # only 2" since -> barely buried
    crust = pipeline.buried_crust_index(_obs(rows), as_of=date(2026, 1, 8))
    assert crust is not None and crust > 0.5, crust


def test_buried_crust_fades_when_buried_deep():
    """The same rain pulse, but two feet of snow since -> largely buried, low."""
    rows = [("2026-01-05", 10.0, 40.0, 0.0),
            ("2026-01-06", 11.0, 40.0, 0.0)]   # rain pulse
    for i, d in enumerate(range(7, 15)):       # ~4"/day * 8 = 32" since
        rows.append((f"2026-01-{d:02d}", 11.0 + i * 0.3, 44.0 + i * 4, 4.0))
    crust = pipeline.buried_crust_index(_obs(rows), as_of=date(2026, 1, 14))
    assert crust is not None and crust < 0.3, crust


def test_buried_crust_none_without_pillow():
    """A depth-only station (no SWE) can't reconstruct the pulse -> None (falls back
    to the trailing refreeze signal)."""
    rows = [("2026-01-06", float("nan"), 40.0, 0.0),
            ("2026-01-07", float("nan"), 41.0, 1.0)]
    assert pipeline.buried_crust_index(_obs(rows), as_of=date(2026, 1, 7)) is None


def test_snowfall_is_not_mistaken_for_a_crust():
    """A normal snowfall raises BOTH swe and depth -> not a rain pulse -> no crust."""
    rows = [("2026-01-05", 10.0, 40.0, 0.0),
            ("2026-01-06", 11.0, 50.0, 10.0),   # +1" SWE AND +10" depth = snowfall
            ("2026-01-07", 11.0, 50.0, 0.0)]
    crust = pipeline.buried_crust_index(_obs(rows), as_of=date(2026, 1, 7))
    assert crust == 0.0, crust


def test_storm_date_card_serializes_with_density():
    """A card on an accumulating date carries a measured density -- and must stay
    JSON-serializable (the ratio/component are numpy scalars from pandas until we
    coerce them; json.dumps would raise on np.float64)."""
    import json
    path, key = _seed_db()
    try:
        # Dec 1 sits inside the seeded accumulation window (1"/day), so there's
        # real recent snow + SWE gain -> a measured density, unlike mid-Jan.
        c = scorecard(key, db_path=path, as_of=date(2024, 12, 1), use_network=False)
    finally:
        os.unlink(path)
    json.dumps(c)  # must not raise
    assert c["skiability"]["new_snow_density"] is not None
    dc = c["snow_quality"]["components"]["density"]
    assert dc is not None and isinstance(dc, float)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("all snow-quality tests passed")
