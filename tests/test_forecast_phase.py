"""AU/NZ forecast rain/snow split at the resort's elevation (not the model grid's):
  - openmeteo.resplit_precip_phase   -- marginal-temperature hours re-split
  - fetch_forecast_outlook / fetch_recent_conditions -- elevation + precipitation
    requested for snow_model mountains only; outlook snow/rain use the re-split
  - pipeline.fetch_outlook_for_mountain -- passes each mountain's snow_model

Covers Perisher + Thredbo (AU) and Mt Hutt + Cardrona (NZ). No network: the HTTP
layer is monkeypatched with a synthetic Open-Meteo payload in which the model
grid called sub-freezing precipitation "rain" -- the 2026 Mt Hutt failure mode.
"""

from __future__ import annotations

import pandas as pd
import pytest

from config import MOUNTAINS, SNOW_MODEL
from ski import pipeline
from ski.sources import openmeteo

RESORTS = ["perisher", "thredbo", "mt_hutt", "cardrona"]
RATIO = SNOW_MODEL["snow_cm_per_mm"] * 10.0          # 7:1 snow:water


def _f(c):
    return c * 9.0 / 5.0 + 32.0


NOW = pd.Timestamp("2026-08-10 00:00")


def _payload(temps_c, precip_in=0.10, grid_snow=0.0):
    """Hourly payload starting at NOW: every hour has `precip_in` of water that
    the grid labelled entirely rain (snowfall `grid_snow`)."""
    n = len(temps_c)
    times = pd.date_range(NOW, periods=n, freq="h").strftime("%Y-%m-%dT%H:%M").tolist()
    return {"elevation": 1500.0, "hourly": {
        "time": times,
        "temperature_2m": [_f(c) for c in temps_c],
        "precipitation": [precip_in] * n,
        "rain": [precip_in] * n,
        "snowfall": [grid_snow] * n,
        "wind_speed_10m": [5.0] * n,
        "wind_direction_10m": [270.0] * n,
    }, "current": {"temperature_2m": _f(temps_c[0]), "wind_speed_10m": 5.0, "cloud_cover": 80}}


# --- the split itself ---------------------------------------------------------
@pytest.mark.parametrize("temp_c, snow_frac", [
    (-3.0, 1.0),                                              # cold: all snow
    (SNOW_MODEL["t_snow_c"], 1.0),                            # at threshold
    ((SNOW_MODEL["t_snow_c"] + SNOW_MODEL["t_rain_c"]) / 2, 0.5),   # marginal: half
    (SNOW_MODEL["t_rain_c"], 0.0),                            # all rain
    (6.0, 0.0),
])
def test_resplit_marginal_temperatures(temp_c, snow_frac):
    out = openmeteo.resplit_precip_phase(_payload([temp_c]))
    h = out["hourly"]
    assert h["snowfall"][0] == pytest.approx(0.10 * snow_frac * RATIO)
    assert h["rain"][0] == pytest.approx(0.10 * (1 - snow_frac))


def test_resplit_keeps_provider_split_when_inputs_missing_and_does_not_mutate():
    p = _payload([-2.0, -2.0])
    p["hourly"]["precipitation"][1] = None
    out = openmeteo.resplit_precip_phase(p)
    assert out["hourly"]["snowfall"][1] == 0.0 and out["hourly"]["rain"][1] == 0.10
    assert p["hourly"]["snowfall"][0] == 0.0                 # input untouched
    assert openmeteo.resplit_precip_phase({"hourly": {}}) == {"hourly": {}}


# --- the fetchers, per resort -------------------------------------------------
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        # 24h at 0.5C (below t_snow -> snow at the resort, grid said rain), then
        # 24h marginal (2C -> half snow), then 24h warm.
        temps = [0.5] * 24 + [2.0] * 24 + [6.0] * 24 + [6.0] * 200
        p = _payload(temps)
        return _Resp(p)

    monkeypatch.setattr(openmeteo.http, "get", fake_get)
    monkeypatch.setattr(pd.Timestamp, "utcnow", classmethod(lambda cls: NOW.tz_localize("UTC")))
    return calls


@pytest.mark.parametrize("key", RESORTS)
def test_outlook_uses_resort_elevation_and_resplit(capture, key):
    sm = MOUNTAINS[key]["snow_model"]
    m = MOUNTAINS[key]
    out = openmeteo.fetch_forecast_outlook(m["latitude"], m["longitude"], snow_model=sm)
    params = capture[-1]
    assert "precipitation" in params["hourly"].split(",")
    assert params.get("elevation") == sm.get("elevation_m")   # only where configured
    # 24h: 24 * 0.10in all snow at 0.5C; 48h adds 24 * 0.05in snow-water at 2C.
    assert out.snow_in[24] == pytest.approx(24 * 0.10 * RATIO)
    assert out.snow_in[48] == pytest.approx(24 * 0.10 * RATIO + 24 * 0.05 * RATIO)
    # The phantom rain is gone from the thaw input: only the marginal + warm hours.
    assert out.rain_72h_in == pytest.approx(24 * 0.05 + 24 * 0.10)


def test_perisher_and_thredbo_request_their_override_elevations(capture):
    for key, elev in (("perisher", 1830), ("thredbo", 1700)):
        m = MOUNTAINS[key]
        openmeteo.fetch_recent_conditions(m["latitude"], m["longitude"],
                                          snow_model=m["snow_model"])
        assert capture[-1]["elevation"] == elev


def test_without_snow_model_the_provider_split_is_untouched(capture):
    out = openmeteo.fetch_forecast_outlook(46.0, 7.7)          # e.g. an Alps point
    assert "elevation" not in capture[-1]
    assert out.snow_in[24] == 0.0                              # grid said rain; kept
    assert out.rain_72h_in == pytest.approx(72 * 0.10)


@pytest.mark.parametrize("key", RESORTS)
def test_pipeline_passes_each_resorts_snow_model(monkeypatch, key):
    seen = {}
    monkeypatch.setattr(openmeteo, "fetch_forecast_outlook",
                        lambda lat, lon, snow_model=None: seen.setdefault("sm", snow_model))
    pipeline.fetch_outlook_for_mountain(key)
    assert seen["sm"] == MOUNTAINS[key]["snow_model"]
