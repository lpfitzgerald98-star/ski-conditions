"""The scorecard: a pure-JSON view of a mountain's condition.

This is the clean data contract between the Python scoring engine and any
frontend (a map website, a CLI printer, a JSON API). Everything here is
plain dicts / lists / numbers / strings / None -- no dataclasses, no pandas,
no datetimes -- so `json.dumps(scorecard(...))` just works.

`scorecard()` composes what `pipeline.mountain_scorecard()` already computes
(the grade objects + sub-scores) with `score.overall_score()` for every
profile, and flattens it into a shape a card can render directly.
"""

from __future__ import annotations

import math
from datetime import date

from config import (DEFAULT_PROFILE, MOUNTAINS, OVERALL_GRADE_THRESHOLDS,
                    SCORE_PROFILES)
from ski import pipeline, stability
from ski.grading import letter_grade
from ski.score import apply_off_season_cap, apply_stale_cap, overall_score


def _round(v, ndigits: int = 1):
    """Round a float for display; pass None / non-numbers through untouched.

    A NaN (e.g. a pandas sum over a data gap upstream) is treated as missing,
    not a number: `json.dumps` would otherwise emit the bare token `NaN`, which
    is invalid JSON and breaks every consumer's parser, not just this field."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return round(v, ndigits) if isinstance(v, (int, float)) else v


def _grade_json(g) -> dict | None:
    """Serialize a Season/Month/Base grade dataclass to a compact dict."""
    if g is None:
        return None
    out = {
        "grade": g.grade,
        "value": _round(g.current_value),
        "percentile": _round(g.percentile, 0),
        "units": g.units,
        "n_years": g.n_years,
        "low_confidence": g.low_confidence,
    }
    return out


def _storm_json(s) -> dict | None:
    """Serialize a StormGrade (measured or forecast) to a compact dict."""
    if s is None:
        return None
    return {
        "window_hours": s.window_hours,
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "inches": _round(s.total_inches),
        "grade": s.grade,
        "percentile": _round(s.percentile, 0),
        "alert": bool(s.alert),
    }


def _weather_json(w) -> dict | None:
    if w is None:
        return None
    return {
        "temperature_f": _round(w.temperature_f, 0),
        "wind_mph": _round(w.wind_mph, 0),
        "sky_cover_pct": _round(w.sky_cover_pct, 0),
    }


def _overall_by_profile(subscores: dict, season_progress: float | None,
                        cover: float = 1.0, in_season: bool | None = None,
                        stale: bool = False, cover_known: bool = True,
                        mountain_key: str | None = None, as_of: date | None = None,
                        stable: bool = True, db_path: str | None = None) -> dict:
    """Overall score under every applicable profile, keyed by profile name.

    `dynamic` is only included when we know the season progress (i.e. the
    mountain has a season_window configured).

    An off-season mountain is capped (config.OFF_SEASON) and its letter recomputed
    from the capped value, so the number and the grade can't tell different
    stories. The cover gate alone leaves room for an off-season "B". A stale
    station with no cover reading gets the same treatment (config.apply_stale_cap).

    `stable` (default True) routes the final value through ski.stability, a
    hysteresis band that keeps the letter from flapping between adjacent grades
    on day-to-day noise near a boundary -- see that module's docstring. Callers
    on the retro/historical path pass `stable=False`: a settled date shows the
    letter its own numbers earned, not one anchored to a "yesterday" that in a
    backfill run may not even be adjacent in real time.
    """
    profiles = (["dynamic"] if season_progress is not None else []) + list(SCORE_PROFILES)
    out = {}
    for prof in profiles:
        o = overall_score(subscores, prof, season_progress=season_progress, cover=cover)
        value = apply_off_season_cap(o.value, in_season)
        value = apply_stale_cap(value, stale, cover_known)
        if stable and mountain_key is not None and as_of is not None:
            grade = stability.stabilize(mountain_key, as_of, prof, value,
                                        OVERALL_GRADE_THRESHOLDS, db_path=db_path)
        else:
            grade = o.grade if value == o.value else letter_grade(value, OVERALL_GRADE_THRESHOLDS)
        entry = {"score": _round(value), "grade": grade}
        if prof == "dynamic" and o.weights_used:
            entry["leaning"] = max(o.weights_used, key=o.weights_used.get)
        out[prof] = entry
    return out


def scorecard(
    key: str,
    db_path: str | None = None,
    as_of: date | None = None,
    use_network: bool = True,
    default_profile: str = DEFAULT_PROFILE,
    retro: bool = False,
) -> dict:
    """Full JSON-serializable scorecard for one mountain.

    Shape (all fields always present; missing data -> null):
        mountain:   {key, name, latitude, longitude, verified}
        as_of:      ISO date
        default_profile: which profile the frontend should feature
        season_progress: 0..1 or null
        overall:    {profile: {score, grade, [leaning]}, ...}
        subscores:  {season, in_season, forecast, conditions}  (0-100 or null)
        grades:     {season, in_season, base}   compact grade dicts
        forecast:   incoming-storm dict or null
        outlook:    {provider, rain_72h_in, tmax_72h_f, thaw_index, medium_range} or null
        conditions: {base_depth, base_grade, weather:{...}, weather_quality}
        sources:    {history, forecast, weather} -- who supplied what (null =
                    unavailable), so cross-mountain composition differences are
                    visible instead of silently renormalized away
    """
    m = MOUNTAINS[key]
    as_of = as_of or date.today()
    kwargs = {} if db_path is None else {"db_path": db_path}
    card = pipeline.mountain_scorecard(key, as_of=as_of, use_network=use_network,
                                       retro=retro, **kwargs)

    sub = card["subscores"]
    base = card["base"]
    ski = card["skiability"]
    sq = card["snow_quality"]
    outlook = card.get("outlook")
    provider = outlook.provider if outlook is not None else None
    return {
        "mountain": {
            "key": key,
            "name": m["name"],
            "latitude": m.get("latitude"),
            "longitude": m.get("longitude"),
            "verified": m.get("verified", False),
        },
        "as_of": as_of.isoformat(),
        "default_profile": default_profile,
        "season_progress": _round(card["season_progress"], 2),
        "cover_factor": _round(card["cover_factor"], 2),
        # True skiable / False off-season / None unknown (see score.is_in_season)
        "in_season": card["in_season"],
        # Days since this station last reported anything usable, and whether that
        # exceeds config.DATA_STALE_DAYS -- so a quiet station is visible on the
        # card instead of being silently trusted (see pipeline.observation_age_days).
        "data_age_days": card.get("data_age_days"),
        "stale": card.get("stale", False),
        "cover_depth": _round(card["effective_depth"], 1),
        # The HEADLINE: absolute "how good is the skiing right now" (see
        # score.skiability_score). Governs the grade in both directions -- a
        # #1-ranked hill on a thin base still reads honestly here, and a great
        # day can't be buried by a crowded leaderboard. `overall` (below) is
        # kept as the self-relative "vs this mountain's own history" context.
        "skiability": {
            "score": _round(ski.value),
            "grade": ski.grade,
            "base_pts": _round(ski.base_pts),
            "powder_pts": _round(ski.powder_pts),
            "powder_in": _round(ski.powder_in),
            "quality_factor": _round(ski.quality_factor, 2),
            # New-snow water fraction (Phase 2): ~0.05 blower .. ~0.20+ heavy/wet.
            # Null when there was no meaningful recent snow to judge.
            "new_snow_density": _round(card.get("new_snow_density"), 3),
            # Wind (Phase 3): sustained recent wind (mph) and the 0..1 scour penalty
            # it applied to the fresh snow. Null/0 off the live path.
            "wind_sustained_mph": _round(card.get("wind_sustained_mph"), 0),
            "wind_scour": _round(card.get("wind_scour"), 2),
            # Buried rain/melt crust severity 0..1 (Phase 5b), pillow stations only;
            # null off pillow. Distinguishes an old buried crust from a fresh thaw.
            "buried_crust": _round(card.get("buried_crust_index"), 2),
        },
        # Explainable "how good is the surface" signal (Phase 0 SCAFFOLD): a
        # single named 0-100 number with its component breakdown, surfaced for
        # observation only -- weighted 0 in every consumer, so it governs no grade
        # yet (see config.SNOW_QUALITY_WEIGHTS / docs/snow-quality-plan.md).
        # `density`/`wind` components read null until Phases 2/3 supply them.
        "snow_quality": {
            "score": _round(sq.value),
            "components": {k: _round(v, 0) for k, v in sq.components.items()},
            "weights_used": sq.weights_used,
        },
        # Absolute inputs to ski.comparable's global/regional score -- distinct
        # from the self-relative percentiles in `grades` (see
        # config.GLOBAL_SCORE_WEIGHTS). Null components are excluded from that
        # mountain's blend, not zeroed.
        "comparable_inputs": {
            k: _round(v) for k, v in card.get("comparable_inputs", {}).items()
        },
        "overall": _overall_by_profile(sub, card["season_progress"],
                                       cover=card["cover_factor"],
                                       in_season=card["in_season"],
                                       stale=card.get("stale", False),
                                       cover_known=card["effective_depth"] is not None,
                                       mountain_key=key, as_of=as_of, stable=not retro,
                                       db_path=db_path),
        "subscores": {k: _round(v) for k, v in sub.items()},
        "grades": {
            "season": _grade_json(card["season"]),
            "in_season": _grade_json(card["month"]),
            "base": _grade_json(base),
        },
        "forecast": _storm_json(card["incoming"]),
        # Per-horizon breakout (24/48/72h) for the card's expandable forecast
        # section: the pinned `forecast` above is the single biggest window; this
        # lists all near-term horizons so the UI can drop down to the fuller view.
        # Null off the live path (retro/no-network have no multi-horizon forecast).
        "forecast_horizons": None if card.get("forecast_horizons") is None else [
            {
                "horizon_hours": ph["horizon_hours"],
                "inches": _round(ph.get("predicted_inches"), 1),
                "percentile": _round(ph.get("predicted_percentile"), 0),
                "tmax_f": _round(ph.get("tmax_f"), 0),
            }
            for ph in card["forecast_horizons"]
        ],
        "outlook": None if outlook is None else {
            "provider": provider,
            "rain_72h_in": _round(outlook.rain_72h_in, 2),
            "tmax_72h_f": _round(outlook.tmax_72h_f, 0),
            "thaw_index": _round(card.get("thaw_index"), 2),
            "refreeze_index": _round(card.get("refreeze_index"), 2),
            # ISO-8601 UTC -- when this forecast was fetched, so the UI can show
            # "forecast as of Xh ago" instead of silently trusting a stale pull.
            "forecast_as_of": outlook.fetched_at,
            # 4-10 day tier: a RANGE, not a point estimate, plus how far out the
            # source's data actually reached and how confident that makes it
            # (see ski.sources.outlook.medium_range_band). Null when the source
            # doesn't cover at least config.MEDIUM_RANGE['min_hours'] out -- an
            # explicit gap rather than a fabricated narrow band.
            "medium_range": None if outlook.medium_range is None else {
                "low_in": outlook.medium_range.low_in,
                "mid_in": outlook.medium_range.mid_in,
                "high_in": outlook.medium_range.high_in,
                "horizon_hours": outlook.medium_range.horizon_hours,
                "confidence": outlook.medium_range.confidence,
            },
        },
        "conditions": {
            "base_depth": _round(base.current_value, 0) if base else None,
            "base_grade": base.grade if base else None,
            "fresh_7d": _round(card.get("fresh_7d"), 1),
            "weather": _weather_json(card["weather"]),
            "weather_quality": _round(card["weather_quality"], 0),
        },
        "sources": {
            "history": m.get("data_source", "snotel"),
            "forecast": provider,
            "weather": provider if card["weather"] is not None else None,
        },
    }
