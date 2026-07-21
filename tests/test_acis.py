"""Tests for the NOAA ACIS client (ski/sources/acis.py). No network.

Exercises the two things that are easy to get wrong: ACIS value parsing
(missing / trace / flagged cells) and the reported-snow vs depth-derived
new-snow branch. Run standalone:  python tests/test_acis.py
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski.sources.acis import _val, parse_stndata  # noqa: E402


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# --- value parsing ----------------------------------------------------------
def test_val_missing_and_trace():
    assert _isnan(_val("M"))          # missing
    assert _isnan(_val("S"))          # subsequent/accumulated -> treat as missing
    assert _isnan(_val(""))           # blank
    assert _val("T") == 0.0           # trace of snow -> 0
    assert _val("0.0") == 0.0
    assert _val("4.5") == 4.5
    assert _val("46") == 46.0


def test_val_strips_flags():
    # ACIS sometimes suffixes a flag char; we take the leading number.
    assert _val("1.5A") == 1.5
    assert _val("12 ") == 12.0


# --- reported-snowfall station: new_snow comes straight from `snow` ----------
def test_parse_uses_reported_snowfall():
    payload = {"data": [
        ["2024-01-01", "3.0", "10"],
        ["2024-01-02", "0.0", "9"],
        ["2024-01-03", "T", "9"],
        ["2024-01-04", "5.0", "14"],
    ]}
    df = parse_stndata(payload)
    assert list(df["new_snow_24hr"]) == [3.0, 0.0, 0.0, 5.0]
    assert list(df["snow_depth_inches"]) == [10.0, 9.0, 9.0, 14.0]
    assert df["swe_inches"].isna().all()          # COOP has no SWE
    assert len(df) == 4


# --- depth-only station (Mansfield-style): derive from consecutive-day depth --
def test_parse_derives_new_snow_when_snowfall_absent():
    payload = {"data": [
        ["2024-01-01", "M", "10"],
        ["2024-01-02", "M", "16"],   # +6 -> new snow 6
        ["2024-01-03", "M", "14"],   # settle/melt -> 0
        ["2024-01-05", "M", "20"],   # 2-day gap -> NaN (don't dump accumulation)
    ]}
    df = parse_stndata(payload)
    ns = list(df["new_snow_24hr"])
    assert _isnan(ns[0])              # first row: unknown
    assert ns[1] == 6.0
    assert ns[2] == 0.0
    assert _isnan(ns[3])             # across a >1-day gap
    # depth still populated throughout
    assert list(df["snow_depth_inches"]) == [10.0, 16.0, 14.0, 20.0]


def test_parse_reads_mean_temp_and_tolerates_absence():
    # 4-element rows (snow, snwd, avgt): avgt (already F) flows to mean_temp_f;
    # M -> NaN. Rows without a 4th cell (older 2-elem style) still parse, temp NaN.
    df = parse_stndata({"data": [
        ["2024-01-01", "3.0", "10", "18"],
        ["2024-01-02", "0.0", "9", "M"],     # missing temp -> NaN
        ["2024-01-03", "5.0", "14", "31.5"],
    ]})
    temps = list(df["mean_temp_f"])
    assert temps[0] == 18.0
    assert _isnan(temps[1])
    assert temps[2] == 31.5
    # A response that predates the avgt element (3-col rows) must not crash.
    df2 = parse_stndata({"data": [["2024-01-01", "3.0", "10"]]})
    assert _isnan(df2["mean_temp_f"].iloc[0])


def test_parse_sorts_and_drops_bad_dates():
    payload = {"data": [
        ["2024-01-03", "1.0", "5"],
        ["not-a-date", "9.0", "9"],
        ["2024-01-01", "2.0", "3"],
    ]}
    df = parse_stndata(payload)
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-03"]


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
