"""Orchestration: ingest raw data (write) and compute a report (read).

Kept deliberately thin -- the interesting logic lives in `grading` (read-side)
and the source clients (write-side). This just wires a mountain key to them.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from config import (COMPARABLE_FRESH_WINDOW_DAYS, COVER_GATE, DATA_STALE_DAYS,
                    DB_PATH, DEFAULT_BASE_OFFSET_IN, FORECAST_HORIZON_WEIGHTS,
                    FORECAST_HORIZONS_HOURS, FRESH_WINDOW_DAYS, IN_SEASON_GATE,
                    MEDIUM_RANGE, MOUNTAINS, SEASON_METRIC, STORM_THRESHOLDS)
from ski import forecast_log
from ski import score as score_mod
from ski.db import read_observations, upsert_observations
from ski.watercalendar import month_day_to_dowy, season_progress, water_year
from ski.grading import (
    BaseGrade,
    MonthGrade,
    SeasonGrade,
    StormGrade,
    default_alert_floor,
    detect_storm_events,
    grade_base,
    grade_rolling_window,
    grade_season_to_date,
    grade_storm,
    historical_window_distribution,
    percentile_rank,
    season_adjusted_floor,
)
from ski.sources import acis, bcsws, cdec, eccc, nws, openmeteo, snotel

# ---------------------------------------------------------------------------
# Historical data sources
# ---------------------------------------------------------------------------
# One registry entry per `data_source`. Adding a network/region = add a source
# client + a row here + config entries; nothing else in the pipeline changes.
#   fetch     -> (station_id) -> canonical obs frame
#   id_field  -> which MOUNTAINS key holds this source's station id
#   metric    -> default season metric (SWE-gain where SWE exists, else new_snow)
SOURCES = {
    "snotel": {"fetch": snotel.fetch_station_daily, "id_field": "snotel_station", "metric": "swe_gain"},
    "acis":   {"fetch": acis.fetch_station_daily,   "id_field": "acis_sid",       "metric": "new_snow"},
    "cdec":   {"fetch": cdec.fetch_station_daily,   "id_field": "cdec_station",    "metric": "swe_gain"},
    "eccc":   {"fetch": eccc.fetch_station_daily,   "id_field": "eccc_station",    "metric": "new_snow"},
    "bcsws":  {"fetch": bcsws.fetch_station_daily,  "id_field": "bcsws_station",   "metric": "swe_gain"},
    "openmeteo": {"fetch": openmeteo.fetch_station_daily, "id_field": "openmeteo_id", "metric": "new_snow"},
}
DEFAULT_SOURCE = "snotel"


def get_mountain(key: str) -> dict:
    try:
        return MOUNTAINS[key]
    except KeyError:
        raise KeyError(f"unknown mountain '{key}'; known: {sorted(MOUNTAINS)}") from None


def _source(m: dict) -> dict:
    src = m.get("data_source", DEFAULT_SOURCE)
    try:
        return SOURCES[src]
    except KeyError:
        raise KeyError(f"unknown data_source '{src}'; known: {sorted(SOURCES)}") from None


def mountain_station(m: dict) -> str:
    """The storage key (raw_observations.station_id) for a mountain's history.

    Each source uses its own id space (NRCS triplet, ACIS GHCN id, CDEC id, ECCC
    climate id), so keys never collide across sources."""
    return m[_source(m)["id_field"]]


def mountain_metric(m: dict) -> str:
    """Season metric for a mountain: explicit per-mountain override, else the
    source default (SWE-gain for SWE networks, new_snow for depth/snowfall ones),
    else the global default."""
    return m.get("season_metric") or _source(m).get("metric") or SEASON_METRIC


def mountain_wy_start(m: dict) -> int:
    """Month the accumulation water year begins for a mountain. Explicit
    `water_year_start_month`, else by hemisphere: Southern (latitude < 0) uses May
    so a Jun-Oct season stays in one water year; Northern uses Oct (the default)."""
    if m.get("water_year_start_month"):
        return m["water_year_start_month"]
    lat = m.get("latitude")
    return 5 if (lat is not None and lat < 0) else 10


def mountain_season_start(m: dict) -> int:
    """Day-of-water-year the season accumulation should start on for a mountain --
    its `season_window` start, else 1. Keeps the season-to-date coverage check
    honest for stations that only report in-season (e.g. on-mountain ECCC)."""
    window = m.get("season_window")
    if window and window.get("start"):
        return month_day_to_dowy(*window["start"], start_month=mountain_wy_start(m))
    return 1


def ingest_mountain(key: str, db_path: str = DB_PATH) -> int:
    """Fetch full history for the mountain's station and store raw rows.

    Dispatches on `data_source` via the SOURCES registry (SNOTEL / ACIS / CDEC /
    ECCC)."""
    m = get_mountain(key)
    df = _source(m)["fetch"](mountain_station(m))
    return upsert_observations(db_path, mountain_station(m), df)


def grade_mountain(key: str, db_path: str = DB_PATH, as_of: date | None = None) -> SeasonGrade:
    """Compute the season-to-date grade from stored raw observations."""
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    return grade_season_to_date(obs, as_of=as_of, metric=mountain_metric(m),
                                season_start_dowy=mountain_season_start(m),
                                wy_start_month=mountain_wy_start(m))


def grade_mountain_month(
    key: str, db_path: str = DB_PATH, as_of: date | None = None, window_days: int = 30
) -> MonthGrade:
    """Compute the trailing rolling-window ('hot month') grade."""
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    return grade_rolling_window(obs, as_of=as_of, window_days=window_days,
                                metric=mountain_metric(m),
                                wy_start_month=mountain_wy_start(m))


def grade_mountain_base(
    key: str, db_path: str = DB_PATH, as_of: date | None = None,
    field_name: str = "snow_depth_inches",
) -> BaseGrade:
    """Grade the current snowpack (depth by default) vs the same date in history."""
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    return grade_base(obs, as_of=as_of, field_name=field_name,
                      wy_start_month=mountain_wy_start(m))


def mountain_alert_floor(key: str, window_hours: int, season_percentile: float | None) -> float:
    """Resolve the alert floor for a mountain+window+season: per-mountain base
    (falling back to the global default) scaled by how the season is going."""
    m = get_mountain(key)
    base = default_alert_floor(window_hours, base=m.get("storm_floor_inches"))
    return season_adjusted_floor(base, season_percentile)


def this_season_storms(
    key: str, db_path: str = DB_PATH, as_of: date | None = None, top_n: int = 3
) -> list[StormGrade]:
    """Biggest storms of the current season, with alert floors adapted to how the
    season is going (lean years lower the bar)."""
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    if obs.empty:
        return []
    wy_start = mountain_wy_start(m)
    season_pct = grade_season_to_date(
        obs, as_of=as_of, metric=mountain_metric(m),
        season_start_dowy=mountain_season_start(m), wy_start_month=wy_start).percentile
    season_wy = water_year(as_of or date.today(), wy_start)
    out = []
    for wh in (24, 72):
        floor = mountain_alert_floor(key, wh, season_pct)
        out.extend(detect_storm_events(obs, wh, water_year_filter=season_wy,
                                       top_n=top_n, alert_floor=floor,
                                       wy_start_month=wy_start))
    return out


def has_nws(m: dict) -> bool:
    """Whether a mountain has an NWS grid. NWS (api.weather.gov) is US-only;
    mountains without one fall back to the global Open-Meteo forecast API for
    their forecast/weather sub-scores (see fetch_outlook_for_mountain)."""
    return bool(m.get("nws_office") and m.get("nws_grid"))


def fetch_outlook_for_mountain(key: str):
    """Provider-neutral forecast Outlook for a mountain: NWS where a grid exists
    (US), otherwise Open-Meteo's global forecast API (Canada, Southern
    Hemisphere) -- every mountain gets forecast + weather signals now, not just
    the US roster.

    Recent trailing actuals (the refreeze/crust signal) always come from
    Open-Meteo's `past_days`: it's one cheap global endpoint, so the US (NWS
    forward) path just borrows it too rather than scraping station observations.
    """
    m = get_mountain(key)
    lat, lon = m.get("latitude"), m.get("longitude")
    if has_nws(m):
        outlook = nws.fetch_outlook(m["nws_office"], *m["nws_grid"])
        if lat is not None and lon is not None:
            try:
                outlook.recent = openmeteo.fetch_recent_conditions(lat, lon)
            except Exception:  # noqa: BLE001 -- crust signal is best-effort
                pass
        return outlook
    if lat is not None and lon is not None:
        return openmeteo.fetch_forecast_outlook(lat, lon)
    return None


def fetch_nws(key: str):
    """Return (forecast_periods, active_alerts) for the mountain."""
    m = get_mountain(key)
    office, gx, gy = m["nws_office"], *m["nws_grid"]
    forecast = nws.fetch_forecast(office, gx, gy)
    alerts = nws.fetch_active_alerts(m["latitude"], m["longitude"])
    return forecast, alerts


def mountain_scorecard(
    key: str, db_path: str = DB_PATH, as_of: date | None = None,
    use_network: bool = True, retro: bool = False,
) -> dict:
    """Assemble all sub-scores for the mountain card.

    Returns the individual grade objects plus a `subscores` dict (0-100 each) ready
    to feed `score.overall_score(subscores, profile)`. Network failures degrade
    gracefully: forecast falls back to neutral, weather to base-depth-only.

    `retro` scores a PAST `as_of` with no live data: the "incoming" storm is
    reconstructed from the snow the station actually recorded in the forward window
    (what a weekend outlook really turned out to be). Thaw/weather stay absent --
    the DB stores snow, not temperature. `retro` and `use_network` are exclusive;
    retro implies offline.
    """
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    metric = mountain_metric(m)
    wy_start = mountain_wy_start(m)
    season = grade_season_to_date(obs, as_of=as_of, metric=metric,
                                  season_start_dowy=mountain_season_start(m),
                                  wy_start_month=wy_start)
    month = grade_rolling_window(obs, as_of=as_of, metric=metric, wy_start_month=wy_start)
    base = grade_base(obs, as_of=as_of, wy_start_month=wy_start)

    window = m.get("season_window")
    sp = None
    if window:
        sp = season_progress(as_of or date.today(), window["start"], window["end"])

    fresh = fresh_snow_total(obs, as_of=as_of)
    # The comparable score's "fresh" input: a shorter, more "right now" window
    # than the 7-day figure above (see config.GLOBAL_SCORE_WEIGHTS docstring).
    fresh_72h = fresh_snow_total(obs, as_of=as_of, window_days=COMPARABLE_FRESH_WINDOW_DAYS)

    outlook = None
    if use_network:
        try:
            outlook = fetch_outlook_for_mountain(key)
        except Exception:  # noqa: BLE001 -- offline/degraded is fine, stay neutral
            outlook = None

    forecast_sub = None
    forecast_72h_in = None
    per_horizon_out = None       # 24/48/72h breakout for the expandable card section
    incoming = None
    weather = None
    weather_q = None
    thaw = 0.0
    refreeze = 0.0
    if outlook is not None:
        thaw = score_mod.thaw_index(outlook.rain_72h_in, outlook.tmax_72h_f,
                                    season_progress=sp)
        rec = outlook.recent
        if rec is not None:
            refreeze = score_mod.refreeze_index(
                rec.rain_72h_in, rec.tmax_72h_f, rec.tmin_24h_f, fresh)
        try:
            # The "incoming" badge stays exactly as before -- the single biggest
            # of the 24/72h storm windows, unrelated to the horizon blend below.
            inc = forecast_incoming_storms(key, db_path, outlook=outlook, obs=obs)
            incoming = max(inc, key=lambda s: s.total_inches) if inc else None
        except Exception:  # noqa: BLE001
            pass
        # The forecast SUB-SCORE, in contrast, blends 24/48/72h (near-term
        # weighted heaviest) with a temperature-based precip-phase correction --
        # see weighted_incoming_percentile / config.FORECAST_HORIZON_WEIGHTS.
        weighted_pct, has, per_horizon = weighted_incoming_percentile(
            key, outlook, db_path, obs=obs)
        # The comparable score's "forecast" input: the absolute (not
        # percentile) 72h phase-adjusted total -- "how much more is coming",
        # in the same inches unit as the other three comparable inputs.
        forecast_72h_in = next(
            (ph["predicted_inches"] for ph in per_horizon if ph["horizon_hours"] == 72), None)
        per_horizon_out = per_horizon
        # The 4-10 day medium-range band folds in on top, at a small
        # confidence-tapered weight (see combine_forecast_percentile /
        # config.MEDIUM_RANGE) -- it can nudge the blend but never dominate it.
        mr_pct = medium_range_percentile(key, outlook.medium_range, db_path, obs=obs)
        combined_pct = combine_forecast_percentile(
            weighted_pct, mr_pct,
            outlook.medium_range.weight_factor if outlook.medium_range else 0.0)
        mr_has_snow = outlook.medium_range is not None and \
            outlook.medium_range.mid_in >= STORM_THRESHOLDS["grade_baseline_min_inches"]
        forecast_sub = score_mod.forecast_score(combined_pct, has or mr_has_snow, thaw=thaw)
        weather = outlook.current
        if weather is not None:
            weather_q = score_mod.weather_quality(
                weather.temperature_f, weather.wind_mph, weather.sky_cover_pct)
        # Best-effort log of what was predicted, for the forecast-accuracy
        # backtest (forecast_accuracy). A logging failure must never sink a
        # scorecard render.
        try:
            for ph in per_horizon:
                forecast_log.record(db_path, key, as_of or date.today(),
                                    ph["horizon_hours"], ph["predicted_inches"],
                                    ph["predicted_percentile"], ph["tmax_f"])
            if outlook.medium_range is not None:
                forecast_log.record(db_path, key, as_of or date.today(),
                                    outlook.medium_range.horizon_hours,
                                    outlook.medium_range.mid_in, mr_pct, None)
        except Exception:  # noqa: BLE001
            pass
    elif retro:
        # Historical date: the "incoming" storm is what actually fell next, read
        # from the DB. Snow only -- no thaw/weather without stored temperature.
        inc = retro_incoming_storms(key, as_of, db_path, obs=obs)
        incoming = max(inc, key=lambda s: s.total_inches) if inc else None
        has = incoming is not None and \
            incoming.total_inches >= STORM_THRESHOLDS["grade_baseline_min_inches"]
        forecast_sub = score_mod.forecast_score(
            incoming.percentile if incoming else None, has, thaw=0.0)

    conditions_sub = score_mod.conditions_score(
        base.percentile, fresh_7d_inches=fresh, weather_q=weather_q)
    conditions_sub = score_mod.apply_refreeze(conditions_sub, refreeze)
    offset = m.get("base_offset_in", DEFAULT_BASE_OFFSET_IN)
    eff_depth = settled_cover_depth(obs, as_of or date.today(), base_offset_in=offset)
    cover = score_mod.cover_factor(eff_depth)
    age = observation_age_days(obs, as_of or date.today())
    stale = age is not None and age >= DATA_STALE_DAYS
    # True / False / None(unknown). Gates BOTH the overall score and the
    # within-region rank, so the two can never disagree about whether the mountain
    # is skiable. Note "in_season" in `subscores` is an unrelated legacy key -- it
    # means the trailing-30d percentile, not this gate.
    in_season = score_mod.is_in_season(eff_depth, fresh)
    # Absolute skiability: the honest "how good is the skiing right now", from
    # inches only (settled base + recency/horizon-weighted fresh+forecast, scaled
    # by surface/weather quality). This is the headline that governs the grade in
    # both directions -- see score.skiability_score / SKIABILITY_GRADE_THRESHOLDS.
    skiability = score_mod.skiability_score(
        eff_depth, fresh_72h, fresh, forecast_72h_in,
        weather_q=weather_q, refreeze=refreeze, thaw=thaw)
    subscores = {
        "season": season.percentile,
        "in_season": month.percentile,
        "forecast": forecast_sub,
        "conditions": conditions_sub,
    }
    return {
        "season": season, "month": month, "base": base,
        "incoming": incoming, "weather": weather, "weather_quality": weather_q,
        "fresh_7d": fresh, "cover_factor": cover, "effective_depth": eff_depth,
        "in_season": in_season, "data_age_days": age, "stale": stale,
        "skiability": skiability,
        # Per-horizon 24/48/72h forecast breakout (predicted inches + percentile),
        # for the card's expandable forecast section. None off the live path.
        "forecast_horizons": per_horizon_out,
        "outlook": outlook, "thaw_index": thaw if outlook is not None else None,
        "refreeze_index": refreeze if outlook is not None else None,
        # Absolute, cross-mountain-comparable inputs for ski.comparable's
        # global/regional score -- distinct from the self-relative percentiles
        # above (see config.GLOBAL_SCORE_WEIGHTS).
        "comparable_inputs": {
            "base_in": eff_depth,
            "fresh_in": fresh_72h,
            "season_in": season_snow_equivalent_in(season),
            "forecast_in": forecast_72h_in,
        },
        "subscores": subscores, "season_progress": sp,
    }


def _latest_within(obs: pd.DataFrame, as_of: date, field: str,
                   days: int) -> float | None:
    """Most recent non-null `field` in the `days` before `as_of`, else None.

    Deliberately looser than `grade_base`'s +/-3 day date-matching. That tolerance
    exists to line a value up against the same calendar day in history; this one
    just asks "what is on the ground now", and a 4-day-old reading answers it fine.
    (Alta reported 0.0" on Jul 6; on Jul 10 the tighter window called that "no
    data" and sent the gate to a proxy that invented 100 inches of base.)
    """
    end = pd.Timestamp(as_of)
    start = end - pd.Timedelta(days=days)
    win = obs[(obs["date"] >= start) & (obs["date"] <= end)].dropna(subset=[field])
    return None if win.empty else float(win.iloc[-1][field])


def _last_reading(obs: pd.DataFrame, as_of: date,
                  field: str) -> tuple[float, pd.Timestamp] | tuple[None, None]:
    """The most recent non-null `field` at or before `as_of`, at ANY age."""
    rows = obs[obs["date"] <= pd.Timestamp(as_of)].dropna(subset=[field])
    if rows.empty:
        return None, None
    last = rows.iloc[-1]
    return float(last[field]), last["date"]


def _snowfall_since(obs: pd.DataFrame, after: pd.Timestamp, as_of: date) -> float:
    """New snow reported strictly after `after`, through `as_of`. 0.0 if silent."""
    win = obs[(obs["date"] > after) & (obs["date"] <= pd.Timestamp(as_of))]["new_snow_24hr"]
    return 0.0 if win.notna().sum() == 0 else float(win.sum(skipna=True))


def _carried_forward_cover(obs: pd.DataFrame, as_of: date) -> float | None:
    """A stale-but-known BARE reading, carried forward. See config.IN_SEASON_GATE.

    Seasonal stations (ACIS COOP, mostly) report 0.0" through the spring and then
    stop for the summer. Their last word was "no snow", and snow cannot appear
    without snowfall -- so if nothing has fallen since, the mountain is still bare.
    Without this, that known zero degrades to "unknown", the cover gate disengages,
    and a season-to-date percentile from last winter carries a bare mountain to the
    top of the leaderboard in July.

    Deliberately narrow. Only a sub-threshold reading carries: a station that went
    quiet holding a deep base may have melted out, and we genuinely don't know. Any
    reported snowfall since the reading voids it. And it expires after
    `carry_forward_days`, so a station that dies in autumn isn't called bare all
    winter.
    """
    limit = IN_SEASON_GATE["min_depth_in"]
    depth, when = _last_reading(obs, as_of, "snow_depth_inches")
    if depth is None:
        swe, when = _last_reading(obs, as_of, "swe_inches")
        depth = None if swe is None else swe * COVER_GATE["swe_to_depth_ratio"]
    if depth is None or depth >= limit:
        return None
    if (pd.Timestamp(as_of) - when).days > IN_SEASON_GATE["carry_forward_days"]:
        return None
    if _snowfall_since(obs, when, as_of) > 0:
        return None       # it snowed after that reading; cover may have grown
    return depth


def observation_age_days(obs: pd.DataFrame, as_of: date) -> int | None:
    """Days between `as_of` and the most recent observation carrying ANY usable
    field (depth / SWE / new snow). None when the station has never reported.

    This is the staleness signal `mountain_scorecard` surfaces (`data_age_days`)
    and `apply_stale_cap` gates on -- a station that has gone entirely silent, as
    opposed to one reporting a known bare reading (which the cover/in-season gates
    already handle via carry-forward)."""
    if obs is None or obs.empty:
        return None
    end = pd.Timestamp(as_of)
    got = obs[obs["date"] <= end].dropna(
        subset=["snow_depth_inches", "swe_inches", "new_snow_24hr"], how="all")
    if got.empty:
        return None
    return int((end - got["date"].max()).days)


def settled_cover_depth(obs: pd.DataFrame, as_of: date,
                        base_offset_in: float = 0.0) -> float | None:
    """Best available estimate of the settled base depth ON THE GROUND NOW.

    Every branch is a STOCK. The previous version fell back to the season-to-date
    total -- a flow that only ever grows -- so a mountain that accumulated 33" of
    water over the winter still read as 100" of base in July and the cover gate
    opened all the way for bare ground.

    Preference order, best evidence first:
      1. measured snow depth, within recency_days         (49/79 mountains)
      2. current SWE x swe_to_depth_ratio, within recency (SWE pillows: CDEC, BC-SWS)
      3. trailing-30d snowfall x snowfall_settle_ratio    (snowfall-only: ECCC, ACIS)
      4. a stale but sub-threshold reading, carried forward when no snow has
         fallen since (seasonal stations that stop reporting in summer)

    (3) is a real estimate, not a fudge: it decays as snowfall stops, which is the
    whole point. Snowfall-only stations would otherwise have NO cover gate at all,
    which is strictly more permissive than the bug we're fixing.

    None when the station reports nothing usable -- an unknown cover, not a zero.

    `base_offset_in` (a mountain's valley-station under-read correction, see
    config.DEFAULT_BASE_OFFSET_IN) lifts a POSITIVE reading only. A station reading
    0" is genuinely bare and stays bare, so the offset can never manufacture summer
    cover; the carried-forward BARE branch is likewise never lifted.
    """
    if obs is None or obs.empty:
        return None
    recency = IN_SEASON_GATE["recency_days"]

    def lift(v: float | None) -> float | None:
        return v + base_offset_in if (v is not None and v > 0) else v

    depth = _latest_within(obs, as_of, "snow_depth_inches", recency)
    if depth is not None:
        return lift(depth)

    swe = _latest_within(obs, as_of, "swe_inches", recency)
    if swe is not None:
        return lift(swe * COVER_GATE["swe_to_depth_ratio"])

    recent_snowfall = fresh_snow_total(obs, as_of, window_days=30)
    if recent_snowfall is not None:
        return lift(recent_snowfall * COVER_GATE["snowfall_settle_ratio"])

    return _carried_forward_cover(obs, as_of)


def fresh_snow_total(obs: pd.DataFrame, as_of: date | None = None,
                     window_days: int = FRESH_WINDOW_DAYS) -> float | None:
    """Total new snow (inches) over the trailing `window_days` ending `as_of`.

    Absolute inches, not a percentile -- the cross-mountain "did it snow lately"
    signal. None when the window has no usable observations (e.g. SWE-only
    stations, or the station hasn't reported recently)."""
    if obs is None or obs.empty:
        return None
    as_of = as_of or date.today()
    end = pd.Timestamp(as_of)
    start = end - pd.Timedelta(days=window_days - 1)
    win = obs[(obs["date"] >= start) & (obs["date"] <= end)]["new_snow_24hr"]
    if win.notna().sum() == 0:
        return None
    return float(win.sum(skipna=True))


def season_snow_equivalent_in(season: SeasonGrade) -> float | None:
    """Season-to-date total in a common SNOW-inches unit, for ski.comparable.

    `grade_season_to_date` cumulates whichever metric a mountain's source
    provides: `swe_gain` (water-inches, the SWE networks) or `new_snow`
    (already snow-inches, the ACIS/ECCC/Open-Meteo depth-change networks). A
    global comparable pool can't mix the two, so water-inches are converted
    via the same nominal density ratio COVER_GATE already uses to turn a SWE
    reading into a settled-depth one. Coarse (real snow density varies by
    climate -- see config.GLOBAL_SCORE_WEIGHTS's known-limitation note) but
    internally consistent, and better than silently comparing water to snow.
    """
    if season is None or season.current_value is None or math.isnan(season.current_value):
        return None
    if season.metric == "swe_gain":
        return season.current_value * COVER_GATE["swe_to_depth_ratio"]
    return season.current_value


def weighted_incoming_percentile(
    key: str, outlook, db_path: str = DB_PATH,
    horizons=FORECAST_HORIZONS_HOURS, weights: dict = FORECAST_HORIZON_WEIGHTS,
    obs: pd.DataFrame | None = None,
) -> tuple[float | None, bool, list[dict]]:
    """Blend the incoming-snow percentile across forecast horizons, near-term
    weighted heaviest (config.FORECAST_HORIZON_WEIGHTS -- forecast skill degrades
    fast past ~3 days). Each horizon's forecast total is first derated for
    temperature (score.phase_adjusted_snow_in): forecast precip at 38F is rain,
    not powder, and must not count toward an incoming storm.

    Percentile is ranked against the same STORM baseline (>= grade_baseline_
    min_inches historical windows) the measured storm letter grade uses -- ranking
    against every window including dry days saturates near 100 for any real storm
    (see STORM_THRESHOLDS's docstring), which would make the boost meaningless.

    Returns (weighted_percentile | None, has_incoming_snow, per_horizon) where
    per_horizon is one dict per horizon (for forecast_log / testing): percentile
    is None only when no horizon has both a phase-adjusted total and a historical
    baseline to rank it against (e.g. a brand-new station).

    `obs` (a pre-loaded observations frame, e.g. from `mountain_scorecard`) skips
    a second full-history DB read; when None it's read fresh, same as before.
    """
    m = get_mountain(key)
    if obs is None:
        obs = read_observations(db_path, mountain_station(m))
    baseline = STORM_THRESHOLDS["grade_baseline_min_inches"]
    acc = total_w = 0.0
    has_snow = False
    per_horizon = []
    for wh in horizons:
        raw = outlook.snow_in.get(wh, 0.0)
        tmax = (outlook.tmax_by_window or {}).get(wh)
        adj = score_mod.phase_adjusted_snow_in(raw, tmax)
        if adj >= baseline:
            has_snow = True
        window_days = max(1, wh // 24)
        dist = historical_window_distribution(obs, window_days, baseline) \
            if not obs.empty else np.array([])
        pct = percentile_rank(adj, dist) if dist.size else None
        w = weights.get(wh, 0.0)
        if pct is not None and w > 0:
            acc += w * pct
            total_w += w
        per_horizon.append({"horizon_hours": wh, "predicted_inches": adj,
                            "predicted_percentile": pct, "tmax_f": tmax})
    blended = acc / total_w if total_w > 0 else None
    return blended, has_snow, per_horizon


def medium_range_percentile(key: str, mr, db_path: str = DB_PATH,
                            obs: pd.DataFrame | None = None) -> float | None:
    """Percentile-rank a medium-range band's midpoint against this mountain's
    own history of that same window length -- the same STORM baseline the
    near-term horizons and the measured storm letter grade all use, so a
    medium-range read sits on the identical scale.

    `mr` is an outlook.MediumRangeBand or None. Returns None when there's no
    band, or no history to rank it against (a brand-new station). `obs` (a
    pre-loaded observations frame, e.g. from `mountain_scorecard`) skips a
    second full-history DB read; when None it's read fresh, same as before."""
    if mr is None:
        return None
    m = get_mountain(key)
    if obs is None:
        obs = read_observations(db_path, mountain_station(m))
    if obs.empty:
        return None
    window_days = max(1, mr.horizon_hours // 24)
    baseline = STORM_THRESHOLDS["grade_baseline_min_inches"]
    dist = historical_window_distribution(obs, window_days, baseline)
    return percentile_rank(mr.mid_in, dist) if dist.size else None


def combine_forecast_percentile(
    near_term_pct: float | None, mr_pct: float | None, mr_weight_factor: float,
    base_weight: float = MEDIUM_RANGE["weight"],
) -> float | None:
    """Fold the medium-range (4-10d) percentile into the near-term 24/48/72h
    blend at a small, confidence-tapered weight (config.MEDIUM_RANGE['weight']
    scaled by `mr_weight_factor`, which shrinks as the medium-range window
    reaches farther out -- see sources.outlook.medium_range_band). The
    near-term blend implicitly carries a weight of 1.0, so this tier can never
    outweigh it.

    None medium-range percentile leaves the near-term blend untouched (not
    dragged toward a fake neutral). A medium-range-only read (no near-term
    signal at all) falls back to it alone rather than returning None."""
    if mr_pct is None:
        return near_term_pct
    w = base_weight * max(0.0, min(1.0, mr_weight_factor))
    if near_term_pct is None:
        return mr_pct
    return (near_term_pct + mr_pct * w) / (1.0 + w)


def forecast_incoming_storms(
    key: str, db_path: str = DB_PATH, windows_hours=(24, 72), outlook=None,
    obs: pd.DataFrame | None = None,
) -> list[StormGrade]:
    """Grade the snow FORECAST to fall over the next 24/72h against storm history.

    Reuses the same ranking + alert logic as measured storms, so an incoming dump
    is judged on the same scale as past ones. Note the forecast measures snowfall
    while the SNOTEL baseline measures depth change (which undercounts settling),
    so forecast percentiles skew slightly high -- fine for an alert heuristic.

    `outlook` (a pre-fetched sources.outlook.Outlook) avoids a second provider
    call; when None it is fetched via the mountain's provider (NWS or Open-Meteo).
    `obs` (a pre-loaded observations frame, e.g. from `mountain_scorecard`) skips
    a second full-history DB read + season-percentile recompute; when None it's
    read fresh, same as before.
    """
    m = get_mountain(key)
    if obs is None:
        obs = read_observations(db_path, mountain_station(m))
    if outlook is None:
        outlook = fetch_outlook_for_mountain(key)
    if outlook is None:
        return []
    now = pd.Timestamp.now(tz="UTC")
    season_pct = (grade_season_to_date(
        obs, metric=mountain_metric(m),
        season_start_dowy=mountain_season_start(m),
        wy_start_month=mountain_wy_start(m)).percentile
        if not obs.empty else None)
    out = []
    for wh in windows_hours:
        total = outlook.snow_in.get(wh, 0.0)
        end_date = (now + pd.Timedelta(hours=wh)).date()
        floor = mountain_alert_floor(key, wh, season_pct)
        out.append(grade_storm(total, obs, wh, end_date, alert_floor=floor))
    return out


def retro_incoming_storms(
    key: str, as_of: date, db_path: str = DB_PATH, windows_hours=(24, 72),
    obs: pd.DataFrame | None = None,
) -> list[StormGrade]:
    """The RETROSPECTIVE incoming storm for a past `as_of`: instead of a live
    forecast, grade the snow the station actually recorded in the forward window
    (as_of, as_of + wh]. Same storm-grading scale as the live path, so a
    historical "good weekend" reads on the same curve as a forecast one.

    `obs` (a pre-loaded observations frame, e.g. from `mountain_scorecard`) skips
    a second full-history DB read + season-percentile recompute; when None it's
    read fresh, same as before.
    """
    m = get_mountain(key)
    if obs is None:
        obs = read_observations(db_path, mountain_station(m))
    if obs.empty:
        return []
    season_pct = grade_season_to_date(
        obs, as_of=as_of, metric=mountain_metric(m),
        season_start_dowy=mountain_season_start(m),
        wy_start_month=mountain_wy_start(m)).percentile
    start = pd.Timestamp(as_of)
    out = []
    for wh in windows_hours:
        window_days = max(1, wh // 24)
        end = start + pd.Timedelta(days=window_days)
        fwd = obs[(obs["date"] > start) & (obs["date"] <= end)]["new_snow_24hr"]
        total = float(fwd.sum(skipna=True)) if fwd.notna().any() else 0.0
        floor = mountain_alert_floor(key, wh, season_pct)
        out.append(grade_storm(total, obs, wh, end.date(), alert_floor=floor))
    return out


def forecast_accuracy(key: str, db_path: str = DB_PATH,
                      as_of: date | None = None) -> pd.DataFrame:
    """Backtest report: for every LOGGED forecast (forecast_log.record, written
    once per mountain/day/horizon by mountain_scorecard) whose horizon has fully
    elapsed, the predicted snow vs what the station actually recorded in that
    window. This is the validation loop config.FORECAST_HORIZON_WEIGHTS needs to
    be checked against over time.

    Empty until forecasts have been logged AND their horizons have elapsed -- a
    freshly-deployed station needs days to accumulate rows. Northern Hemisphere
    mountains need an in-season winter (Nov onward) before this has anything to
    say; a currently in-season Southern Hemisphere mountain can validate now.
    """
    cols = ["as_of", "horizon_hours", "predicted_inches", "actual_inches",
           "error_inches", "predicted_percentile"]
    m = get_mountain(key)
    obs = read_observations(db_path, mountain_station(m))
    log = forecast_log.read_log(db_path, mountain_key=key)
    if log.empty or obs.empty:
        return pd.DataFrame(columns=cols)

    today = as_of or date.today()
    rows = []
    for r in log.itertuples(index=False):
        made_for = pd.Timestamp(r.as_of)
        end = made_for + pd.Timedelta(hours=r.horizon_hours)
        if end.date() > today:
            continue  # horizon hasn't elapsed yet -- nothing to compare against
        fwd = obs[(obs["date"] > made_for) & (obs["date"] <= end)]["new_snow_24hr"]
        actual = float(fwd.sum(skipna=True)) if fwd.notna().any() else 0.0
        rows.append({
            "as_of": made_for.date().isoformat(),
            "horizon_hours": r.horizon_hours,
            "predicted_inches": r.predicted_inches,
            "actual_inches": actual,
            "error_inches": None if r.predicted_inches is None
                           else round(actual - r.predicted_inches, 2),
            "predicted_percentile": r.predicted_percentile,
        })
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
