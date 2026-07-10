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

import numpy as np
import pandas as pd

from ski.sources.outlook import CurrentWeather, Outlook, Recent
from ski.sources import http

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
USER_AGENT = "ski-conditions-app (historical grading)"
START_DATE = "1980-01-01"
CM_TO_IN = 1.0 / 2.54
M_TO_IN = 39.3701


def fetch_station_daily(loc: str, timeout: int = 90) -> pd.DataFrame:
    """Daily snow history for a 'lat,lon' location string, as the canonical obs
    frame (snowfall -> new_snow_24hr, depth -> snow_depth_inches, swe NaN)."""
    lat, lon = (float(x) for x in loc.split(","))
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": START_DATE,
        "end_date": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "daily": "snowfall_sum,snow_depth_max",
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

    depth = pd.to_numeric(pd.Series(depth_m, dtype="object"), errors="coerce") * M_TO_IN
    snow = (pd.to_numeric(pd.Series(snowfall_cm, dtype="object"), errors="coerce") * CM_TO_IN).clip(lower=0)
    df = pd.DataFrame({
        "date": pd.to_datetime(pd.Series(dates), errors="coerce"),
        "swe_inches": np.nan,
        "snow_depth_inches": depth.to_numpy(),
        "new_snow_24hr": snow.to_numpy(),
    })
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Forecast (global) -- the non-US counterpart to nws.fetch_outlook
# ---------------------------------------------------------------------------
_FORECAST_PARAMS = {
    "hourly": "snowfall,rain,temperature_2m",
    "current": "temperature_2m,wind_speed_10m,cloud_cover",
    "timezone": "UTC",
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "precipitation_unit": "inch",
}


def fetch_forecast_outlook(lat: float, lon: float, timeout: int = 30,
                           windows_hours=(24, 72), past_days: int = 3) -> Outlook:
    """Provider-neutral Outlook (incoming snow, thaw signals, current weather, and
    trailing actuals for the recent-refreeze crust signal) anywhere on Earth.
    `past_days` pulls recent actuals in the same call. Imperial units."""
    params = {"latitude": lat, "longitude": lon, "forecast_days": 4,
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
    rain72 = _between(hourly, times, "rain", now - pd.Timedelta(hours=72), now)
    tmax72 = _between(hourly, times, "temperature_2m", now - pd.Timedelta(hours=72), now)
    tmin24 = _between(hourly, times, "temperature_2m", now - pd.Timedelta(hours=24), now)
    return Recent(
        rain_72h_in=float(rain72.sum(skipna=True)) if rain72.notna().any() else None,
        tmax_72h_f=float(tmax72.max()) if tmax72.notna().any() else None,
        tmin_24h_f=float(tmin24.min()) if tmin24.notna().any() else None,
    )


def parse_forecast_outlook(payload: dict, now: pd.Timestamp | None = None,
                           windows_hours=(24, 72)) -> Outlook:
    """Parse an Open-Meteo forecast response (imperial units, timezone=UTC).

    Forward windows feed the thaw/incoming-snow signals; if the payload carried
    `past_days`, the trailing window feeds the recent-refreeze crust signal."""
    hourly, times, now = _hourly_frame(payload, now)

    def fwd(name: str, hours: int) -> pd.Series:
        return _between(hourly, times, name, now, now + pd.Timedelta(hours=hours))

    snow_in = {wh: float(fwd("snowfall", wh).sum(skipna=True)) for wh in windows_hours}
    rain_72 = float(fwd("rain", 72).sum(skipna=True))
    tmax_series = fwd("temperature_2m", 72)
    tmax_72 = float(tmax_series.max()) if tmax_series.notna().any() else None

    cur = payload.get("current", {}) or {}
    current = CurrentWeather(
        temperature_f=cur.get("temperature_2m"),
        wind_mph=cur.get("wind_speed_10m"),
        sky_cover_pct=cur.get("cloud_cover"),
    )
    recent = parse_recent(payload, now=now) if (times < now).any() else None
    return Outlook(provider="openmeteo", snow_in=snow_in,
                   rain_72h_in=rain_72, tmax_72h_f=tmax_72, current=current,
                   recent=recent)
