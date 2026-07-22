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

from config import (COMPARABLE_FRESH_WINDOW_DAYS, CONSISTENCY_MIN_YEARS,
                    COVER_GATE, PRESERVATION_FLOOR, PRESERVATION_WARM_F,
                    PRESERVATION_WARM_PENALTY, SEASON_SWE_TO_SNOWFALL_RATIO,
                    TERRAIN_STATS as _TERRAIN, TRIP_BASELINE_WEIGHTS,
                    TRIP_LEAD_DECAY, TRIP_WINDOW_DAYS)
from ski import comparable
from ski.grading import _daily_increment, _prepare
from ski.score import decay_weight, density_from_temp, density_score, is_in_season
from ski.watercalendar import day_of_water_year

DOWY_MAX = 366  # a water year with a Feb 29 reaches day 366; index 1..366 always


def density_priors(region: str | None, source: str | None) -> tuple[float | None, float]:
    """(regional density prior water fraction, network trust) for a mountain -- the
    two knobs climatology() shrinks the measured density with. Unknown region -> no
    prior (pure measured); unknown source -> the default trust."""
    from config import (DENSITY_SOURCE_TRUST, DENSITY_TRUST_DEFAULT,
                        REGION_DENSITY_PRIOR)
    prior = REGION_DENSITY_PRIOR.get(region)
    trust = DENSITY_SOURCE_TRUST.get(source, DENSITY_TRUST_DEFAULT)
    return prior, trust


def preservation_priors(region: str | None, source: str | None) -> tuple[float | None, float]:
    """(regional preservation prior 0-100, network trust) for a mountain. Only
    temperature networks can MEASURE melt exposure; the rest use the prior outright."""
    from config import (PRESERVATION_SOURCE_TRUST, PRESERVATION_TRUST_DEFAULT,
                        REGION_PRESERVATION_PRIOR)
    prior = REGION_PRESERVATION_PRIOR.get(region)
    trust = PRESERVATION_SOURCE_TRUST.get(source, PRESERVATION_TRUST_DEFAULT)
    return prior, trust


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
                metric: str, window_days: int = TRIP_WINDOW_DAYS,
                density_prior: float | None = None, density_trust: float = 1.0,
                preservation_prior: float | None = None,
                preservation_trust: float = 0.0) -> dict[int, dict]:
    """Per day-of-water-year (1..366): a mountain's typical conditions across all
    years, as the same absolute inputs ski.comparable ranks live.

    Returns {dowy: {base_in, fresh_in, season_in, quality, water_fraction,
    preservation, consistency, n_years}} (empty when there's no usable history).
    Smoothed over +/- `window_days`:
      base_in    median settled base depth (snow depth, or SWE x ratio when a
                 pillow station reports no depth) -- the persistent pack.
      fresh_in   typical trailing-window fresh: mean daily new snow x the comparable
                 fresh window, so it's the same unit/scale the live `abs_fresh_in`
                 carries -- "does this place reliably snow this week of the year".
      season_in  median cumulative season-to-date accumulation by that dowy, in snow
                 inches (SWE-gain metrics converted with the same ratio the cover
                 gate uses), so SWE and depth-change mountains compare on one scale.
      quality    climatological new-snow QUALITY (0-100), the multi-decade analog of
                 the live density read, mapped through DENSITY_SCORE_CURVE (light/dry
                 climates high, maritime cement low). The MEASURED water fraction --
                 Tier 1 (SWE stations): positive SWE gain / depth gain; Tier 2
                 (depth-only networks): snowfall-weighted air temp -> density_from_temp
                 -- is shrunk toward `density_prior` by `density_trust` (see
                 config.REGION_DENSITY_PRIOR / DENSITY_SOURCE_TRUST), because automated
                 station density is biased in confirmable ways. With no measured read
                 AND no prior it's None (drops out of the ranking).
      preservation  how well the pack holds between storms (0-100). Measured from the
                 climatological fraction of season days above PRESERVATION_WARM_F where
                 temperature exists, shrunk toward `preservation_prior` by
                 `preservation_trust`; else the regional prior outright. None when
                 neither is available.
      n_years    distinct water years contributing near that dowy -- the confidence
                 signal (see config.LOW_CONFIDENCE_YEARS).

    `density_prior`/`preservation_prior` are the region's literature priors and
    `*_trust` the network's measurement reliability; callers look both up per mountain
    (see build_snapshot._build_climatology). Defaults (no prior, full trust) reproduce
    the pure-measured behavior, so existing callers/tests are unchanged."""
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
        # cumulative SWE -> fresh snowfall inches (see config), so SWE stations land
        # on the same "inches that fell" scale reported-snowfall stations already use.
        season_in = season_in * SEASON_SWE_TO_SNOWFALL_RATIO

    # consistency: inter-year reliability of season-to-date (feast-or-famine penalty).
    # Coefficient of variation across years at each dowy; low CV = reliable, high = the
    # Sierra's boom/bust. Built on the cumulative-season AGGREGATE, which compares fairly
    # across station vs reanalysis networks -- a daily powder-day metric would not (ERA5
    # smooths daily peaks). Gated on CONSISTENCY_MIN_YEARS in the output loop.
    season_mean = cum.groupby(df["dowy"]).mean()
    season_std = cum.groupby(df["dowy"]).std()
    cv = _centered_smooth(season_std / season_mean.where(season_mean > 0), window_days, "mean")
    consistency = 100.0 * (1.0 - cv.clip(lower=0.0, upper=1.0))

    # quality: climatological new-snow density -> a per-dowy water fraction, mapped to
    # a 0-100 quality below. Clamped to the same physical band as the live measured
    # read (pipeline.measured_new_snow_density). Two tiers, same order as live scoring.
    density_wf = None
    if df["swe_inches"].notna().any():
        # Tier 1: measured. Smoothed mean positive SWE gain / smoothed mean depth gain,
        # over the SAME day population for both channels. Depth sensors (ultrasonic)
        # drop out far more often than SWE pillows -- especially mid-storm, exactly
        # when it matters most -- so averaging each channel over its own non-null days
        # (rather than a shared mask) silently compares different day-counts and
        # inflates the apparent water fraction. A small per-day floor on depth also
        # excludes sensor-noise/trace days (a hair of depth jitter paired with real
        # SWE from wind-blown deposition) that would otherwise skew the ratio.
        swe_gain_raw = _daily_increment(df, "swe_gain").clip(lower=0)
        depth_gain_raw = df["new_snow_24hr"].clip(lower=0)
        paired = swe_gain_raw.notna() & depth_gain_raw.notna() & (depth_gain_raw > 0.3)
        swe_rate = _centered_smooth(
            swe_gain_raw.where(paired).groupby(df["dowy"]).mean(), window_days, "mean")
        depth_rate = _centered_smooth(
            depth_gain_raw.where(paired).groupby(df["dowy"]).mean(), window_days, "mean")
        # water fraction only where there's real snowfall to weigh (a near-zero depth
        # rate is noise, not "infinitely wet"); elsewhere NaN -> quality None below.
        density_wf = (swe_rate / depth_rate.where(depth_rate > 0.05)).clip(lower=0.02, upper=0.40)
    elif "mean_temp_f" in df.columns and df["mean_temp_f"].notna().any():
        # Tier 2: derived. The typical SNOWFALL-WEIGHTED air temperature at that dowy
        # (temperature on the days it actually snowed, matching live snow_temp_72h_f),
        # mapped to a water fraction by score.density_from_temp. Weighting by new snow
        # keeps warm dry days from dragging the storm-temperature estimate up.
        snow_w = df["new_snow_24hr"].clip(lower=0)
        valid = df["mean_temp_f"].notna() & (snow_w > 0)
        num = _centered_smooth(
            (df["mean_temp_f"] * snow_w).where(valid).groupby(df["dowy"]).sum(),
            window_days, "mean")
        den = _centered_smooth(
            snow_w.where(valid).groupby(df["dowy"]).sum(), window_days, "mean")
        snow_temp = num / den.where(den > 0)
        wf = snow_temp.map(lambda t: density_from_temp(t) if pd.notna(t) else np.nan)
        density_wf = pd.to_numeric(wf, errors="coerce").clip(lower=0.02, upper=0.40)

    # Shrink the measured water fraction toward the regional literature prior. Gaps in
    # the measured series (and networks with no usable read at all -- CDEC/BC-SWS at
    # trust 0) fall to the prior; where both exist we blend by trust. With no prior we
    # keep the pure measured read (legacy behavior).
    density_wf = _shrink_to_prior(density_wf, density_prior, density_trust)

    # Preservation: climatological midwinter melt exposure -> how well the pack holds.
    # Measured only where temperature exists (fraction of season days above freezing,
    # per dowy so the seasonal warming arc shows); shrunk toward the regional prior.
    preservation = None
    if "mean_temp_f" in df.columns and df["mean_temp_f"].notna().any():
        t = df["mean_temp_f"]
        in_season = df["dowy"] >= season_start_dowy
        warm = ((t > PRESERVATION_WARM_F) & in_season & t.notna())
        known = (in_season & t.notna())
        warm_frac = _centered_smooth(warm.groupby(df["dowy"]).sum(), window_days, "mean") / \
            _centered_smooth(known.groupby(df["dowy"]).sum(), window_days, "mean").where(lambda s: s > 0)
        preservation = (100.0 * (1.0 - PRESERVATION_WARM_PENALTY * warm_frac)).clip(
            lower=PRESERVATION_FLOOR, upper=100.0)
    preservation = _shrink_to_prior(preservation, preservation_prior, preservation_trust)

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
        wf = None if density_wf is None else density_wf.get(d)
        q = None if wf is None or np.isnan(wf) else density_score(float(wf))
        pv = None if preservation is None else preservation.get(d)
        ny = n_years.get(d)
        ny_int = 0 if ny is None or np.isnan(ny) else int(round(ny))
        cons = consistency.get(d)
        cons = None if (cons is None or np.isnan(cons) or ny_int < CONSISTENCY_MIN_YEARS) else float(cons)
        out[d] = {
            "base_in": None if b is None or np.isnan(b) else float(b),
            "fresh_in": None if f is None or np.isnan(f) else float(max(0.0, f)),
            "season_in": None if s is None or np.isnan(s) else float(max(0.0, s)),
            "quality": None if q is None else float(q),
            # The raw water fraction behind `quality` (post-prior-shrinkage), so
            # commentary can quote a real snow-to-liquid figure instead of just the
            # 0-100 score -- e.g. "~9% water content" is a verifiable technical claim,
            # "quality 84" on its own is not.
            "water_fraction": None if wf is None or np.isnan(wf) else float(wf),
            "preservation": None if pv is None or np.isnan(pv) else float(pv),
            "consistency": cons,
            "n_years": ny_int,
        }
    return out


def _shrink_to_prior(measured: "pd.Series | None", prior: float | None,
                     trust: float) -> "pd.Series | None":
    """Shrink a per-dowy measured series toward a scalar regional `prior`.

    - No prior: return the measured series unchanged (legacy pure-measured behavior).
    - No measured series (network has no usable read): a flat prior series, or None if
      there's no prior either.
    - Both: fill measured gaps (NaN dowy) with the prior, then blend
      `trust*measured + (1-trust)*prior`. At trust 0 this is the pure prior (so a
      known-broken network is overridden entirely); at trust 1, pure measurement."""
    full = range(1, DOWY_MAX + 1)
    if prior is None:
        return measured
    if measured is None:
        return pd.Series(float(prior), index=full)
    filled = measured.reindex(full).where(measured.reindex(full).notna(), float(prior))
    return trust * filled + (1.0 - trust) * float(prior)


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
        # Climatological new-snow density: the persistent quality signal a far-out
        # trip CAN lean on (Tier 1 SWE-measured or Tier 2 temperature-derived, shrunk
        # to a regional literature prior -- see trip.climatology). None only when a
        # station has neither read nor prior -> drops out, like a missing live density.
        "abs_quality": c.get("quality"),
        # Climatological preservation: how well the pack holds between storms (midwinter
        # melt exposure). Another axis of "what skiers want" beyond fresh density.
        "abs_preservation": c.get("preservation"),
        # Climatological consistency: inter-year reliability (feast-or-famine penalty).
        "abs_consistency": c.get("consistency"),
        # Mountain character (config.TERRAIN_STATS): STATIC facts about the resort,
        # not conditions -- doesn't depend on the climatology dowy at all, unlike
        # every other abs_* field above, so it's the same value at every trip date.
        "abs_vertical_ft": _TERRAIN.get(key, {}).get("vertical_drop_ft"),
        "abs_acres": _TERRAIN.get(key, {}).get("skiable_acres"),
        "abs_pct_advanced_expert": _TERRAIN.get(key, {}).get("pct_advanced_expert"),
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
