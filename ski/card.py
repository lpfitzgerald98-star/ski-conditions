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

from datetime import date

from config import (DEFAULT_PROFILE, MOUNTAINS, OVERALL_GRADE_THRESHOLDS,
                    SCORE_PROFILES)
from ski import pipeline
from ski.grading import letter_grade
from ski.score import apply_off_season_cap, overall_score


def _round(v, ndigits: int = 1):
    """Round a float for display; pass None / non-numbers through untouched."""
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
                        cover: float = 1.0, in_season: bool | None = None) -> dict:
    """Overall score under every applicable profile, keyed by profile name.

    `dynamic` is only included when we know the season progress (i.e. the
    mountain has a season_window configured).

    An off-season mountain is capped (config.OFF_SEASON) and its letter recomputed
    from the capped value, so the number and the grade can't tell different
    stories. The cover gate alone leaves room for an off-season "B".
    """
    profiles = (["dynamic"] if season_progress is not None else []) + list(SCORE_PROFILES)
    out = {}
    for prof in profiles:
        o = overall_score(subscores, prof, season_progress=season_progress, cover=cover)
        value = apply_off_season_cap(o.value, in_season)
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
        outlook:    {provider, rain_72h_in, tmax_72h_f, thaw_index} or null
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
        "cover_depth": _round(card["effective_depth"], 1),
        "overall": _overall_by_profile(sub, card["season_progress"],
                                       cover=card["cover_factor"],
                                       in_season=card["in_season"]),
        "subscores": {k: _round(v) for k, v in sub.items()},
        "grades": {
            "season": _grade_json(card["season"]),
            "in_season": _grade_json(card["month"]),
            "base": _grade_json(base),
        },
        "forecast": _storm_json(card["incoming"]),
        "outlook": None if outlook is None else {
            "provider": provider,
            "rain_72h_in": _round(outlook.rain_72h_in, 2),
            "tmax_72h_f": _round(outlook.tmax_72h_f, 0),
            "thaw_index": _round(card.get("thaw_index"), 2),
            "refreeze_index": _round(card.get("refreeze_index"), 2),
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
