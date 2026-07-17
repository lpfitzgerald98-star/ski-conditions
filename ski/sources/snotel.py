"""NRCS SNOTEL client -- the historical grading baseline.

Uses the public NRCS report-generator CSV endpoint, which returns period-of-
record daily values with no API key. Example URL (period of record for the
Snowbird station, SWE + snow depth):

  https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/customSingleStationReport/
      daily/766:UT:SNTL/POR_BEGIN,POR_END/WTEQ::value,SNWD::value

Elements we pull:
  WTEQ = snow water equivalent (in), start-of-day
  SNWD = snow depth (in), start-of-day

`new_snow_24hr` is derived here (SNOTEL doesn't report it directly) as the
positive day-over-day change in snow depth, and ONLY between consecutive
calendar days -- across a gap we store NaN rather than dumping several days of
accumulation onto one day (that would fake signal the grader then trusts).
"""

from __future__ import annotations

import io

import pandas as pd
from ski.sources import http

BASE = (
    "https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/"
    "customSingleStationReport/daily"
)
ELEMENTS = "WTEQ::value,SNWD::value"
USER_AGENT = "ski-conditions-app (historical grading)"


def build_url(triplet: str, start: str = "POR_BEGIN", end: str = "POR_END") -> str:
    return f"{BASE}/{triplet}/{start},{end}/{ELEMENTS}"


def fetch_station_daily(
    triplet: str,
    start: str = "POR_BEGIN",
    end: str = "POR_END",
    timeout: int = 90,
    since=None,
) -> pd.DataFrame:
    """Fetch daily SNOTEL observations and return the canonical obs frame.

    Columns: date (Timestamp), swe_inches, snow_depth_inches, new_snow_24hr.

    `since` (a `date`): incremental ingest -- fetch only from that day forward
    instead of the full period of record. Overrides `start` when given.
    """
    if since is not None:
        start = since.isoformat()
        # The report endpoint returns an error PAGE (not CSV) for an ISO start
        # paired with POR_END -- it only accepts POR_END alongside POR_BEGIN.
        # Pin a concrete end date so an incremental window is a valid range.
        if end == "POR_END":
            end = pd.Timestamp.today().strftime("%Y-%m-%d")
    url = build_url(triplet, start, end)
    resp = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_report_csv(resp.text)


def parse_report_csv(text: str) -> pd.DataFrame:
    """Parse the report-generator CSV (comment header + one data table)."""
    raw = pd.read_csv(io.StringIO(text), comment="#")

    # Column headers embed the station name, e.g.
    # "Snowbird (766) Snow Water Equivalent (in) Start of Day Values".
    # Identify by substring rather than exact match so it works for any station.
    date_col = _find_col(raw, "Date")
    swe_col = _find_col(raw, "Snow Water Equivalent")
    depth_col = _find_col(raw, "Snow Depth")

    df = pd.DataFrame({
        "date": pd.to_datetime(raw[date_col]),
        "swe_inches": pd.to_numeric(raw[swe_col], errors="coerce"),
        "snow_depth_inches": pd.to_numeric(raw[depth_col], errors="coerce"),
    })
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["new_snow_24hr"] = _derive_new_snow(df)
    return df


def _derive_new_snow(df: pd.DataFrame) -> pd.Series:
    """Positive day-over-day snow-depth change, only across consecutive days.

    - consecutive day, depth up   -> the increase (new snow proxy)
    - consecutive day, depth flat/down -> 0 (no new snow / settling)
    - gap of >1 day, or first row  -> NaN (unknown; don't invent it)
    """
    day_gap = df["date"].diff().dt.days
    depth_delta = df["snow_depth_inches"].diff()

    new_snow = depth_delta.clip(lower=0)
    new_snow = new_snow.where(day_gap == 1, other=pd.NA)
    return pd.to_numeric(new_snow, errors="coerce")


def _find_col(df: pd.DataFrame, needle: str) -> str:
    for col in df.columns:
        if needle.lower() in str(col).lower():
            return col
    raise ValueError(
        f"could not find a '{needle}' column in SNOTEL response; got: {list(df.columns)}"
    )
