"""Orchestration: ingest raw data (write) and compute a report (read).

Kept deliberately thin -- the interesting logic lives in `grading` (read-side)
and the source clients (write-side). This just wires a mountain key to them.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from config import (COVER_GATE, DB_PATH, FRESH_WINDOW_DAYS, IN_SEASON_GATE,
                    MOUNTAINS, SEASON_METRIC, STORM_THRESHOLDS)
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
    key: str, db_path: str = DB_PATH, as_of: date | None = None, use_network: bool = True
) -> dict:
    """Assemble all sub-scores for the mountain card.

    Returns the individual grade objects plus a `subscores` dict (0-100 each) ready
    to feed `score.overall_score(subscores, profile)`. Network failures degrade
    gracefully: forecast falls back to neutral, weather to base-depth-only.
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

    outlook = None
    if use_network:
        try:
            outlook = fetch_outlook_for_mountain(key)
        except Exception:  # noqa: BLE001 -- offline/degraded is fine, stay neutral
            outlook = None

    forecast_sub = None
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
            inc = forecast_incoming_storms(key, db_path, outlook=outlook)
            incoming = max(inc, key=lambda s: s.total_inches) if inc else None
        except Exception:  # noqa: BLE001
            pass
        has = incoming is not None and \
            incoming.total_inches >= STORM_THRESHOLDS["grade_baseline_min_inches"]
        forecast_sub = score_mod.forecast_score(
            incoming.percentile if incoming else None, has, thaw=thaw)
        weather = outlook.current
        if weather is not None:
            weather_q = score_mod.weather_quality(
                weather.temperature_f, weather.wind_mph, weather.sky_cover_pct)

    conditions_sub = score_mod.conditions_score(
        base.percentile, fresh_7d_inches=fresh, weather_q=weather_q)
    conditions_sub = score_mod.apply_refreeze(conditions_sub, refreeze)
    eff_depth = settled_cover_depth(obs, as_of or date.today())
    cover = score_mod.cover_factor(eff_depth)
    # True / False / None(unknown). Gates BOTH the overall score and the
    # within-region rank, so the two can never disagree about whether the mountain
    # is skiable. Note "in_season" in `subscores` is an unrelated legacy key -- it
    # means the trailing-30d percentile, not this gate.
    in_season = score_mod.is_in_season(eff_depth, fresh)
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
        "in_season": in_season,
        "outlook": outlook, "thaw_index": thaw if outlook is not None else None,
        "refreeze_index": refreeze if outlook is not None else None,
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


def settled_cover_depth(obs: pd.DataFrame, as_of: date) -> float | None:
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
    """
    if obs is None or obs.empty:
        return None
    recency = IN_SEASON_GATE["recency_days"]

    depth = _latest_within(obs, as_of, "snow_depth_inches", recency)
    if depth is not None:
        return depth

    swe = _latest_within(obs, as_of, "swe_inches", recency)
    if swe is not None:
        return swe * COVER_GATE["swe_to_depth_ratio"]

    recent_snowfall = fresh_snow_total(obs, as_of, window_days=30)
    if recent_snowfall is not None:
        return recent_snowfall * COVER_GATE["snowfall_settle_ratio"]

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


def forecast_incoming_storms(
    key: str, db_path: str = DB_PATH, windows_hours=(24, 72), outlook=None
) -> list[StormGrade]:
    """Grade the snow FORECAST to fall over the next 24/72h against storm history.

    Reuses the same ranking + alert logic as measured storms, so an incoming dump
    is judged on the same scale as past ones. Note the forecast measures snowfall
    while the SNOTEL baseline measures depth change (which undercounts settling),
    so forecast percentiles skew slightly high -- fine for an alert heuristic.

    `outlook` (a pre-fetched sources.outlook.Outlook) avoids a second provider
    call; when None it is fetched via the mountain's provider (NWS or Open-Meteo).
    """
    m = get_mountain(key)
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
