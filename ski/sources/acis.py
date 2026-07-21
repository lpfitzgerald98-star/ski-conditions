"""NOAA ACIS client -- historical snow for the Northeast (and anywhere SNOTEL
doesn't reach).

SNOTEL is a Western-US network: it has *zero* stations in VT/NH/ME/NY. East
Coast resorts instead lean on NWS COOP stations, which ACIS (the Applied Climate
Information System, run by the NOAA Regional Climate Centers) serves over a clean
JSON API with decades of daily record and no API key.

We pull two elements and map them onto the SAME canonical obs frame the SNOTEL
client produces, so the grading engine doesn't care where a mountain's data came
from:
  snow = daily snowfall (in)      -> new_snow_24hr   (REPORTED new snow)
  snwd = snow depth (in)          -> snow_depth_inches
  avgt = daily mean air temp (F)  -> mean_temp_f      (Tier-2 density proxy)
COOP has no snow-water-equivalent, so `swe_inches` is NaN and these mountains
grade on the "new_snow" season metric (config: per-mountain `season_metric`).
`avgt` (already Fahrenheit -- ACIS defaults to English units) feeds the Tier-2
climatological density: without a SWE pillow we read snow density from how cold it
was when it fell (see ski.trip.climatology / score.density_from_temp).

Two station flavors, handled transparently:
  * Most COOP stations report snowfall directly -> use it as new_snow_24hr.
  * A few (notably Mount Mansfield, the Stowe stake) report ONLY depth -> we
    derive new_snow from consecutive-day depth increase, exactly as the SNOTEL
    client does, so the season metric still works.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from ski.sources import http

BASE = "https://data.rcc-acis.org/StnData"
USER_AGENT = "ski-conditions-app (historical grading)"

# ACIS daily value flags we translate rather than parse as numbers.
_MISSING = {"M", "S", ""}   # M=missing, S=subsequent(accumulated into a later day)
_TRACE = "T"                # trace of snow/snowfall -> count as 0.0
_LEADING_FLOAT = re.compile(r"-?\d+(?:\.\d+)?")


def fetch_station_daily(
    sid: str,
    sdate: str = "por",
    edate: str = "por",
    timeout: int = 90,
    since=None,
) -> pd.DataFrame:
    """Fetch daily ACIS observations for a station id and return the canonical
    obs frame (date, swe_inches, snow_depth_inches, new_snow_24hr).

    `sid` is any ACIS station id -- a GHCN id like "USC00435416" works well.
    `sdate`/`edate` accept ISO dates or "por" (period of record).
    `since` (a `date`): incremental ingest -- fetch only from that day forward.
    Overrides `sdate` when given.
    """
    if since is not None:
        sdate = since.isoformat()
    payload = {"sid": sid, "sdate": sdate, "edate": edate, "elems": "snow,snwd,avgt"}
    resp = http.post(BASE, json=payload,
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_stndata(resp.json())


def parse_stndata(payload: dict) -> pd.DataFrame:
    """Parse an ACIS StnData response (rows of [date, snow, snwd, avgt])."""
    rows = payload.get("data", [])
    dates, snowfall, depth, temp = [], [], [], []
    for r in rows:
        dates.append(r[0])
        snowfall.append(_val(r[1]) if len(r) > 1 else np.nan)
        depth.append(_val(r[2]) if len(r) > 2 else np.nan)
        temp.append(_val(r[3]) if len(r) > 3 else np.nan)

    df = pd.DataFrame({
        "date": pd.to_datetime(dates, errors="coerce"),
        "swe_inches": np.nan,                       # COOP has no SWE
        "snow_depth_inches": pd.to_numeric(depth, errors="coerce"),
        "mean_temp_f": pd.to_numeric(temp, errors="coerce"),
        "_reported_snow": pd.to_numeric(snowfall, errors="coerce"),
    })
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Prefer reported snowfall; fall back to depth-derived new snow for
    # depth-only stations (e.g. the Mount Mansfield stake).
    if df["_reported_snow"].notna().any():
        df["new_snow_24hr"] = df["_reported_snow"]
    else:
        df["new_snow_24hr"] = _derive_new_snow(df)
    return df.drop(columns="_reported_snow")


def _val(cell) -> float:
    """One ACIS daily value -> float. M/S -> NaN, trace -> 0.0."""
    s = str(cell).strip()
    if s in _MISSING:
        return np.nan
    if s.startswith(_TRACE):
        return 0.0
    m = _LEADING_FLOAT.match(s)
    return float(m.group()) if m else np.nan


def _derive_new_snow(df: pd.DataFrame) -> pd.Series:
    """Positive day-over-day snow-depth change, only across consecutive days.

    Identical policy to the SNOTEL client: a >1-day gap or the first row yields
    NaN rather than dumping several days of accumulation onto one day.
    """
    day_gap = df["date"].diff().dt.days
    depth_delta = df["snow_depth_inches"].diff()
    new_snow = depth_delta.clip(lower=0).where(day_gap == 1, other=pd.NA)
    return pd.to_numeric(new_snow, errors="coerce")
