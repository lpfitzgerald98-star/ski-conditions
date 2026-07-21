"""ECCC client -- Environment and Climate Change Canada, for Canadian resorts.

Neither SNOTEL (US) nor ACIS (US COOP) covers Canada. ECCC publishes daily
climate observations through the MSC GeoMet OGC API (api.weather.gc.ca), no key
required. We read the `climate-daily` collection, which reports snow in
centimetres:
  TOTAL_SNOW       = new snowfall (cm)   -> new_snow_24hr   (REPORTED new snow)
  SNOW_ON_GROUND   = snow depth (cm)     -> snow_depth_inches
  MEAN_TEMPERATURE = daily mean temp (C) -> mean_temp_f      (Tier-2 density proxy)
Canadian climate stations don't report SWE, so these mountains grade on the
"new_snow" season metric, and read snow density from the mean temperature the day
it fell (Tier-2; see ski.trip.climatology / score.density_from_temp).

Two station flavors, handled transparently (as in the ACIS client): most report
snowfall directly (use it); depth-only stations get new-snow derived from
consecutive-day depth change. Values are converted cm -> inches.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ski.sources import http

BASE = "https://api.weather.gc.ca/collections/climate-daily/items"
USER_AGENT = "ski-conditions-app (historical grading)"
CM_TO_IN = 1.0 / 2.54
_PAGE = 10000


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def fetch_station_daily(climate_id: str, timeout: int = 90, since=None) -> pd.DataFrame:
    """Fetch the full daily record for an ECCC station (by CLIMATE_IDENTIFIER)
    and return the canonical obs frame.

    `since` (a `date`): incremental ingest -- adds an OGC `datetime` filter so
    the server returns only rows from that day forward. Best-effort: if the
    collection ever ignores the filter, upsert still keeps the result correct,
    just not faster (only 9 stations use ECCC, so it isn't the bottleneck).
    """
    features = []
    offset = 0
    datetime_filter = f"{since.isoformat()}/.." if since is not None else None
    while True:
        params = {
            "CLIMATE_IDENTIFIER": climate_id,
            "sortby": "LOCAL_DATE",
            "limit": _PAGE, "offset": offset, "f": "json",
        }
        if datetime_filter:
            params["datetime"] = datetime_filter
        resp = http.get(BASE, params=params,
                            headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        page = resp.json().get("features", [])
        features.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return parse_features(features)


def parse_features(features: list) -> pd.DataFrame:
    """Parse GeoJSON climate-daily features into the canonical obs frame."""
    dates, depth_cm, snow_cm, temp_c = [], [], [], []
    for f in features:
        p = f.get("properties", f)
        dates.append(p.get("LOCAL_DATE"))
        depth_cm.append(_num(p.get("SNOW_ON_GROUND")))
        snow_cm.append(_num(p.get("TOTAL_SNOW")))
        temp_c.append(_num(p.get("MEAN_TEMPERATURE")))

    df = pd.DataFrame({
        "date": pd.to_datetime(dates, errors="coerce"),
        "swe_inches": np.nan,                                    # ECCC has no SWE
        "snow_depth_inches": np.array(depth_cm, dtype=float) * CM_TO_IN,
        "mean_temp_f": _c_to_f(np.array(temp_c, dtype=float)),
        "_reported_snow": np.array(snow_cm, dtype=float) * CM_TO_IN,
    })
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return df.drop(columns="_reported_snow").assign(new_snow_24hr=[])

    if df["_reported_snow"].notna().any():
        df["new_snow_24hr"] = df["_reported_snow"]
    else:
        df["new_snow_24hr"] = _derive_new_snow(df)
    return df.drop(columns="_reported_snow")


def _num(v) -> float:
    if v is None or v == "":
        return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _derive_new_snow(df: pd.DataFrame) -> pd.Series:
    day_gap = df["date"].diff().dt.days
    depth_delta = df["snow_depth_inches"].diff()
    new_snow = depth_delta.clip(lower=0).where(day_gap == 1, other=pd.NA)
    return pd.to_numeric(new_snow, errors="coerce")
