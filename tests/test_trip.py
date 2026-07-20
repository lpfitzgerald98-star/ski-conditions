"""Tests for ski.trip -- the Trip Predictor (future-date ranking).

No network, no DB: synthetic observation frames and rows. Covers the shared decay
curve on the lead-time axis, the climatology aggregation, the historical-baseline
comparable score, and the lead-time blend (including its convergence to live
scoring at lead 0 and to pure history far out).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from config import TRIP_LEAD_DECAY
from ski import trip
from ski.score import decay_weight


# --- synthetic observations -------------------------------------------------
def _obs(years, depth=40.0, daily_snow=1.0, swe0=5.0, start=(12, 1), end=(3, 31)):
    """Daily obs across several Northern-Hemisphere winters (Dec 1 -> Mar 31).

    Constant depth and a constant daily snowfall so climatology medians are
    predictable; SWE accumulates from swe0 by daily_snow x a nominal water fraction.
    `years` are calendar years of the Jan-Mar tail (so year Y spans Dec Y-1 -> Mar Y).
    """
    rows = []
    for y in years:
        d = date(y - 1, *start)
        stop = date(y, *end)
        swe = swe0
        while d <= stop:
            swe += daily_snow * 0.1
            rows.append({"date": pd.Timestamp(d), "swe_inches": swe,
                         "snow_depth_inches": depth, "new_snow_24hr": daily_snow})
            d = date.fromordinal(d.toordinal() + 1)
    return pd.DataFrame(rows)


# --- decay curve on the lead-time axis --------------------------------------
def test_lead_weight_is_the_shared_decay_curve():
    hl = TRIP_LEAD_DECAY["half_life_days"]
    assert trip.lead_weight(0) == 1.0                      # lead 0 -> full weight
    assert abs(trip.lead_weight(hl) - 0.5) < 1e-9          # one half-life -> 0.5
    assert abs(trip.lead_weight(2 * hl) - 0.25) < 1e-9     # two -> 0.25
    # It IS score.decay_weight, just named for this axis.
    assert trip.lead_weight(7) == decay_weight(7, hl)
    # Monotone non-increasing in lead time.
    ws = [trip.lead_weight(l) for l in range(0, 40)]
    assert all(a >= b for a, b in zip(ws, ws[1:]))


# --- blend: convergence + graceful edges ------------------------------------
def test_blend_converges_to_current_at_lead_zero():
    # At lead 0 the trip score IS today's comparable score -- history drops out.
    score, w = trip.blend_trip_score(current=72.0, baseline=30.0, lead_days=0)
    assert w == 1.0
    assert score == 72.0


def test_blend_leans_to_history_far_out():
    # Months out, w ~ 0: the blend is essentially the baseline.
    score, w = trip.blend_trip_score(current=90.0, baseline=40.0, lead_days=90)
    assert w < 0.01
    assert abs(score - 40.0) < 0.5


def test_blend_midrange_is_between():
    hl = int(TRIP_LEAD_DECAY["half_life_days"])
    score, w = trip.blend_trip_score(current=80.0, baseline=20.0, lead_days=hl)
    assert abs(w - 0.5) < 1e-9
    assert abs(score - 50.0) < 0.6          # halfway at one half-life


def test_blend_baseline_only_is_pure_history_at_any_lead():
    # Off-season NOW (no live score) but history exists -> lean on history, near or far.
    assert trip.blend_trip_score(None, 55.0, lead_days=3)[0] == 55.0
    assert trip.blend_trip_score(None, 55.0, lead_days=120)[0] == 55.0


def test_blend_current_only_trusts_today_near_but_not_far():
    # No history for this window: today's conditions stand in only within a
    # forecast-ish horizon; far out with no history we can't predict.
    near, _ = trip.blend_trip_score(60.0, None, lead_days=2)
    far, _ = trip.blend_trip_score(60.0, None, lead_days=60)
    assert near == 60.0
    assert far is None


def test_blend_neither_is_none():
    assert trip.blend_trip_score(None, None, lead_days=10)[0] is None


# --- climatology ------------------------------------------------------------
def test_climatology_recovers_typical_conditions():
    obs = _obs([2021, 2022, 2023], depth=40.0, daily_snow=1.0)
    clim = trip.climatology(obs, wy_start=10, season_start_dowy=1, metric="new_snow")
    jan15 = trip.target_dowy(date(2021, 1, 15), wy_start=10)
    c = clim[jan15]
    assert abs(c["base_in"] - 40.0) < 1.0              # constant depth recovered
    assert abs(c["fresh_in"] - 3.0) < 0.5              # 1"/day x 3-day fresh window
    assert c["season_in"] > 30.0                       # cumulative Dec 1 -> Jan 15
    assert c["n_years"] == 3


def test_climatology_offseason_dowy_is_bare():
    obs = _obs([2021, 2022, 2023])
    clim = trip.climatology(obs, wy_start=10, season_start_dowy=1, metric="new_snow")
    jul = trip.target_dowy(date(2021, 7, 15), wy_start=10)
    c = clim[jul]
    # No July observations -> no typical base/fresh, so the row will read off-season.
    assert c["base_in"] is None or c["base_in"] == 0.0
    assert trip.baseline_row(clim, jul, "m", "R")["in_season"] is not True


def test_climatology_empty_obs():
    assert trip.climatology(pd.DataFrame(), 10, 1, "new_snow") == {}


# --- historical baseline (comparable score, trip weights) -------------------
def test_baseline_ranks_deeper_pack_higher():
    deep = trip.climatology(_obs([2021, 2022, 2023], depth=70, daily_snow=2.0),
                            10, 1, "new_snow")
    thin = trip.climatology(_obs([2021, 2022, 2023], depth=15, daily_snow=0.2),
                            10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    rows = [trip.baseline_row(deep, dowy, "deep", "Utah"),
            trip.baseline_row(thin, dowy, "thin", "Utah")]
    trip.score_baseline(rows)
    by = {r["key"]: r for r in rows}
    assert by["deep"]["baseline_score"] > by["thin"]["baseline_score"]


def test_offseason_baseline_row_drops_out_of_ranking():
    clim = trip.climatology(_obs([2021, 2022, 2023]), 10, 1, "new_snow")
    alive_dowy = trip.target_dowy(date(2021, 1, 15), 10)
    dead_dowy = trip.target_dowy(date(2021, 7, 15), 10)
    rows = [trip.baseline_row(clim, alive_dowy, "alive", "Utah"),
            trip.baseline_row(clim, dead_dowy, "dead", "Utah")]
    trip.score_baseline(rows)
    by = {r["key"]: r for r in rows}
    assert by["dead"]["baseline_score"] is None       # off-season -> unranked


# --- roster helper (the /trip endpoint + build both call this) --------------
def test_roster_baseline_rows_resolves_per_mountain_water_year():
    # One NH and one SH station share a calendar date but land on opposite ends of
    # their own water years: Jan is deep winter North, deep off-season South.
    clim_nh = trip.climatology(_obs([2021, 2022, 2023], depth=60, daily_snow=1.5),
                               10, 1, "new_snow")
    # A real Southern-Hemisphere winter (Jun 1 -> Sep 30, same calendar year), water
    # year starting in May -- so January is deep off-season for it.
    sh_rows = []
    for y in (2021, 2022, 2023):
        d = date(y, 6, 1)
        while d <= date(y, 9, 30):
            sh_rows.append({"date": pd.Timestamp(d), "swe_inches": 6.0,
                            "snow_depth_inches": 60.0, "new_snow_24hr": 1.5})
            d = date.fromordinal(d.toordinal() + 1)
    clim_sh = trip.climatology(pd.DataFrame(sh_rows), 5, 1, "new_snow")
    # Two NH mountains (so the in-season pool has a peer) plus one SH.
    meta = {"nh": {"station": "N", "wy_start": 10, "region": "R"},
            "nh2": {"station": "N", "wy_start": 10, "region": "R"},
            "sh": {"station": "S", "wy_start": 5, "region": "R"}}
    clim = {"N": clim_nh, "S": clim_sh}
    rows = trip.roster_baseline_rows(date(2027, 1, 20), ["nh", "nh2", "sh"], meta, clim)
    by = {r["key"]: r for r in rows}
    # NH mountains rank in January; SH is off-season then and drops out (None).
    assert by["nh"]["baseline_score"] is not None
    assert by["sh"]["in_season"] is not True
    assert by["sh"]["baseline_score"] is None


# --- rank ordering ----------------------------------------------------------
def test_rank_trip_orders_desc_nulls_last():
    rows = [{"key": "a", "trip_score": 40.0}, {"key": "b", "trip_score": None},
            {"key": "c", "trip_score": 90.0}]
    order = [r["key"] for r in trip.rank_trip(rows)]
    assert order == ["c", "a", "b"]
