"""Open-Meteo client -- a GLOBAL historical snow source (ERA5 reanalysis).

There is no SNOTEL-style station network in the Southern Hemisphere (or most of
the world), so for those resorts we fall back to Open-Meteo's free historical
archive API, which serves daily snowfall + snow depth anywhere on Earth back to
1940 from the ERA5 reanalysis. No API key.

Reanalysis is gridded (~25 km) and smooths mountain peaks, so absolute depths run
low -- but grading is percentile-vs-this-location's-own-history, which only needs
internal consistency, and ERA5 provides a long, uniform record for that. A
mountain opts in with `data_source: "openmeteo"` and `openmeteo_id: "lat,lon"`.

Elements (daily):
  snowfall_sum   (cm)  -> new_snow_24hr   (grade on the "new_snow" season metric)
  snow_depth_max (m)   -> snow_depth_inches
No SWE, so `swe_inches` is NaN.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import MEDIUM_RANGE, WIND
from ski.sources.outlook import CurrentWeather, Outlook, Recent, medium_range_band
from ski.sources import http

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "ski-conditions-app (historical grading)"
START_DATE = "1980-01-01"
CM_TO_IN = 1.0 / 2.54
M_TO_IN = 39.3701


def fetch_station_daily(loc: str, timeout: int = 90, since=None,
                        snow_model: dict | None = None) -> pd.DataFrame:
    """Daily snow history for a 'lat,lon' location string, as the canonical obs
    frame (snowfall -> new_snow_24hr, depth -> snow_depth_inches, swe NaN).

    `since` (a `date`): incremental ingest -- fetch only from that day forward
    instead of back to 1980. This is the biggest single lever on Action time:
    54 of the roster's stations are Open-Meteo, and a full 1980-> pull for each
    is what draws the archive API's 429s. The tail is a handful of days.

    `snow_model` (a MOUNTAINS entry's `snow_model` dict plus `reset_month`):
    replace the reanalysis depth + snowfall with our own degree-day snowpack
    (see snowpack_model / config.SNOW_MODEL). Also pulls precipitation_sum, and
    samples at `elevation_m` when given (Open-Meteo downscales temperature to it).
    The caller must start `since` on a water-year boundary (the model's state
    resets there) -- pipeline.ingest_mountain does.
    """
    lat, lon = (float(x) for x in loc.split(","))
    start_date = since.isoformat() if since is not None else START_DATE
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start_date,
        "end_date": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "daily": "snowfall_sum,snow_depth_max,temperature_2m_mean",
        "temperature_unit": "fahrenheit",   # else the archive defaults to Celsius
        "timezone": "auto",
    }
    if snow_model is not None:
        params["daily"] += ",precipitation_sum"
        if snow_model.get("elevation_m") is not None:
            params["elevation"] = snow_model["elevation_m"]
    resp = http.get(ARCHIVE, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    df = parse_archive(resp.json())
    if snow_model is not None:
        df = apply_snow_model(df, resp.json(), snow_model)
    return df


def snowpack_model(precip_mm, temp_c, dates, precip_multiplier: float = 1.0,
                   reset_month: int | None = None, params: dict | None = None):
    """Daily degree-day snowpack: (depth_cm, new_snow_cm) arrays.

    Per day: split precip into snow/rain on the mean temp (all snow <= t_snow_c,
    all rain >= t_rain_c, linear between); settle the existing pack's bulk density
    toward rho_max, mix the new snow in at rho_new; melt ddf mm per degC above
    t_melt_c plus a rain-on-snow term; depth = SWE / density. State resets to zero
    on day 1 of `reset_month` (the water-year start), so a fetch beginning on that
    boundary reproduces a full-history run exactly. Missing precip counts as dry;
    a missing temp keeps the day's snow but melts nothing. See config.SNOW_MODEL.
    """
    from config import SNOW_MODEL
    p = {**SNOW_MODEL, **(params or {})}
    P = np.nan_to_num(np.asarray(precip_mm, dtype=float), nan=0.0) * precip_multiplier
    T = np.asarray(temp_c, dtype=float)
    dates = pd.to_datetime(pd.Series(dates))
    resets = ((dates.dt.month == reset_month) & (dates.dt.day == 1)).to_numpy()         if reset_month else np.zeros(len(P), dtype=bool)
    span = p["t_rain_c"] - p["t_snow_c"]
    depth = np.zeros(len(P))
    new_snow = np.zeros(len(P))
    swe, rho = 0.0, p["rho_new"]
    for i in range(len(P)):
        if resets[i]:
            swe, rho = 0.0, p["rho_new"]
        t = T[i]
        known = not np.isnan(t)
        frac = 1.0 if not known or t <= p["t_snow_c"] else             0.0 if t >= p["t_rain_c"] else (p["t_rain_c"] - t) / span
        snow, rain = P[i] * frac, P[i] * (1.0 - frac)
        if swe > 0:
            rho += (p["rho_max"] - rho) * p["settle_per_day"]
        if snow > 0:
            rho = (swe * rho + snow * p["rho_new"]) / (swe + snow)
            swe += snow
        if known:
            melt = max(0.0, t - p["t_melt_c"]) * p["ddf_mm_per_c_day"]                 + rain * max(t, 0.0) * p["rain_melt_mm_per_mm_c"]
            swe = max(0.0, swe - melt)
        if swe == 0.0:
            rho = p["rho_new"]
        depth[i] = swe / rho * 100.0          # mm water / (kg/m3) -> m * 1000 / 10 -> cm
        new_snow[i] = snow * p["snow_cm_per_mm"]
    return depth, new_snow


def apply_snow_model(df: pd.DataFrame, payload: dict, snow_model: dict) -> pd.DataFrame:
    """Swap the reanalysis depth/snowfall columns of a parsed archive frame for
    snowpack_model's. `payload` is the raw response (for precipitation_sum)."""
    daily = payload.get("daily", {})
    raw = pd.DataFrame({
        "date": pd.to_datetime(pd.Series(daily.get("time", [])), errors="coerce"),
        "precip_mm": pd.to_numeric(pd.Series(daily.get("precipitation_sum", []),
                                             dtype="object"), errors="coerce"),
    }).dropna(subset=["date"])
    df = df.merge(raw, on="date", how="left")
    temp_c = (df["mean_temp_f"] - 32.0) * 5.0 / 9.0
    depth_cm, snow_cm = snowpack_model(
        df["precip_mm"], temp_c, df["date"],
        precip_multiplier=snow_model.get("precip_multiplier", 1.0),
        reset_month=snow_model.get("reset_month"))
    df["snow_depth_inches"] = depth_cm * CM_TO_IN
    df["new_snow_24hr"] = snow_cm * CM_TO_IN
    return df.drop(columns="precip_mm")


def parse_archive(payload: dict) -> pd.DataFrame:
    """Parse an Open-Meteo archive response into the canonical obs frame."""
    daily = payload.get("daily", {})
    dates = daily.get("time", [])
    snowfall_cm = daily.get("snowfall_sum", [])
    depth_m = daily.get("snow_depth_max", [])
    temp_f = daily.get("temperature_2m_mean", [])

    depth = pd.to_numeric(pd.Series(depth_m, dtype="object"), errors="coerce") * M_TO_IN
    snow = (pd.to_numeric(pd.Series(snowfall_cm, dtype="object"), errors="coerce") * CM_TO_IN).clip(lower=0)
    temp = pd.to_numeric(pd.Series(temp_f, dtype="object"), errors="coerce")  # already F
    if len(temp) != len(dates):        # variable absent in this response -> all-NaN
        temp = pd.Series([np.nan] * len(dates))
    df = pd.DataFrame({
        "date": pd.to_datetime(pd.Series(dates), errors="coerce"),
        "swe_inches": np.nan,
        "snow_depth_inches": depth.to_numpy(),
        "new_snow_24hr": snow.to_numpy(),
        "mean_temp_f": temp.to_numpy(),
    })
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Forecast (global) -- the non-US counterpart to nws.fetch_outlook
# ---------------------------------------------------------------------------
_FORECAST_PARAMS = {
    "hourly": "snowfall,rain,temperature_2m,wind_speed_10m,wind_direction_10m",
    "current": "temperature_2m,wind_speed_10m,cloud_cover",
    "timezone": "UTC",
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "precipitation_unit": "inch",
}


def fetch_forecast_outlook(lat: float, lon: float, timeout: int = 30,
                           windows_hours=(24, 48, 72), past_days: int = 3,
                           forecast_days: int = 11,
                           snow_model: dict | None = None) -> Outlook:
    """Provider-neutral Outlook (incoming snow, thaw signals, current weather,
    trailing actuals for the recent-refreeze crust signal, and the 4-10 day
    medium-range band) anywhere on Earth. `past_days` pulls recent actuals in
    the same call. Imperial units.

    `forecast_days` defaults to 11 (Open-Meteo's max is 16) so the medium-range
    band has a full MEDIUM_RANGE['horizon_hours'] (10 days) of hourly data to
    sum -- the near-term 24/48/72h windows only need 3, but fetching once at
    the wider horizon avoids a second call.

    `snow_model` (a MOUNTAINS entry's `snow_model`): sample at its
    `elevation_m` and re-split precipitation at that temperature -- see
    resplit_precip_phase.
    """
    params = _forecast_params(lat, lon, forecast_days, past_days, snow_model)
    resp = http.get(FORECAST, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    payload = resplit_precip_phase(resp.json()) if snow_model is not None else resp.json()
    return parse_forecast_outlook(payload, windows_hours=windows_hours)


def fetch_recent_conditions(lat: float, lon: float, timeout: int = 30,
                            snow_model: dict | None = None) -> Recent:
    """Trailing-72h actuals only (rain, warmth, refreeze) -- the recent-crust
    signal for mountains whose forward forecast comes from another provider
    (NWS), so every mountain gets it from one cheap global endpoint.
    `snow_model` as in fetch_forecast_outlook."""
    params = _forecast_params(lat, lon, 1, 3, snow_model)
    resp = http.get(FORECAST, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    payload = resplit_precip_phase(resp.json()) if snow_model is not None else resp.json()
    return parse_recent(payload)


def _forecast_params(lat, lon, forecast_days, past_days, snow_model) -> dict:
    params = {"latitude": lat, "longitude": lon, "forecast_days": forecast_days,
              "past_days": past_days, **_FORECAST_PARAMS}
    if snow_model is not None:
        # precipitation is needed to re-split the phase ourselves.
        params["hourly"] = params["hourly"] + ",precipitation"
        if snow_model.get("elevation_m") is not None:
            params["elevation"] = snow_model["elevation_m"]   # metres, like the archive
    return params


def resplit_precip_phase(payload: dict) -> dict:
    """Replace the forecast's own hourly snowfall/rain split with one made at
    the requested point's (elevation-downscaled) temperature.

    WHY: Open-Meteo downscales temperature_2m to the requested elevation but
    splits precipitation into rain/snow at the forecast model's GRID height. For
    the narrow NZ/AU ranges that grid sits far below the ski terrain: Mt Hutt,
    14 days to 2026-10-15, 81mm fell in hours at <=0.5C at 1928m and 40mm of it
    was labelled rain (model freezing level 1390m). score.phase_adjusted_snow_in
    only DERATES snow when warm, so snow the grid called rain was never
    recovered, and that phantom rain inflated the thaw and crust penalties.

    Same thresholds as the history model (config.SNOW_MODEL t_snow_c/t_rain_c,
    linear between) and the same water->snow ratio (snow_cm_per_mm, i.e. 7:1).
    Units match _FORECAST_PARAMS: precipitation/rain/snowfall in inches,
    temperature_2m in F. Hours missing precip or temp keep the provider's split.
    Returns a new payload; the input is not modified."""
    from config import SNOW_MODEL
    hourly = dict(payload.get("hourly", {}))
    precip = hourly.get("precipitation")
    temp = hourly.get("temperature_2m")
    if precip is None or temp is None:
        return payload
    t_snow, t_rain = SNOW_MODEL["t_snow_c"], SNOW_MODEL["t_rain_c"]
    ratio = SNOW_MODEL["snow_cm_per_mm"] * 10.0      # cm per mm == in snow per in water
    old_snow = list(hourly.get("snowfall", [None] * len(precip)))
    old_rain = list(hourly.get("rain", [None] * len(precip)))
    snow, rain = [], []
    for i, (p, t) in enumerate(zip(precip, temp)):
        if p is None or t is None:
            snow.append(old_snow[i] if i < len(old_snow) else None)
            rain.append(old_rain[i] if i < len(old_rain) else None)
            continue
        c = (float(t) - 32.0) * 5.0 / 9.0
        frac = 1.0 if c <= t_snow else 0.0 if c >= t_rain else (t_rain - c) / (t_rain - t_snow)
        snow.append(float(p) * frac * ratio)
        rain.append(float(p) * (1.0 - frac))
    hourly["snowfall"], hourly["rain"] = snow, rain
    return {**payload, "hourly": hourly}


def _hourly_frame(payload: dict, now: pd.Timestamp | None):
    hourly = payload.get("hourly", {})
    times = pd.to_datetime(pd.Series(hourly.get("time", [])), errors="coerce")
    now = now if now is not None else pd.Timestamp.utcnow().tz_localize(None)
    return hourly, times, now


def _between(hourly, times, name, start, end) -> pd.Series:
    """Hourly `name` values whose timestamp is in [start, end)."""
    vals = pd.to_numeric(pd.Series(hourly.get(name, []), dtype="object"), errors="coerce")
    vals = vals.iloc[:len(times)]
    mask = (times >= start) & (times < end)
    return vals[mask.to_numpy()[:len(vals)]]


def parse_recent(payload: dict, now: pd.Timestamp | None = None) -> Recent:
    """Recent actuals (last 72h rain + warmth, last 24h min temp) from a forecast
    payload that included `past_days`."""
    hourly, times, now = _hourly_frame(payload, now)
    start72 = now - pd.Timedelta(hours=72)
    rain72 = _between(hourly, times, "rain", start72, now)
    tmax72 = _between(hourly, times, "temperature_2m", start72, now)
    tmin24 = _between(hourly, times, "temperature_2m", now - pd.Timedelta(hours=24), now)

    # New-snow density inputs (Phase 2): total snow in the last 72h and the
    # snowfall-weighted temperature it fell at. The weighting is what makes this a
    # SNOWFALL temperature (the temp during the hours it actually snowed), not just
    # the ambient 72h mean -- a cold storm followed by a warm afternoon still reads
    # cold. None when no snow fell (nothing to weight).
    snow72 = _between(hourly, times, "snowfall", start72, now)
    temp72_snow = _between(hourly, times, "temperature_2m", start72, now)
    snowfall_72h = float(snow72.sum(skipna=True)) if snow72.notna().any() else None
    snow_temp = None
    if snowfall_72h is not None and snowfall_72h > 0:
        n = min(len(snow72), len(temp72_snow))
        s, t = snow72.to_numpy()[:n], temp72_snow.to_numpy()[:n]
        mask = ~(np.isnan(s) | np.isnan(t)) & (s > 0)
        if mask.any() and s[mask].sum() > 0:
            snow_temp = float((s[mask] * t[mask]).sum() / s[mask].sum())

    # Sustained recent wind (Phase 3): a high hourly quantile over 72h -- robust to
    # a lone gust -- plus the direction at the windiest hour (stored for future
    # aspect work). float() throughout: numpy scalars from pandas must not leak into
    # the JSON card.
    wind72 = _between(hourly, times, "wind_speed_10m", start72, now)
    dir72 = _between(hourly, times, "wind_direction_10m", start72, now)
    wind_sustained = None
    wind_dir = None
    if wind72.notna().any():
        wind_sustained = float(wind72.quantile(WIND["sustained_quantile"]))
        gust_idx = wind72.idxmax()
        if gust_idx in dir72.index and pd.notna(dir72.get(gust_idx)):
            wind_dir = float(dir72.loc[gust_idx])

    return Recent(
        rain_72h_in=float(rain72.sum(skipna=True)) if rain72.notna().any() else None,
        tmax_72h_f=float(tmax72.max()) if tmax72.notna().any() else None,
        tmin_24h_f=float(tmin24.min()) if tmin24.notna().any() else None,
        snowfall_72h_in=snowfall_72h,
        snow_temp_72h_f=snow_temp,
        wind_sustained_72h_mph=wind_sustained,
        wind_dir_72h_deg=wind_dir,
    )


def parse_forecast_outlook(payload: dict, now: pd.Timestamp | None = None,
                           windows_hours=(24, 48, 72)) -> Outlook:
    """Parse an Open-Meteo forecast response (imperial units, timezone=UTC).

    Forward windows feed the thaw/incoming-snow signals; if the payload carried
    `past_days`, the trailing window feeds the recent-refreeze crust signal."""
    hourly, times, now = _hourly_frame(payload, now)

    def fwd(name: str, hours: int) -> pd.Series:
        return _between(hourly, times, name, now, now + pd.Timedelta(hours=hours))

    snow_in = {wh: float(fwd("snowfall", wh).sum(skipna=True)) for wh in windows_hours}
    rain_72 = float(fwd("rain", 72).sum(skipna=True))

    # Per-horizon max temp (score.phase_adjusted_snow_in reclassifies forecast
    # snow using each horizon's own warmest reading, not just the 72h one).
    tmax_by_window = {}
    for wh in windows_hours:
        s = fwd("temperature_2m", wh)
        tmax_by_window[wh] = float(s.max()) if s.notna().any() else None
    tmax_72 = tmax_by_window.get(72)

    cur = payload.get("current", {}) or {}
    current = CurrentWeather(
        temperature_f=cur.get("temperature_2m"),
        wind_mph=cur.get("wind_speed_10m"),
        sky_cover_pct=cur.get("cloud_cover"),
    )
    recent = parse_recent(payload, now=now) if (times < now).any() else None

    # Medium-range (4-10 day) band: whatever forward coverage this payload
    # actually has, capped at MEDIUM_RANGE['horizon_hours']. None (not a
    # fabricated 0"-wide band) if the payload doesn't even reach min_hours out.
    forward = times[times >= now]
    coverage_hours = int((forward.max() - now).total_seconds() // 3600) if len(forward) else 0
    min_h, full_h = MEDIUM_RANGE["min_hours"], MEDIUM_RANGE["horizon_hours"]
    window_end = min(coverage_hours, full_h)
    mr = None
    if window_end >= min_h:
        mr_total = float(fwd("snowfall", window_end).sum(skipna=True)) - \
            float(fwd("snowfall", min_h).sum(skipna=True))
        mr = medium_range_band(
            max(0.0, mr_total), window_end, min_h, full_h,
            MEDIUM_RANGE["band_width_at_min"], MEDIUM_RANGE["band_width_at_full"],
        )

    return Outlook(provider="openmeteo", snow_in=snow_in,
                   rain_72h_in=rain_72, tmax_72h_f=tmax_72,
                   tmax_by_window=tmax_by_window, current=current,
                   recent=recent, medium_range=mr,
                   fetched_at=datetime.now(timezone.utc).isoformat())
