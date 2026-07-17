"""NWS / NOAA client -- forecasts and "what's coming" + active weather alerts.

Free, no API key. NWS just asks for a descriptive User-Agent. Two calls we use:
  - gridpoints/{office}/{x},{y}/forecast   -> multi-period forecast
  - alerts/active?point={lat},{lon}        -> active watches/warnings/advisories
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from config import MEDIUM_RANGE
from ski.sources.outlook import CurrentWeather, Outlook, medium_range_band
from ski.sources import http

BASE = "https://api.weather.gov"
USER_AGENT = "ski-conditions-app (contact: set-your-email@example.com)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

# Nominal snow-to-liquid ratio used to split rain out of the QPF (which is
# liquid-equivalent of ALL precip, snow included): rain ~ qpf - snow/SLR.
SNOW_TO_LIQUID_RATIO = 10.0


@dataclass
class ForecastPeriod:
    name: str
    short_forecast: str
    temperature: int | None
    temperature_unit: str
    wind: str
    detailed: str


@dataclass
class Alert:
    event: str
    severity: str
    urgency: str
    headline: str
    expires: str


def fetch_forecast(office: str, grid_x: int, grid_y: int, timeout: int = 30) -> list[ForecastPeriod]:
    url = f"{BASE}/gridpoints/{office}/{grid_x},{grid_y}/forecast"
    resp = http.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    periods = resp.json().get("properties", {}).get("periods", [])
    return [
        ForecastPeriod(
            name=p.get("name", ""),
            short_forecast=p.get("shortForecast", ""),
            temperature=p.get("temperature"),
            temperature_unit=p.get("temperatureUnit", "F"),
            wind=f"{p.get('windSpeed', '')} {p.get('windDirection', '')}".strip(),
            detailed=p.get("detailedForecast", ""),
        )
        for p in periods
    ]


@dataclass
class SnowBlock:
    start: pd.Timestamp   # tz-aware (UTC)
    end: pd.Timestamp
    inches: float


_DUR_RE = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def _iso_duration_hours(dur: str) -> float:
    """Hours in an ISO-8601 duration like 'PT6H', 'P1DT6H', 'PT30M'."""
    m = _DUR_RE.fullmatch(dur)
    if not m:
        return 0.0
    days, hours, mins = (int(g or 0) for g in m.groups())
    return days * 24 + hours + mins / 60.0


def _amount_blocks(series: dict) -> list[SnowBlock]:
    """Parse a gridpoints amount time-series (mm over validTime intervals) into
    inch blocks with explicit start/end timestamps."""
    to_in = (1.0 / 25.4) if "mm" in series.get("uom", "") else 1.0
    blocks = []
    for v in series.get("values", []):
        if v.get("value") is None:
            continue
        start_s, dur = v["validTime"].split("/")
        start = pd.Timestamp(start_s)
        end = start + pd.Timedelta(hours=_iso_duration_hours(dur))
        blocks.append(SnowBlock(start=start, end=end, inches=float(v["value"]) * to_in))
    return blocks


def _fetch_gridpoint_properties(office: str, grid_x: int, grid_y: int, timeout: int = 30) -> dict:
    url = f"{BASE}/gridpoints/{office}/{grid_x},{grid_y}"
    resp = http.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("properties", {})


def fetch_gridpoint_snowfall(office: str, grid_x: int, grid_y: int, timeout: int = 30) -> list[SnowBlock]:
    """Quantitative forecast snowfall blocks from the raw gridpoints endpoint.

    NWS reports `snowfallAmount` in mm over variable-length validTime intervals;
    we convert to inches and explicit start/end timestamps.
    """
    props = _fetch_gridpoint_properties(office, grid_x, grid_y, timeout)
    return _amount_blocks(props.get("snowfallAmount", {}))


def forecast_snow_total(blocks: list[SnowBlock], hours: int, now: pd.Timestamp | None = None) -> float:
    """Forecast snowfall (inches) over the next `hours`, overlap-weighting blocks
    so variable-length intervals are counted proportionally."""
    now = now or pd.Timestamp.now(tz="UTC")
    end = now + pd.Timedelta(hours=hours)
    total = 0.0
    for b in blocks:
        ov_start = max(b.start, now)
        ov_end = min(b.end, end)
        ov = (ov_end - ov_start).total_seconds() / 3600.0
        if ov <= 0:
            continue
        block_h = (b.end - b.start).total_seconds() / 3600.0
        total += b.inches * (ov / block_h) if block_h > 0 else b.inches
    return total


def _value_now(series: dict, now: pd.Timestamp) -> float | None:
    """Value of a gridpoints time-series whose interval covers `now` (else first)."""
    values = series.get("values", [])
    if not values:
        return None
    for v in values:
        start_s, dur = v["validTime"].split("/")
        start = pd.Timestamp(start_s)
        end = start + pd.Timedelta(hours=_iso_duration_hours(dur))
        if start <= now < end:
            return v["value"]
    return values[0]["value"]


def _current_from_properties(p: dict, now: pd.Timestamp) -> CurrentWeather:
    temp_c = _value_now(p.get("temperature", {}), now)
    wind_kmh = _value_now(p.get("windSpeed", {}), now)
    sky = _value_now(p.get("skyCover", {}), now)
    return CurrentWeather(
        temperature_f=None if temp_c is None else temp_c * 9 / 5 + 32,
        wind_mph=None if wind_kmh is None else wind_kmh * 0.621371,
        sky_cover_pct=sky,
    )


def fetch_current_weather(office: str, grid_x: int, grid_y: int, timeout: int = 30) -> CurrentWeather:
    """Current temperature (F), wind (mph), and sky cover (%) from gridpoints."""
    p = _fetch_gridpoint_properties(office, grid_x, grid_y, timeout)
    return _current_from_properties(p, pd.Timestamp.now(tz="UTC"))


def _series_max(series: dict, now: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Max value of a gridpoints time-series over intervals overlapping [now, end]."""
    best = None
    for v in series.get("values", []):
        if v.get("value") is None:
            continue
        start_s, dur = v["validTime"].split("/")
        start = pd.Timestamp(start_s)
        stop = start + pd.Timedelta(hours=_iso_duration_hours(dur))
        if stop <= now or start >= end:
            continue
        best = v["value"] if best is None else max(best, v["value"])
    return best


def outlook_from_properties(
    p: dict, now: pd.Timestamp | None = None, windows_hours=(24, 48, 72)
) -> Outlook:
    """Build the provider-neutral Outlook from one gridpoints payload.

    Rain is split out of the QPF (liquid-equivalent of ALL precip) by crediting
    forecast snow at a nominal 10:1 snow-to-liquid ratio -- coarse, but the thaw
    signal only needs "is meaningful rain coming", not hydrology-grade numbers.
    """
    now = now or pd.Timestamp.now(tz="UTC")
    snow_blocks = _amount_blocks(p.get("snowfallAmount", {}))
    qpf_blocks = _amount_blocks(p.get("quantitativePrecipitation", {}))

    snow_in = {wh: forecast_snow_total(snow_blocks, wh, now) for wh in windows_hours}
    snow_72 = forecast_snow_total(snow_blocks, 72, now)
    qpf_72 = forecast_snow_total(qpf_blocks, 72, now)
    rain_72 = max(0.0, qpf_72 - snow_72 / SNOW_TO_LIQUID_RATIO)

    # Per-horizon max temp (score.phase_adjusted_snow_in reclassifies forecast
    # snow using each horizon's own warmest reading, not just the 72h one).
    tmax_by_window = {}
    for wh in windows_hours:
        tc = _series_max(p.get("temperature", {}), now, now + pd.Timedelta(hours=wh))
        tmax_by_window[wh] = None if tc is None else tc * 9 / 5 + 32

    # Medium-range (4-10 day) band. NWS gridpoints snowfallAmount typically only
    # reaches ~7 days out (not the full 10) -- window_end is the REAL outer edge
    # of the blocks we have, so a short NWS horizon reports a narrower, more-
    # confident band rather than padding out to a 10-day reach it doesn't have.
    min_h, full_h = MEDIUM_RANGE["min_hours"], MEDIUM_RANGE["horizon_hours"]
    last_end = max((b.end for b in snow_blocks), default=now)
    coverage_hours = max(0, int((last_end - now).total_seconds() // 3600))
    window_end = min(coverage_hours, full_h)
    mr = None
    if window_end >= min_h:
        mr_total = forecast_snow_total(snow_blocks, window_end, now) - \
            forecast_snow_total(snow_blocks, min_h, now)
        mr = medium_range_band(
            max(0.0, mr_total), window_end, min_h, full_h,
            MEDIUM_RANGE["band_width_at_min"], MEDIUM_RANGE["band_width_at_full"],
        )

    return Outlook(
        provider="nws",
        snow_in=snow_in,
        rain_72h_in=rain_72,
        tmax_72h_f=tmax_by_window.get(72),
        tmax_by_window=tmax_by_window,
        current=_current_from_properties(p, now),
        medium_range=mr,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def fetch_outlook(office: str, grid_x: int, grid_y: int, timeout: int = 30,
                  windows_hours=(24, 48, 72)) -> Outlook:
    """One gridpoints call -> incoming snow, rain/warmth (thaw), current weather."""
    p = _fetch_gridpoint_properties(office, grid_x, grid_y, timeout)
    return outlook_from_properties(p, windows_hours=windows_hours)


def fetch_active_alerts(lat: float, lon: float, timeout: int = 30) -> list[Alert]:
    url = f"{BASE}/alerts/active"
    resp = http.get(url, headers=HEADERS, params={"point": f"{lat},{lon}"}, timeout=timeout)
    resp.raise_for_status()
    features = resp.json().get("features", [])
    alerts = []
    for f in features:
        p = f.get("properties", {})
        alerts.append(Alert(
            event=p.get("event", ""),
            severity=p.get("severity", ""),
            urgency=p.get("urgency", ""),
            headline=p.get("headline", ""),
            expires=p.get("expires", ""),
        ))
    return alerts
