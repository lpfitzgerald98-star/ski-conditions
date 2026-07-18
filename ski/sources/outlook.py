"""Provider-neutral forecast outlook -- "what's coming" from any forecast source.

NWS (api.weather.gov) covers US mountains; Open-Meteo's forecast API covers
everywhere else (Canada, the Southern Hemisphere). Both providers are parsed
into this one shape so the scoring pipeline never cares which one a mountain
has -- every mountain gets a forecast sub-score and live weather, not just the
US roster.

The thaw fields exist because a forecast can be BAD news: incoming rain or a
sustained warm spell destroys the base, and "nothing coming" and "rain coming"
must not score the same (see score.thaw_index / score.forecast_score).

The `recent` fields are the backward-looking mirror of thaw: a thaw that has
already happened and refrozen leaves a boilerplate/ice surface TODAY, which the
forward forecast can't see (see score.refreeze_index).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CurrentWeather:
    """Live conditions for the weather-quality sub-score."""
    temperature_f: float | None
    wind_mph: float | None
    sky_cover_pct: float | None


@dataclass
class Recent:
    """Trailing actuals for the recent-refreeze (crust) and new-snow-density signals."""
    rain_72h_in: float | None = None    # rain that fell in the last 72h
    tmax_72h_f: float | None = None     # warmest in the last 72h (was there a thaw?)
    tmin_24h_f: float | None = None     # coldest in the last 24h (did it refreeze?)
    # New-snow density (Phase 2): how much snow fell in the last 72h and the
    # temperature it fell at (snowfall-weighted). Together they estimate whether
    # the recent snow is light/dry or heavy/wet for mountains without a SWE pillow
    # -- see score.density_from_temp. None when nothing fell.
    snowfall_72h_in: float | None = None
    snow_temp_72h_f: float | None = None
    # Wind loading/scour (Phase 3): sustained recent wind (a high hourly quantile
    # over 72h, not a gust) and its direction at the windiest hour. Direction is
    # stored for future aspect work; only magnitude is scored today. None when no
    # wind reading is available. See score.wind_scour_index.
    wind_sustained_72h_mph: float | None = None
    wind_dir_72h_deg: float | None = None


@dataclass
class MediumRangeBand:
    """The 4-10 day tier: a RANGE, not a point estimate -- medium-range forecast
    skill is real but coarse, so this reports a low/mid/high accumulation band
    over whatever forward window the source actually covers (up to 10 days),
    plus how confident that band is.

    `horizon_hours` is the ACTUAL outer edge of the window (a source may only
    reach 5-7 days), not the target 10-day reach -- so a short-horizon source
    is visibly reporting a narrower, more-confident tier rather than silently
    padding to 10 days with nothing behind it.

    `weight_factor` (0..1) is how much this tier should count in the blended
    forecast score, tapering down as the window reaches farther out -- see
    `medium_range_band`. It shrinks in lockstep with the band widening, so the
    same underlying "how sure are we" signal drives both the display band and
    the score contribution instead of two independently-tuned numbers.
    """
    low_in: float
    mid_in: float
    high_in: float
    horizon_hours: int
    confidence: str              # "low" | "very_low"
    weight_factor: float


def medium_range_band(
    mid_in: float, window_end_hours: int,
    min_hours: int, full_hours: int,
    band_width_at_min: float, band_width_at_full: float,
) -> "MediumRangeBand | None":
    """Build the 4-10 day band from a raw forecast total and how far out the
    source's data actually reaches.

    None below `min_hours` of coverage -- a source that only reaches 2-3 days
    out has nothing to say about "medium range" and must not fabricate a tier.
    Between `min_hours` and `full_hours`, the band widens and the score weight
    (see Outlook doc) tapers together, linearly in the fraction of that span
    covered: a window that barely clears the 4-day floor is (relatively) the
    most trustworthy this tier ever is; one that reaches the full 10 days is
    the least, since it is dominated by the least reliable days.
    """
    if window_end_hours < min_hours:
        return None
    window_end_hours = min(window_end_hours, full_hours)
    span = full_hours - min_hours
    frac = max(0.0, min(1.0, (window_end_hours - min_hours) / span)) if span > 0 else 1.0
    width = band_width_at_min + frac * (band_width_at_full - band_width_at_min)
    low = max(0.0, mid_in * (1.0 - width))
    high = mid_in * (1.0 + width)
    confidence = "very_low" if frac >= 0.5 else "low"
    weight_factor = 1.0 - 0.6 * frac
    return MediumRangeBand(
        low_in=round(low, 1), mid_in=round(mid_in, 1), high_in=round(high, 1),
        horizon_hours=window_end_hours, confidence=confidence,
        weight_factor=round(weight_factor, 2),
    )


@dataclass
class Outlook:
    """Everything the score needs from a forecast, provider-agnostic."""
    provider: str                                  # "nws" | "openmeteo"
    snow_in: dict = field(default_factory=dict)    # {window_hours: forecast inches}
    rain_72h_in: float | None = None               # liquid rain over the next 72h
    tmax_72h_f: float | None = None                # warmest temp (F) in the next 72h
    tmax_by_window: dict = field(default_factory=dict)  # {window_hours: warmest F in that window}
    current: CurrentWeather | None = None
    recent: Recent | None = None                   # trailing actuals (crust signal)
    medium_range: MediumRangeBand | None = None    # 4-10 day accumulation band (see above)
    fetched_at: str | None = None                  # ISO-8601 UTC, when this outlook was fetched
