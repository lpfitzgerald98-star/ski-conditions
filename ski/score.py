"""Overall mountain score: blend the sub-scores under a selectable profile.

Every sub-score is normalized to 0-100 (mostly they already are -- they're
percentiles). The forecast sub-score is two-sided around NEUTRAL (50): incoming
snow boosts, an incoming thaw (rain / sustained warmth) penalizes, and a dry,
benign forecast drops out entirely (None) so it neither drags nor props up.

The blend weights come from config.SCORE_PROFILES -- "weekend" leans on current
conditions + forecast, "season" leans on the whole winter, etc. Weights are
normalized over whichever sub-scores are actually available. The non-forecast
sub-scores blend through a strict power mean; the forecast applies afterwards as
an additive delta around NEUTRAL (see overall_score).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import (
    CONDITIONS,
    COVER_GATE,
    DEPTH_SCORE_CURVE,
    DYNAMIC_WEIGHTS,
    FORECAST_THAW,
    FRESH_SCORE_CURVE,
    IN_SEASON_GATE,
    OFF_SEASON,
    OVERALL_GRADE_THRESHOLDS,
    POWDER_SCORE_CURVE,
    PRECIP_PHASE,
    STALE_UNKNOWN_COVER_CAP,
    REFREEZE,
    SCORE_BLEND_EXPONENT,
    SCORE_PROFILES,
    SKI_BASE_MAX,
    SKI_POWDER_MAX,
    SKI_POWDER_WEIGHTS,
    SKI_QUALITY,
    SKIABILITY_GRADE_THRESHOLDS,
)
from ski.grading import letter_grade

NEUTRAL = 50.0


# --- weather quality (0-100) -----------------------------------------------
def temp_score(temp_f: float | None) -> float | None:
    if temp_f is None:
        return None
    lo, hi = CONDITIONS["ideal_temp_f"]
    if lo <= temp_f <= hi:
        return 100.0
    if temp_f > hi:                       # warming toward slush
        z = CONDITIONS["temp_warm_zero_f"]
        return max(0.0, 100.0 * (z - temp_f) / (z - hi))
    cf, cs = CONDITIONS["temp_cold_floor_f"], CONDITIONS["temp_cold_score"]
    if temp_f <= cf:                      # deep cold: unpleasant but skiable
        return cs
    return cs + (100.0 - cs) * (temp_f - cf) / (lo - cf)


def wind_score(wind_mph: float | None) -> float | None:
    if wind_mph is None:
        return None
    z = CONDITIONS["wind_zero_mph"]
    return max(0.0, 100.0 * (z - wind_mph) / z)


def sky_score(sky_pct: float | None) -> float | None:
    if sky_pct is None:
        return None
    return max(0.0, 100.0 - sky_pct * 0.5)   # clear=100, overcast=50 (mild factor)


def weather_quality(temp_f, wind_mph, sky_pct) -> float | None:
    mix = CONDITIONS["weather_mix"]
    parts = [
        (mix["temp"], temp_score(temp_f)),
        (mix["wind"], wind_score(wind_mph)),
        (mix["sky"], sky_score(sky_pct)),
    ]
    parts = [(w, s) for w, s in parts if s is not None]
    if not parts:
        return None
    return sum(w * s for w, s in parts) / sum(w for w, _ in parts)


# --- absolute anchors (real inches, cross-mountain comparable) --------------
def piecewise(x: float, points: list[tuple[float, float]]) -> float:
    """Linear interpolation through (x, score) knots, clamped at both ends."""
    if x <= points[0][0]:
        return float(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(points[-1][1])


def depth_score(depth_inches: float | None) -> float | None:
    """Absolute base-depth quality: thin cover is thin cover anywhere."""
    if depth_inches is None:
        return None
    return piecewise(max(0.0, depth_inches), DEPTH_SCORE_CURVE)


def fresh_score(fresh_7d_inches: float | None) -> float | None:
    """Absolute recent-snow quality over the trailing week."""
    if fresh_7d_inches is None:
        return None
    return piecewise(max(0.0, fresh_7d_inches), FRESH_SCORE_CURVE)


def is_in_season(cover_depth_inches: float | None,
                 fresh_7d_inches: float | None) -> bool | None:
    """Is there enough absolute snow here to ski at all?

    Returns True (skiable), False (off-season / insufficient conditions), or None
    (unknown -- no cover reading and no snowfall reading, so we have evidence of
    neither snow nor its absence, and must not invent either).

    This is the companion to `cover_factor`: the gate caps a score, this decides
    whether the score means anything. A within-region percentile has no idea the
    whole region is bare, so without this a July mountain ranks A+ against equally
    dead neighbours (see config.IN_SEASON_GATE).

    Purely a function of measured conditions -- never the calendar. A snowless
    January week is off-season and a July week at Mt Hutt is not.
    """
    depth_ok = cover_depth_inches is not None and \
        cover_depth_inches >= IN_SEASON_GATE["min_depth_in"]
    fresh_ok = fresh_7d_inches is not None and \
        fresh_7d_inches >= IN_SEASON_GATE["min_fresh_7d_in"]
    if depth_ok or fresh_ok:
        return True
    if cover_depth_inches is None and fresh_7d_inches is None:
        return None
    return False


def apply_off_season_cap(value: float | None, in_season: bool | None) -> float | None:
    """Clamp an overall score that has no skiable conditions behind it.

    `min`, not assignment: a mountain already scoring below the cap keeps its
    (lower) value, so the off-season tail still sorts sensibly on the leaderboard.
    Unknown (None) is left alone -- we don't punish a quiet station.
    """
    if value is None or in_season is not False:
        return value
    return min(value, OFF_SEASON["overall_cap"])


def apply_stale_cap(value: float | None, stale: bool,
                    cover_known: bool) -> float | None:
    """Cap an overall from a station that has gone silent and shows no cover.

    Belt-and-suspenders alongside `apply_off_season_cap`: when the station is
    stale (no observation of any kind in DATA_STALE_DAYS) AND we have no current
    cover reading, a high overall would be riding a frozen season-to-date
    percentile with nothing current behind it. `min`, not assignment: an already
    lower score keeps its value and still sorts sensibly.

    A station with a fresh cover reading is never stale-capped -- if we know the
    base, the season percentile is not the only evidence. Verified to change no
    live grade in the current DB; it exists to keep it that way if a source dies
    mid-season (see config.DATA_STALE_DAYS)."""
    if value is None or not stale or cover_known:
        return value
    return min(value, STALE_UNKNOWN_COVER_CAP)


def cover_factor(effective_depth_inches: float | None) -> float:
    """Multiplier on the overall score from absolute cover (see config.COVER_GATE).

    1.0 with a deep base, sliding to `floor` at zero cover. None (no depth and no
    proxy available) -> 1.0, i.e. no gate rather than a fake one."""
    if effective_depth_inches is None:
        return 1.0
    floor = COVER_GATE["floor"]
    ds = piecewise(max(0.0, effective_depth_inches), DEPTH_SCORE_CURVE)
    return floor + (1.0 - floor) * ds / 100.0


# --- absolute skiability (the honest "how good is the skiing right now") -----
@dataclass
class Skiability:
    value: float | None       # 0-100, absolute (not a percentile / rank)
    grade: str                # letter from SKIABILITY_GRADE_THRESHOLDS
    base_pts: float = 0.0     # contribution breakdown, for the card / debugging
    powder_pts: float = 0.0
    powder_in: float = 0.0    # effective (recency+horizon weighted) powder inches
    quality_factor: float = 1.0


def effective_powder_in(fresh_recent_in: float | None,
                        fresh_7d_in: float | None,
                        forecast_72h_in: float | None) -> float:
    """Fold fresh + incoming snow into one recency/horizon-weighted inches figure.

    Snow already down this instant counts most (`recent`, the ~72h window); the
    rest of the trailing week is older and discounted (`week`); imminent forecast
    powder is nearly as good as on the ground (`forecast`). Missing inputs count
    as zero here (unlike a percentile pool) -- no forecast really is no incoming
    snow, not "unknown", for the purpose of how good today skis."""
    w = SKI_POWDER_WEIGHTS
    recent = max(0.0, fresh_recent_in or 0.0)
    week_only = max(0.0, (fresh_7d_in or 0.0) - recent)   # days ~3-7, older snow
    fc = max(0.0, forecast_72h_in or 0.0)
    return w["recent"] * recent + w["week"] * week_only + w["forecast"] * fc


def skiability_quality_factor(weather_q: float | None, refreeze: float = 0.0,
                              thaw: float = 0.0) -> float:
    """Multiplier in [floor, 1.0] from surface/weather quality.

    Punitive by design: rain-on-snow, a refrozen crust, or an incoming thaw make
    even a deep base ski badly, so they scale the score down. A missing weather
    read is neutral (not a penalty)."""
    q = SKI_QUALITY
    factor = 1.0
    if weather_q is not None:
        factor *= (1.0 - q["weather_span"]) + q["weather_span"] * (max(0.0, min(100.0, weather_q)) / 100.0)
    factor *= 1.0 - q["refreeze_penalty"] * max(0.0, min(1.0, refreeze))
    factor *= 1.0 - q["thaw_penalty"] * max(0.0, min(1.0, thaw))
    return max(q["floor"], min(1.0, factor))


def skiability_score(
    base_depth_in: float | None,
    fresh_recent_in: float | None,
    fresh_7d_in: float | None,
    forecast_72h_in: float | None,
    weather_q: float | None = None,
    refreeze: float = 0.0,
    thaw: float = 0.0,
) -> Skiability:
    """Absolute "how good is the skiing here right now", 0-100 + letter.

    base (enabler, saturating) + powder (fresh + incoming, diminishing returns),
    then scaled by a punitive quality factor. All ABSOLUTE inches -- the same
    number means the same thing at every mountain, so it can be the headline that
    governs the grade in both directions (see config.SKIABILITY_GRADE_THRESHOLDS).

    Returns value=None (grade "N/A") only when there's no base reading AND no
    snow signal of any kind -- nothing to judge."""
    have_base = base_depth_in is not None
    powder_in = effective_powder_in(fresh_recent_in, fresh_7d_in, forecast_72h_in)
    if not have_base and powder_in <= 0.0 \
            and fresh_7d_in is None and forecast_72h_in is None:
        return Skiability(None, "N/A")
    base_pts = SKI_BASE_MAX * (depth_score(base_depth_in) or 0.0) / 100.0 if have_base else 0.0
    powder_pts = SKI_POWDER_MAX * piecewise(powder_in, POWDER_SCORE_CURVE) / 100.0
    quality = skiability_quality_factor(weather_q, refreeze, thaw)
    value = max(0.0, min(100.0, (base_pts + powder_pts) * quality))
    return Skiability(value, letter_grade(value, SKIABILITY_GRADE_THRESHOLDS),
                      base_pts=base_pts, powder_pts=powder_pts,
                      powder_in=powder_in, quality_factor=quality)


# --- sub-score assembly ----------------------------------------------------
def conditions_score(
    base_percentile: float | None,
    fresh_7d_inches: float | None = None,
    weather_q: float | None = None,
) -> float | None:
    """How it skis right now: relative base (vs own history) + absolute fresh
    snow + live weather, weights renormalized over whatever's available.

    Absolute base DEPTH is deliberately NOT in here: it already caps the overall
    via the multiplicative cover gate (cover_factor), and having it in both
    places triple-counted thin cover (base_rel + base_abs + gate). The gate is
    the single absolute-depth anchor; fresh snow stays absolute because it's a
    flow, not the same stock the gate measures."""
    mix = CONDITIONS["mix"]
    parts = [
        (mix["base_rel"], base_percentile),
        (mix["fresh"], fresh_score(fresh_7d_inches)),
        (mix["weather"], weather_q),
    ]
    parts = [(w, s) for w, s in parts if s is not None]
    if not parts:
        return None
    return sum(w * s for w, s in parts) / sum(w for w, _ in parts)


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at/below lo, 1 at/above hi, linear between."""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return min(1.0, max(0.0, (x - lo) / (hi - lo)))


def thaw_index(rain_72h_in: float | None, tmax_72h_f: float | None,
               season_progress: float | None = None) -> float:
    """How hard the next 72h threaten the base, 0 (benign) .. 1 (full-on thaw).

    Rain-on-snow is the primary killer (absolute, season-blind). A warm spell
    melts too but more slowly, so warmth alone only reaches `warm_weight` of a
    full penalty -- AND that weight tapers with `season_progress`, because warmth
    is only a threat when it's out of season (see FORECAST_THAW). `None`
    season_progress means no taper (conservative, = the old behavior). None
    weather inputs contribute nothing (no forecast != bad forecast)."""
    t = FORECAST_THAW
    rain = 0.0 if rain_72h_in is None else _ramp(rain_72h_in, t["rain_zero_in"], t["rain_full_in"])
    warm = 0.0 if tmax_72h_f is None else _ramp(tmax_72h_f, t["warm_zero_f"], t["warm_full_f"])
    p = 0.0 if season_progress is None else max(0.0, min(1.0, season_progress))
    warm_weight = t["warm_weight"] * (1.0 - t["warm_taper_by_progress"] * p)
    return min(1.0, rain + warm_weight * warm)


def refreeze_index(rain_72h_in: float | None, tmax_72h_f: float | None,
                   tmin_24h_f: float | None, fresh_7d_inches: float | None) -> float:
    """Recent-crust severity, 0 (clean) .. 1 (boilerplate), from trailing actuals.

    The backward mirror of thaw: a recent melt (rain or warmth) that has since
    refrozen leaves an icy surface the forward forecast can't see. Fresh snow on
    top resurfaces it, so the penalty decays with the trailing 7-day new-snow
    total. All-None inputs -> 0 (no data != bad surface)."""
    r = REFREEZE
    rain = 0.0 if rain_72h_in is None else _ramp(rain_72h_in, r["rain_zero_in"], r["rain_full_in"])
    warm = 0.0 if tmax_72h_f is None else _ramp(tmax_72h_f, r["warm_zero_f"], r["warm_full_f"])
    thawed = max(rain, warm)                       # was there a melt event at all?
    if thawed <= 0.0:
        return 0.0
    # refroze: 1 when the recent min is well below freezing, 0 when it never froze
    froze = 1.0 if tmin_24h_f is None else 1.0 - _ramp(tmin_24h_f, r["froze_full_f"], r["froze_zero_f"])
    heal = 0.0 if fresh_7d_inches is None else _ramp(fresh_7d_inches, r["heal_zero_in"], r["heal_full_in"])
    return thawed * froze * (1.0 - heal)


def apply_refreeze(conditions: float | None, refreeze: float) -> float | None:
    """Scale a conditions sub-score down for a refrozen/crusty surface.

    Multiplicative (like the cover gate): an icy surface caps how well it skis
    right now no matter how the percentiles read, down to (1 - max_penalty)."""
    if conditions is None or refreeze <= 0.0:
        return conditions
    return conditions * (1.0 - REFREEZE["max_penalty"] * max(0.0, min(1.0, refreeze)))


def phase_adjusted_snow_in(snow_in: float, tmax_f: float | None,
                           phase: dict = PRECIP_PHASE) -> float:
    """Derate forecast snowfall for temperature: precip at 38F is rain, not
    powder, regardless of what the provider's own snow/rain split said (its
    grid point can sit below the resort's real elevation).

    Full credit at/below `snow_full_f`, zero at/above `rain_full_f`, linear
    between. Missing temp -> full credit (no reading != a warm one)."""
    if snow_in <= 0 or tmax_f is None:
        return max(0.0, snow_in)
    lo, hi = phase["snow_full_f"], phase["rain_full_f"]
    if tmax_f <= lo:
        return snow_in
    if tmax_f >= hi:
        return 0.0
    return snow_in * (1.0 - (tmax_f - lo) / (hi - lo))


def forecast_score(incoming_percentile: float | None, has_incoming_snow: bool,
                   thaw: float = 0.0) -> float | None:
    """Two-sided forecast signal around NEUTRAL (50).

    Incoming snow boosts (50 -> 100 with the storm's rank); an incoming thaw
    (rain / sustained warmth, see thaw_index) penalizes (50 -> 0), including
    netting against snow when a storm comes in wet. Dry AND benign returns None
    so the forecast drops out of the blend entirely -- a snowless-but-harmless
    weekend is judged on what's already on the ground."""
    boost = 0.5 * incoming_percentile if (has_incoming_snow and incoming_percentile is not None) else None
    penalty = NEUTRAL * thaw if thaw > 0 else None
    if boost is None and penalty is None:
        return None
    return max(0.0, min(100.0, NEUTRAL + (boost or 0.0) - (penalty or 0.0)))


# --- weight resolution -----------------------------------------------------
def dynamic_weights(season_progress: float) -> dict[str, float]:
    """Interpolate DYNAMIC_WEIGHTS by season progress (0=start, 1=end).

    Early season leans on season-history + forecast; late season leans on current
    conditions. `season_progress` comes from the mountain's own calendar, so this
    is hemisphere-agnostic.
    """
    p = max(0.0, min(1.0, season_progress))
    a, b = DYNAMIC_WEIGHTS["start"], DYNAMIC_WEIGHTS["end"]
    keys = set(a) | set(b)
    return {k: a.get(k, 0) * (1 - p) + b.get(k, 0) * p for k in keys}


def resolve_weights(profile: str, season_progress: float | None) -> dict[str, float]:
    """Weights for a profile. `dynamic` needs season_progress; the rest are fixed."""
    if profile == "dynamic":
        if season_progress is None:
            raise ValueError("the 'dynamic' profile requires season_progress")
        return dynamic_weights(season_progress)
    return dict(SCORE_PROFILES[profile])


# --- overall ---------------------------------------------------------------
@dataclass
class OverallScore:
    profile: str
    value: float | None
    grade: str
    subscores: dict = field(default_factory=dict)   # name -> 0-100 (or None)
    weights_used: dict = field(default_factory=dict)


def _power_mean(pairs: list[tuple[float, float]], exponent: float) -> float:
    """Weighted generalized mean of (value, weight) pairs.

    exponent 1 = arithmetic; <1 penalizes low values (stricter on the weakest
    component). Values are treated as >=0 (scores are 0-100).
    """
    den = sum(w for _, w in pairs)
    if exponent == 1.0:
        return sum(w * v for v, w in pairs) / den
    num = sum(w * (max(0.0, v) ** exponent) for v, w in pairs)
    return (num / den) ** (1.0 / exponent)


def overall_score(
    subscores: dict[str, float | None],
    profile: str,
    season_progress: float | None = None,
    exponent: float = SCORE_BLEND_EXPONENT,
    cover: float = 1.0,
) -> OverallScore:
    """Blend the sub-scores under `profile`, then scale by the cover gate.

    The forecast sub-score is kept OUT of the power mean and applied as an
    additive delta around NEUTRAL instead: a near-neutral forecast used to sit
    below the other components and get dragged down by the strict exponent,
    quietly penalizing mountains for merely HAVING a forecast. As a delta,
    neutral contributes exactly 0, snow boosts, thaw penalizes -- with the same
    marginal weight it had in an arithmetic blend.

    `cover` (from cover_factor) is multiplicative on purpose: thin cover caps the
    overall no matter how strong the relative percentiles are -- a hill having
    the best January in its own history still isn't the place to ski on 8".

    The letter comes from OVERALL_GRADE_THRESHOLDS, not the percentile curve:
    this value is a power-mean scaled by the cover gate, so its distribution
    sits lower than a raw percentile's and needs its own calibration."""
    weights = resolve_weights(profile, season_progress)
    core = [(subscores[name], w) for name, w in weights.items()
            if name != "forecast" and subscores.get(name) is not None]
    fc = subscores.get("forecast")
    w_fc = weights.get("forecast", 0.0)
    if not core and fc is None:
        return OverallScore(profile, None, "N/A", subscores, {})
    if core:
        val = _power_mean(core, exponent)
        if fc is not None and w_fc > 0:
            w_norm = w_fc / (w_fc + sum(w for _, w in core))
            val += w_norm * (fc - NEUTRAL)
    else:
        val = fc  # forecast is all we have
    val = max(0.0, min(100.0, val)) * cover
    used = {name: w for name, w in weights.items() if subscores.get(name) is not None}
    return OverallScore(profile, val, letter_grade(val, OVERALL_GRADE_THRESHOLDS),
                        subscores, used)
