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


def fetch_station_daily(loc: str, timeout: int = 90, since=None) -> pd.DataFrame:
    """Daily snow history for a 'lat,lon' location string, as the canonical obs
    frame (snowfall -> new_snow_24hr, depth -> snow_depth_inches, swe NaN).

    `since` (a `date`): incremental ingest -- fetch only from that day forward
    instead of back to 1980. This is the biggest single lever on Action time:
    54 of the roster's stations are Open-Meteo, and a full 1980-> pull for each
    is what draws the archive API's 429s. The tail is a handful of days.
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
    resp = http.get(ARCHIVE, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_archive(resp.json())


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
                           forecast_days: int = 11) -> Outlook:
    """Provider-neutral Outlook (incoming snow, thaw signals, current weather,
    trailing actuals for the recent-refreeze crust signal, and the 4-10 day
    medium-range band) anywhere on Earth. `past_days` pulls recent actuals in
    the same call. Imperial units.

    `forecast_days` defaults to 11 (Open-Meteo's max is 16) so the medium-range
    band has a full MEDIUM_RANGE['horizon_hours'] (10 days) of hourly data to
    sum -- the near-term 24/48/72h windows only need 3, but fetching once at
    the wider horizon avoids a second call.
    """
    params = {"latitude": lat, "longitude": lon, "forecast_days": forecast_days,
              "past_days": past_days, **_FORECAST_PARAMS}
    resp = http.get(FORECAST, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_forecast_outlook(resp.json(), windows_hours=windows_hours)


def fetch_recent_conditions(lat: float, lon: float, timeout: int = 30) -> Recent:
    """Trailing-72h actuals only (rain, warmth, refreeze) -- the recent-crust
    signal for mountains whose forward forecast comes from another provider
    (NWS), so every mountain gets it from one cheap global endpoint."""
    params = {"latitude": lat, "longitude": lon, "forecast_days": 1,
              "past_days": 3, **_FORECAST_PARAMS}
    resp = http.get(FORECAST, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_recent(resp.json())


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
