"""BC ASWS client -- British Columbia Automated Snow Weather Stations.

Fills the biggest Canadian gap: the major interior BC resorts (Revelstoke, Silver
Star, Big White) have no usable ECCC/COOP snow station nearby, but BC runs a dense
network of high-alpine automated snow pillows -- several sit right on the ski
mountains (e.g. "2A06P Mount Revelstoke", "2F10P Silver Star Mountain"). These
report SWE, so BC mountains grade on the same "swe_gain" season metric as SNOTEL.

The province publishes the full daily history as one wide CSV (a DATE column plus
one SWE column per station, values in mm). We download it once (cached on disk for
a few hours so ingesting several BC mountains doesn't re-fetch), pull the target
station's column, and map it onto the canonical obs frame. Snow depth is served in
a separate 38 MB file we deliberately skip -- so base/storm grades are N/A for BC
mountains (like the ECCC ones), and their score rests on season + in-season SWE.
"""

from __future__ import annotations

import io
import os
import tempfile
import time

import numpy as np
import pandas as pd
from ski.sources import http

ARCHIVE_URL = ("https://www.env.gov.bc.ca/wsd/data_searches/snow/asws/data/"
               "SW_DailyArchive.csv")
# The archive holds only CLOSED water years (it ends at the previous Sep 30);
# the in-progress water year is published separately in SWDaily.csv. Reading only
# the archive leaves every BC station ~9 months stale, so we fetch and merge both.
CURRENT_URL = ("https://www.env.gov.bc.ca/wsd/data_searches/snow/asws/data/"
               "SWDaily.csv")
USER_AGENT = "ski-conditions-app (historical grading)"
MM_TO_IN = 1.0 / 25.4
_CACHE = os.path.join(tempfile.gettempdir(), "ski_bc_sw_dailyarchive.csv")
_CURRENT_CACHE = os.path.join(tempfile.gettempdir(), "ski_bc_sw_daily.csv")
_CACHE_TTL = 6 * 3600  # seconds


def fetch_station_daily(station_id: str, timeout: int = 120) -> pd.DataFrame:
    """Daily SWE history for one BC ASWS station id (e.g. '2A06P'), as the
    canonical obs frame (swe populated; depth / new_snow are NaN).

    Merges the closed-water-year archive with the in-progress current-year feed
    so the series runs right up to today, not to the previous Sep 30."""
    frames = [parse_archive(_archive_text(timeout), station_id)]
    current = _try_parse_current(_current_text(timeout), station_id)
    if current is not None:
        frames.append(current)
    combined = pd.concat(frames, ignore_index=True)
    # Current-feed rows win over any archive overlap (keep='last' after sort).
    combined = (combined.sort_values("date")
                        .drop_duplicates(subset="date", keep="last")
                        .reset_index(drop=True))
    return combined


def _try_parse_current(text: str, station_id: str) -> pd.DataFrame | None:
    """Parse the current-year feed, tolerating a station that isn't in it yet."""
    try:
        return parse_archive(text, station_id)
    except ValueError:
        return None  # station absent from the current feed -> archive only


def _cached_text(url: str, cache_path: str, timeout: int) -> str:
    """A wide CSV feed's text, cached on disk for _CACHE_TTL."""
    if os.path.exists(cache_path) and (time.time() - os.path.getmtime(cache_path)) < _CACHE_TTL:
        return io.open(cache_path, encoding="utf-8").read()
    resp = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()  # pooled session has raise_on_status=False -> don't
                             # decode an error page as if it were CSV data
    text = resp.content.decode("latin-1")
    try:
        io.open(cache_path, "w", encoding="utf-8").write(text)
    except OSError:
        pass  # caching is best-effort
    return text


def _archive_text(timeout: int) -> str:
    """The wide daily-archive CSV text, cached on disk for _CACHE_TTL."""
    return _cached_text(ARCHIVE_URL, _CACHE, timeout)


def _current_text(timeout: int) -> str:
    """The wide current-water-year CSV text, cached on disk for _CACHE_TTL."""
    return _cached_text(CURRENT_URL, _CURRENT_CACHE, timeout)


def parse_archive(text: str, station_id: str) -> pd.DataFrame:
    """Extract one station's SWE column from the wide archive CSV."""
    df = pd.read_csv(io.StringIO(text))
    date_col = df.columns[0]
    swe_col = _find_col(df, station_id)

    dates = pd.to_datetime(df[date_col], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    swe_mm = pd.to_numeric(df[swe_col], errors="coerce")
    out = pd.DataFrame({
        "date": dates,
        "swe_inches": (swe_mm * MM_TO_IN).clip(lower=0),   # drop sensor-noise negatives
        "snow_depth_inches": np.nan,
        "new_snow_24hr": np.nan,
    })
    return out.dropna(subset=["date", "swe_inches"]).sort_values("date").reset_index(drop=True)


def _find_col(df: pd.DataFrame, station_id: str) -> str:
    """The column whose header starts with the station id ('2A06P ...')."""
    sid = station_id.strip().lower()
    for col in df.columns:
        if str(col).strip().lower().startswith(sid):
            return col
    raise ValueError(f"station '{station_id}' not found in BC ASWS archive")
