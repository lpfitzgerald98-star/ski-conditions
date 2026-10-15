"""Own degree-day snowpack for Open-Meteo mountains (config.SNOW_MODEL):
  - openmeteo.snowpack_model  -- partition, accumulation, melt, reset
  - openmeteo.apply_snow_model -- swaps depth/new-snow columns, F->C
  - pipeline.ingest_mountain  -- water-year-aligned tail, version-triggered full pull

No network: fetches are monkeypatched.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from config import MOUNTAINS, SNOW_MODEL
from ski import pipeline
from ski.db import get_station_meta, set_station_meta
from ski.sources import openmeteo


def _days(n, start="2026-06-01"):
    return pd.date_range(start, periods=n, freq="D")


def test_cold_precip_accumulates_as_snow_and_settles():
    d = _days(30)
    depth, new = openmeteo.snowpack_model(np.full(30, 10.0), np.full(30, -8.0), d)
    assert np.all(np.diff(depth) > 0)                      # no melt below freezing
    assert new[0] == 10.0 * SNOW_MODEL["snow_cm_per_mm"]
    # 300mm SWE settled toward rho_max: deeper than rho_max implies, shallower than fresh
    assert 300 / SNOW_MODEL["rho_max"] * 100 < depth[-1] < 300 / SNOW_MODEL["rho_new"] * 100


def test_warm_precip_is_rain_and_melts_the_pack():
    d = _days(20)
    P = np.r_[np.full(10, 20.0), np.full(10, 20.0)]
    T = np.r_[np.full(10, -5.0), np.full(10, 8.0)]       # snow, then warm rain
    depth, new = openmeteo.snowpack_model(P, T, d)
    assert depth[9] > 50
    assert np.all(new[10:] == 0)                           # all rain above t_rain_c
    assert depth[-1] < depth[9] * 0.2                      # degree-day + rain-on-snow melt


def test_mixed_phase_is_linear_between_thresholds():
    mid = (SNOW_MODEL["t_snow_c"] + SNOW_MODEL["t_rain_c"]) / 2
    _, new = openmeteo.snowpack_model([10.0], [mid], _days(1))
    assert abs(new[0] - 5.0 * SNOW_MODEL["snow_cm_per_mm"]) < 1e-9


def test_precip_multiplier_scales_snow():
    d = _days(10)
    a, _ = openmeteo.snowpack_model(np.full(10, 5.0), np.full(10, -5.0), d)
    b, _ = openmeteo.snowpack_model(np.full(10, 5.0), np.full(10, -5.0), d, precip_multiplier=1.5)
    assert abs(b[-1] / a[-1] - 1.5) < 1e-9


def test_state_resets_at_water_year_start():
    d = _days(10, start="2026-04-26")                      # crosses May 1
    depth, _ = openmeteo.snowpack_model(np.full(10, 10.0), np.full(10, -5.0), d, reset_month=5)
    i = list(d).index(pd.Timestamp("2026-05-01"))
    assert depth[i] < depth[i - 1]                          # pack wiped, only that day's snow
    tail, _ = openmeteo.snowpack_model(np.full(10 - i, 10.0), np.full(10 - i, -5.0), d[i:],
                                       reset_month=5)
    assert np.allclose(depth[i:], tail)                     # tail from the boundary == full run


def test_apply_snow_model_replaces_columns_from_fahrenheit():
    d = _days(5)
    df = pd.DataFrame({"date": d, "swe_inches": np.nan, "snow_depth_inches": 0.0,
                       "new_snow_24hr": 0.0, "mean_temp_f": 14.0})    # -10C
    payload = {"daily": {"time": [x.strftime("%Y-%m-%d") for x in d],
                         "precipitation_sum": [10.0] * 5}}
    out = openmeteo.apply_snow_model(df, payload, {"precip_multiplier": 1.0})
    assert out["snow_depth_inches"].iloc[-1] > 0
    assert abs(out["new_snow_24hr"].iloc[0] - 7.0 / 2.54) < 1e-9
    assert list(out.columns) == list(df.columns)


def test_oceania_mountains_opt_in():
    from ski.regions import region_for
    opted = {k for k, m in MOUNTAINS.items() if m.get("snow_model")}
    oceania = {k for k, m in MOUNTAINS.items() if region_for(m) in ("New Zealand", "Australia")}
    assert opted == oceania
    assert all(MOUNTAINS[k]["data_source"] == "openmeteo" for k in opted)


def test_ingest_aligns_tail_to_water_year_and_repulls_on_version_change(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    calls = []

    def fake_fetch(loc, timeout=90, since=None, snow_model=None):
        calls.append((since, snow_model))
        d = _days(3, start="2026-10-01")
        return pd.DataFrame({"date": d, "swe_inches": np.nan, "snow_depth_inches": 30.0,
                             "new_snow_24hr": 0.0, "mean_temp_f": 20.0})

    monkeypatch.setattr(openmeteo, "fetch_station_daily", fake_fetch)
    st = pipeline.mountain_station(MOUNTAINS["mt_hutt"])
    pipeline.ingest_mountain("mt_hutt", db)                 # no version yet -> full pull
    assert calls[-1][0] is None and calls[-1][1]["reset_month"] == 5
    assert get_station_meta(db, st, "snow_model_version") == str(SNOW_MODEL["version"])
    pipeline.ingest_mountain("mt_hutt", db)                 # incremental -> from May 1
    assert calls[-1][0] == date(2026, 5, 1)
    set_station_meta(db, st, "snow_model_version", "0")     # stale model -> full again
    pipeline.ingest_mountain("mt_hutt", db)
    assert calls[-1][0] is None
