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
    """Trailing actuals for the recent-refreeze (crust) signal."""
    rain_72h_in: float | None = None    # rain that fell in the last 72h
    tmax_72h_f: float | None = None     # warmest in the last 72h (was there a thaw?)
    tmin_24h_f: float | None = None     # coldest in the last 24h (did it refreeze?)


@dataclass
class Outlook:
    """Everything the score needs from a forecast, provider-agnostic."""
    provider: str                                  # "nws" | "openmeteo"
    snow_in: dict = field(default_factory=dict)    # {window_hours: forecast inches}
    rain_72h_in: float | None = None               # liquid rain over the next 72h
    tmax_72h_f: float | None = None                # warmest temp (F) in the next 72h
    current: CurrentWeather | None = None
    recent: Recent | None = None                   # trailing actuals (crust signal)
