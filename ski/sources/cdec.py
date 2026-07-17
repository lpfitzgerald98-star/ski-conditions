"""CDEC client -- California Data Exchange Center, for the eastern Sierra.

SNOTEL doesn't reach the eastern Sierra (the nearest SNOTEL to Mammoth is ~30 mi
away), but California runs its own dense snow-sensor network -- CDEC -- with
real-time SWE pillows and snow-depth sensors, served over a JSON servlet with no
API key. Because CDEC stations report SWE, these mountains grade on the same
"swe_gain" season metric as SNOTEL.

Sensors we pull (daily, dur_code "D"):
  3  = snow water content / SWE (in)   -> swe_inches
  18 = snow depth (in)                 -> snow_depth_inches
`new_snow_24hr` is derived from consecutive-day snow-depth change, same policy as
the SNOTEL client (a >1-day gap yields NaN rather than a fabricated dump).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ski.sources import http

BASE = "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet"
USER_AGENT = "ski-conditions-app (historical grading)"
SENSOR_SWE = 3
SENSOR_DEPTH = 18
_MISSING_BELOW = -9998.0   # CDEC uses -9999 (and similar) for missing


def fetch_station_daily(
    station: str,
    start: str = "1900-01-01",
    end: str | None = None,
    timeout: int = 90,
    since=None,
) -> pd.DataFrame:
    """Fetch daily CDEC SWE + snow depth and return the canonical obs frame
    (date, swe_inches, snow_depth_inches, new_snow_24hr).

    `since` (a `date`): incremental ingest -- fetch only from that day forward
    instead of back to 1900. Overrides `start` when given.
    """
    if since is not None:
        start = since.isoformat()
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    swe = _fetch_sensor(station, SENSOR_SWE, start, end, timeout)
    depth = _fetch_sensor(station, SENSOR_DEPTH, start, end, timeout)
    return build_frame(swe, depth)


def _fetch_sensor(station, sensor, start, end, timeout) -> list:
    params = {"Stations": station, "SensorNums": sensor, "dur_code": "D",
              "Start": start, "End": end}
    resp = http.get(BASE, params=params,
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def build_frame(swe_rows: list, depth_rows: list) -> pd.DataFrame:
    """Merge CDEC SWE + depth records (each a list of {date, value}) into the
    canonical obs frame."""
    swe = _series(swe_rows, "swe_inches")
    depth = _series(depth_rows, "snow_depth_inches")
    df = pd.merge(swe, depth, on="date", how="outer").sort_values("date").reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=["date", "swe_inches", "snow_depth_inches", "new_snow_24hr"])
    df["new_snow_24hr"] = _derive_new_snow(df)
    return df


def _series(rows: list, col: str) -> pd.DataFrame:
    dates, vals = [], []
    for r in rows or []:
        dates.append(pd.to_datetime(r.get("date"), errors="coerce"))
        v = r.get("value")
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = np.nan
        vals.append(np.nan if (v is None or v <= _MISSING_BELOW) else v)
    df = pd.DataFrame({"date": dates, col: vals}).dropna(subset=["date"])
    return df.drop_duplicates(subset="date", keep="last")


def _derive_new_snow(df: pd.DataFrame) -> pd.Series:
    day_gap = df["date"].diff().dt.days
    depth_delta = df["snow_depth_inches"].diff()
    new_snow = depth_delta.clip(lower=0).where(day_gap == 1, other=pd.NA)
    return pd.to_numeric(new_snow, errors="coerce")
