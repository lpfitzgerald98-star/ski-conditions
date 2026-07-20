"""Trip Predictor -- rank mountains for a FUTURE date.

You can't forecast weather months out, so a trip ranking leans on HISTORY: how a
mountain has typically skied during that calendar window. But when the trip is
close, today's live conditions are real signal and should count. So the trip
score BLENDS the two, weighting today's conditions down as the trip date recedes:

    TripScore = w * current_comparable_score + (1 - w) * historical_baseline
    w = score.decay_weight(lead_days, TRIP_LEAD_DECAY['half_life_days'])

`w` is the SAME exponential half-life curve that decays a snow observation by its
age (pipeline.decayed_new_snow_in) -- here applied to the inverse axis, lead time.
At lead 0, w = 1 and the trip score IS today's global score (it converges to live
scoring); months out, w -> 0 and the ranking is pure history.

The two terms:

  current_comparable_score  today's `global_score` (ski.comparable) -- already a
        0-100 cross-mountain rank blending base/fresh/season/forecast/quality.
        Computed live elsewhere; this module just consumes it.

  historical_baseline       a comparable score built the SAME way (score_population)
        but from CLIMATOLOGY: each mountain's typical conditions in a +/- window of
        the target date's day-of-water-year, across all years. It uses its own
        weights (config.TRIP_BASELINE_WEIGHTS) -- for a trip you can't catch a
        specific storm, so persistent pack signals (typical base depth, cumulative
        season total by that date) lead and the transient `fresh` window is demoted.

`climatology` is the one expensive step (a few group-bys over a station's whole
record); it's pure and cache-friendly, so callers compute it once per mountain and
reuse it for every date (build_snapshot does all 366; the /trip endpoint memoizes).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from config import (COMPARABLE_FRESH_WINDOW_DAYS, COVER_GATE,
                    TRIP_BASELINE_WEIGHTS, TRIP_LEAD_DECAY, TRIP_WINDOW_DAYS)
from ski import comparable
from ski.grading import _daily_increment, _prepare
from ski.score import decay_weight, is_in_season
from ski.watercalendar import day_of_water_year

DOWY_MAX = 366  # a water year with a Feb 29 reaches day 366; index 1..366 always


# ---------------------------------------------------------------------------
# Climatology -- a mountain's typical conditions by day-of-water-year
# ---------------------------------------------------------------------------
def _centered_smooth(s: pd.Series, window_days: int, how: str = "median") -> pd.Series:
    """Smooth a per-dowy series over +/- window_days (the '+/- N day window around
    the target date' the baseline is defined on), reindexed to a full 1..366.

    A centered rolling aggregate of the per-dowy summary is a fast, robust stand-in
    for re-pooling every observation within the window at every target day; for a
    ranking baseline the difference is immaterial and this is O(366), not O(years x
    window). `min_periods=1` so sparse shoulder-season days still get a value."""
    full = s.reindex(range(1, DOWY_MAX + 1))
    width = 2 * window_days + 1
    roll = full.rolling(width, center=True, min_periods=1)
    return roll.median() if how == "median" else roll.mean()


def climatology(obs: pd.DataFrame, wy_start: int, season_start_dowy: int,
                metric: str, window_days: int = TRIP_WINDOW_DAYS) -> dict[int, dict]:
    """Per day-of-water-year (1..366): a mountain's typical conditions across all
    years, as the same absolute inputs ski.comparable ranks live.

    Returns {dowy: {base_in, fresh_in, season_in, n_years}} (empty when there's no
    usable history). All three inputs are smoothed over +/- `window_days` of dowy:
      base_in    median settled base depth (snow depth, or SWE x ratio when a
                 pillow station reports no depth) -- the persistent pack.
      fresh_in   typical trailing-window fresh: mean daily new snow x the comparable
                 fresh window, so it's the same unit/scale the live `abs_fresh_in`
                 carries -- "does this place reliably snow this week of the year".
      season_in  median cumulative season-to-date accumulation by that dowy, in snow
                 inches (SWE-gain metrics converted with the same ratio the cover
                 gate uses), so SWE and depth-change mountains compare on one scale.
      n_years    distinct water years contributing near that dowy -- the confidence
                 signal (see config.LOW_CONFIDENCE_YEARS)."""
    if obs is None or obs.empty:
        return {}
    df = _prepare(obs, wy_start)
    if df.empty:
        return {}

    # base: settled depth estimate per row (depth, else SWE -> depth)
    depth_est = df["snow_depth_inches"].where(
        df["snow_depth_inches"].notna(),
        df["swe_inches"] * COVER_GATE["swe_to_depth_ratio"])
    base_by_dowy = depth_est.groupby(df["dowy"]).median()
    base_in = _centered_smooth(base_by_dowy, window_days, "median")

    # fresh: climatological daily snowfall rate x the comparable fresh window
    fresh_rate = df["new_snow_24hr"].groupby(df["dowy"]).mean()
    fresh_in = _centered_smooth(fresh_rate, window_days, "median") * COMPARABLE_FRESH_WINDOW_DAYS

    # season-to-date: cumulative positive increment within each water year from the
    # season start, then the per-dowy median across years.
    inc = _daily_increment(df, metric).clip(lower=0).fillna(0.0)
    inc = inc.where(df["dowy"] >= season_start_dowy, 0.0)
    cum = inc.groupby(df["wy"]).cumsum()
    season_by_dowy = cum.groupby(df["dowy"]).median()
    season_in = _centered_smooth(season_by_dowy, window_days, "mean")
    if metric == "swe_gain":
        season_in = season_in * COVER_GATE["swe_to_depth_ratio"]

    # confidence: distinct water years present near each dowy (rolling max so a lone
    # sparse day inside a well-covered window isn't flagged low all by itself).
    years_by_dowy = df.groupby("dowy")["wy"].nunique()
    n_years = _centered_smooth(years_by_dowy.astype(float), window_days, "median")
    n_years = n_years.reindex(range(1, DOWY_MAX + 1)).ffill().bfill()

    out: dict[int, dict] = {}
    for d in range(1, DOWY_MAX + 1):
        b = base_in.get(d)
        f = fresh_in.get(d)
        s = season_in.get(d)
        ny = n_years.get(d)
        out[d] = {
            "base_in": None if b is None or np.isnan(b) else float(b),
            "fresh_in": None if f is None or np.isnan(f) else float(max(0.0, f)),
            "season_in": None if s is None or np.isnan(s) else float(max(0.0, s)),
            "n_years": 0 if ny is None or np.isnan(ny) else int(round(ny)),
        }
    return out


def target_dowy(target: date, wy_start: int) -> int:
    """Day-of-water-year for a trip's calendar date under a mountain's water year.

    Feb 29 has no slot in the non-leap-based climatology index in most years; fold
    it onto Feb 28 so a leap-day trip still resolves rather than erroring."""
    if target.month == 2 and target.day == 29:
        target = target.replace(day=28)
    return day_of_water_year(target, wy_start)


def baseline_row(clim: dict[int, dict], dowy: int, key: str, region: str) -> dict:
    """One ski.comparable-shaped row from a mountain's climatology at `dowy`.

    Carries the absolute inputs score_population ranks (`abs_*`) plus the trip
    breakdown fields. `in_season` is derived from the typical base+fresh the same
    way live scoring does (score.is_in_season), so a date that is historically bare
    (a Northern resort in July) drops out of the ranking instead of scoring an A+
    against equally dead peers -- exactly the live off-season gate, on history."""
    c = clim.get(dowy, {}) if clim else {}
    base = c.get("base_in")
    fresh = c.get("fresh_in")
    season = c.get("season_in")
    return {
        "key": key, "region": region,
        "in_season": is_in_season(base, fresh),
        "abs_base_in": base, "abs_fresh_in": fresh, "abs_season_in": season,
        "abs_forecast_in": None,   # no "incoming" in a historical window
        "abs_quality": None,       # reserved: no multi-decade density/wind proxy yet
        "n_years": c.get("n_years", 0),
    }


def score_baseline(rows: list[dict], field: str = "baseline_score") -> list[dict]:
    """Attach the historical-baseline comparable score to each row IN PLACE.

    Same engine as the live global score (comparable.score_population) but with the
    trip weights (config.TRIP_BASELINE_WEIGHTS): persistent pack over transient
    fresh. Only in-season rows are ranked / counted, same as the live board."""
    scores = comparable.score_population(rows, TRIP_BASELINE_WEIGHTS)
    for r in rows:
        v = scores.get(r["key"])
        r[field] = None if v is None else round(v, 1)
    return rows


# ---------------------------------------------------------------------------
# The lead-time blend
# ---------------------------------------------------------------------------
def lead_weight(lead_days: int) -> float:
    """Weight on TODAY'S conditions at `lead_days` out -- the shared decay curve on
    the lead-time axis (1.0 at lead 0, halving every TRIP_LEAD_DECAY half-life)."""
    return decay_weight(max(0, lead_days), TRIP_LEAD_DECAY["half_life_days"])


def blend_trip_score(current: float | None, baseline: float | None,
                     lead_days: int) -> tuple[float | None, float]:
    """Blend today's comparable score with the historical baseline for a trip
    `lead_days` out. Returns (trip_score | None, current_weight).

    - Both present: w*current + (1-w)*baseline (the normal case).
    - Baseline only (off-season NOW, so no live score): pure history -- valid at any
      lead, since the baseline is exactly what a far-out trip should lean on.
    - Current only (no history for this window): today's conditions stand in ONLY
      when the trip is within ~one half-life (a forecast-ish horizon); farther out,
      with no history, we genuinely can't predict -> None.
    - Neither: None."""
    w = lead_weight(lead_days)
    if current is not None and baseline is not None:
        return round(w * current + (1.0 - w) * baseline, 1), w
    if baseline is not None:
        return round(baseline, 1), w
    if current is not None:
        near = lead_days <= TRIP_LEAD_DECAY["half_life_days"]
        return (round(current, 1) if near else None), w
    return None, w


def roster_baseline_rows(target: date, keys: list[str],
                         meta_by_key: dict[str, dict],
                         clim_by_station: dict[str, dict]) -> list[dict]:
    """Historical-baseline rows for the whole roster on `target`'s calendar date.

    `meta_by_key[key]` supplies {station, wy_start, region}; `clim_by_station` holds
    each station's precomputed climatology (resorts sharing a station share it). Each
    mountain resolves the target to its OWN day-of-water-year, so one calendar date
    is peak-winter for a Northern resort and deep off-season for a Southern one --
    exactly as it should be. Ranked in place with the trip weights (score_baseline)."""
    rows = []
    for key in keys:
        mk = meta_by_key[key]
        clim = clim_by_station.get(mk["station"], {})
        dowy = target_dowy(target, mk["wy_start"])
        rows.append(baseline_row(clim, dowy, key, mk["region"]))
    return score_baseline(rows)


def rank_trip(rows: list[dict]) -> list[dict]:
    """Sort trip rows by trip_score desc, nulls last -- the leaderboard order (mirrors
    api.all_scores). Stable within ties, so a downstream regional tie-break can layer
    on top the same way the live board does."""
    return sorted(rows, key=lambda r: (r.get("trip_score") is None,
                                       -(r.get("trip_score") or 0.0)))
