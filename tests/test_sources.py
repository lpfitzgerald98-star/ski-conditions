"""Offline tests for the CDEC (eastern Sierra) and ECCC (Canada) source clients.

Both parse network payloads into the canonical obs frame; here we feed synthetic
payloads and assert the tricky bits: CDEC missing-value sentinels + SWE/depth
merge, and ECCC cm->inch conversion with the reported-snow vs depth-derived
branch. Run standalone:  python tests/test_sources.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski.sources.bcsws import parse_archive  # noqa: E402
from ski.sources.cdec import build_frame  # noqa: E402
from ski.sources.eccc import parse_features  # noqa: E402
from ski.sources.openmeteo import parse_archive as parse_om  # noqa: E402


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# --- CDEC ------------------------------------------------------------------
def test_cdec_merges_swe_and_depth_and_flags_missing():
    swe = [
        {"date": "2024-1-1 00:00", "value": 10.0},
        {"date": "2024-1-2 00:00", "value": 11.0},
        {"date": "2024-1-3 00:00", "value": -9999},   # missing sentinel
    ]
    depth = [
        {"date": "2024-1-1 00:00", "value": 40.0},
        {"date": "2024-1-2 00:00", "value": 46.0},    # +6 -> new snow 6
        {"date": "2024-1-3 00:00", "value": 45.0},
    ]
    df = build_frame(swe, depth)
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert list(df["swe_inches"])[:2] == [10.0, 11.0]
    assert _isnan(list(df["swe_inches"])[2])           # -9999 -> NaN
    assert _isnan(list(df["new_snow_24hr"])[0])        # first row unknown
    assert list(df["new_snow_24hr"])[1] == 6.0


def test_cdec_empty_inputs():
    df = build_frame([], [])
    assert df.empty
    assert set(df.columns) == {"date", "swe_inches", "snow_depth_inches", "new_snow_24hr"}


# --- ECCC ------------------------------------------------------------------
def _feat(date_s, snow_cm=None, depth_cm=None):
    return {"properties": {"LOCAL_DATE": date_s, "TOTAL_SNOW": snow_cm, "SNOW_ON_GRND": depth_cm}}


def test_eccc_reported_snowfall_cm_to_inches():
    feats = [
        _feat("2024-01-01 00:00:00", snow_cm=2.54, depth_cm=25.4),   # 1 in snow, 10 in depth
        _feat("2024-01-02 00:00:00", snow_cm=0.0, depth_cm=25.4),
        _feat("2024-01-03 00:00:00", snow_cm=12.7, depth_cm=None),   # 5 in snow, no depth
    ]
    df = parse_features(feats)
    ns = [round(x, 3) for x in df["new_snow_24hr"]]
    assert ns == [1.0, 0.0, 5.0]
    assert round(df["snow_depth_inches"].iloc[0], 3) == 10.0
    assert df["swe_inches"].isna().all()


def test_eccc_derives_new_snow_when_snowfall_absent():
    # depth-only station: all TOTAL_SNOW None -> derive from SNOW_ON_GRND (cm)
    feats = [
        _feat("2024-01-01 00:00:00", snow_cm=None, depth_cm=25.4),   # 10 in
        _feat("2024-01-02 00:00:00", snow_cm=None, depth_cm=38.1),   # 15 in -> +5
        _feat("2024-01-03 00:00:00", snow_cm=None, depth_cm=33.02),  # 13 in -> 0
    ]
    df = parse_features(feats)
    ns = list(df["new_snow_24hr"])
    assert _isnan(ns[0])
    assert round(ns[1], 3) == 5.0
    assert ns[2] == 0.0


def test_eccc_empty():
    df = parse_features([])
    assert df.empty


# --- BC ASWS ---------------------------------------------------------------
def test_bcsws_extracts_station_column_mm_to_in():
    # wide archive: DATE + one SWE column per station (values in mm)
    text = (
        "DATE(UTC),2A06P Mount Revelstoke,2F10P Silver Star Mountain\n"
        "2024-01-01 16:00:00,254,127\n"     # 10 in, 5 in
        "2024-01-02 16:00:00,-3,132\n"      # noise negative -> clip 0 ; 5.2 in
        "2024-01-03 16:00:00,508,\n"        # 20 in ; missing
    )
    df = parse_archive(text, "2A06P")
    assert round(df["swe_inches"].iloc[0], 2) == 10.0
    assert df["swe_inches"].iloc[1] == 0.0            # negative clipped
    assert round(df["swe_inches"].iloc[2], 2) == 20.0
    assert df["snow_depth_inches"].isna().all()       # BC frame is SWE-only
    assert df["new_snow_24hr"].isna().all()


def test_bcsws_missing_station_raises():
    text = "DATE(UTC),2A06P Mount Revelstoke\n2024-01-01 16:00:00,254\n"
    try:
        parse_archive(text, "9Z99P")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- Open-Meteo (global reanalysis) ----------------------------------------
def test_openmeteo_units_and_nulls():
    payload = {"daily": {
        "time": ["2024-08-01", "2024-08-02", "2024-08-03"],
        "snowfall_sum": [2.54, 0.0, None],    # cm -> 1 in, 0, NaN
        "snow_depth_max": [1.0, None, 0.5],   # m -> 39.37 in, NaN, 19.685 in
    }}
    df = parse_om(payload)
    ns = df["new_snow_24hr"].tolist()
    assert round(ns[0], 3) == 1.0
    assert ns[1] == 0.0
    assert _isnan(ns[2])
    assert round(df["snow_depth_inches"].iloc[0], 1) == 39.4
    assert _isnan(df["snow_depth_inches"].iloc[1])
    assert df["swe_inches"].isna().all()


def test_openmeteo_empty():
    assert parse_om({"daily": {"time": [], "snowfall_sum": [], "snow_depth_max": []}}).empty


# --- Forecast outlooks (provider-neutral) -----------------------------------
def test_openmeteo_forecast_outlook_windows_and_current():
    import pandas as pd
    from ski.sources.openmeteo import parse_forecast_outlook

    now = pd.Timestamp("2026-01-10T00:00")
    hours = pd.date_range(now, periods=96, freq="h")
    snowfall = [0.5] * 24 + [0.0] * 72        # 12" in the first 24h, dry after
    rain = [0.0] * 48 + [0.02] * 48           # 0.96" of rain in h48..96
    temp = [30.0] * 60 + [44.0] * 36          # warm spell late in the window
    payload = {
        "hourly": {
            "time": [h.isoformat() for h in hours],
            "snowfall": snowfall, "rain": rain, "temperature_2m": temp,
        },
        "current": {"temperature_2m": 28.0, "wind_speed_10m": 12.0, "cloud_cover": 75},
    }
    o = parse_forecast_outlook(payload, now=now)
    assert o.provider == "openmeteo"
    assert round(o.snow_in[24], 2) == 12.0
    assert round(o.snow_in[72], 2) == 12.0            # nothing after h24
    assert round(o.rain_72h_in, 2) == 0.48            # only h48..72 falls inside
    assert o.tmax_72h_f == 44.0
    assert o.current.temperature_f == 28.0
    assert o.current.wind_mph == 12.0
    assert o.current.sky_cover_pct == 75


def test_openmeteo_recent_window_from_past_days():
    import pandas as pd
    from ski.sources.openmeteo import parse_forecast_outlook, parse_recent

    now = pd.Timestamp("2026-02-10T00:00")
    # 168h of hourly data: 72h of past actuals, then 96h forward
    hours = pd.date_range(now - pd.Timedelta(hours=72), periods=168, freq="h")
    # past: 0.5" rain total spread over the last 72h; a thaw to 45F then a hard
    # refreeze to 18F inside the last 24h. forward: cold and dry.
    rain, temp, snow = [], [], []
    for h in hours:
        past = h < now
        dt_from_now_h = (h - now).total_seconds() / 3600.0
        rain.append(0.5/72 if past else 0.0)
        if past:
            # warm mid-window, cold (18F) in the final 24h
            temp.append(45.0 if dt_from_now_h < -24 else 18.0)
        else:
            temp.append(25.0)
        snow.append(0.0 if past else (0.4 if dt_from_now_h < 24 else 0.0))
    payload = {"hourly": {"time": [h.isoformat() for h in hours],
                          "snowfall": snow, "rain": rain, "temperature_2m": temp},
               "current": {"temperature_2m": 20.0, "wind_speed_10m": 5.0, "cloud_cover": 30}}

    rec = parse_recent(payload, now=now)
    assert round(rec.rain_72h_in, 2) == 0.50
    assert rec.tmax_72h_f == 45.0          # the recent thaw
    assert rec.tmin_24h_f == 18.0          # the recent hard refreeze

    o = parse_forecast_outlook(payload, now=now)
    assert round(o.snow_in[24], 1) == 9.6  # 24 forward hrs x 0.4"
    assert o.rain_72h_in == 0.0            # forward is dry
    assert o.recent is not None and o.recent.tmax_72h_f == 45.0


def test_nws_outlook_rain_split_and_tmax():
    import pandas as pd
    from ski.sources.nws import outlook_from_properties

    now = pd.Timestamp("2026-01-10T00:00", tz="UTC")
    props = {
        # 10" of snow in the first 24h (254 mm), nothing after
        "snowfallAmount": {"uom": "wmoUnit:mm", "values": [
            {"validTime": "2026-01-10T00:00:00+00:00/PT24H", "value": 254.0},
        ]},
        # 2" liquid over 72h; snow credits 10/10 = 1" -> rain ~= 1"
        "quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": [
            {"validTime": "2026-01-10T00:00:00+00:00/P3D", "value": 50.8},
        ]},
        # temps: 0C now, 8C later inside 72h, 20C outside the window (ignored)
        "temperature": {"values": [
            {"validTime": "2026-01-10T00:00:00+00:00/PT12H", "value": 0.0},
            {"validTime": "2026-01-11T00:00:00+00:00/P1D", "value": 8.0},
            {"validTime": "2026-01-14T00:00:00+00:00/P1D", "value": 20.0},
        ]},
        "windSpeed": {"values": [
            {"validTime": "2026-01-10T00:00:00+00:00/P4D", "value": 16.09},
        ]},
        "skyCover": {"values": [
            {"validTime": "2026-01-10T00:00:00+00:00/P4D", "value": 40},
        ]},
    }
    o = outlook_from_properties(props, now=now)
    assert o.provider == "nws"
    assert round(o.snow_in[24], 1) == 10.0
    assert round(o.snow_in[72], 1) == 10.0
    assert round(o.rain_72h_in, 1) == 1.0             # qpf 2" minus 10"/10 snow water
    assert round(o.tmax_72h_f, 1) == 46.4             # 8C, not the out-of-window 20C
    assert round(o.current.temperature_f, 1) == 32.0
    assert round(o.current.wind_mph, 1) == 10.0
    assert o.current.sky_cover_pct == 40


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
