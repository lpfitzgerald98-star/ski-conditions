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
def _obs(years, depth=40.0, daily_snow=1.0, swe0=5.0, wf=0.1, temp_f=None,
         swe_channel=True, start=(12, 1), end=(3, 31)):
    """Daily obs across several Northern-Hemisphere winters (Dec 1 -> Mar 31).

    Constant depth and a constant daily snowfall so climatology medians are
    predictable; SWE accumulates from swe0 by daily_snow x `wf`, the new-snow water
    fraction (0.1 = a normal 10:1 storm; raise it for maritime cement, lower it for
    cold/dry blower). `years` are the Jan-Mar tail (year Y spans Dec Y-1 -> Mar Y).

    `temp_f` sets a constant daily mean_temp_f (for the Tier-2 density proxy); when
    given, `swe_channel=False` also blanks SWE so the frame looks like a depth-only
    network (ACIS/ECCC/Open-Meteo) that must fall back to temperature.
    """
    rows = []
    for y in years:
        d = date(y - 1, *start)
        stop = date(y, *end)
        swe = swe0
        while d <= stop:
            swe += daily_snow * wf
            row = {"date": pd.Timestamp(d),
                   "swe_inches": swe if swe_channel else np.nan,
                   "snow_depth_inches": depth, "new_snow_24hr": daily_snow}
            if temp_f is not None:
                row["mean_temp_f"] = temp_f
            rows.append(row)
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


def test_climatology_density_quality_light_beats_cement():
    # Same depth, same daily snowfall -- only the water fraction differs. The dry
    # climate (blower, wf=0.05) must score a HIGHER climatological quality than the
    # maritime cement factory (wf=0.22), and both must produce a real 0-100 quality.
    dry = trip.climatology(_obs([2021, 2022, 2023], wf=0.05), 10, 1, "new_snow")
    wet = trip.climatology(_obs([2021, 2022, 2023], wf=0.22), 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert dry[dowy]["quality"] is not None and wet[dowy]["quality"] is not None
    assert dry[dowy]["quality"] > wet[dowy]["quality"]


def test_tier1_density_unbiased_by_asymmetric_depth_gaps():
    # Regression: depth sensors (ultrasonic) drop out far more often than SWE
    # pillows -- and if the two channels are averaged over their OWN non-null days
    # instead of a SHARED mask, the mismatched denominators inflate the apparent
    # water fraction. Here every storm day has BOTH readings except one, where the
    # depth reading (only) is missing; the recovered water fraction must still match
    # the true wf=0.10 the storm was built at, not be skewed by the asymmetric gap.
    obs = _obs([2021, 2022, 2023], daily_snow=2.0, wf=0.10)
    # Blank the depth (not SWE) reading on one real snow day each year, mid-January.
    gap_dates = {pd.Timestamp(y, 1, 15) for y in [2021, 2022, 2023]}
    obs.loc[obs["date"].isin(gap_dates), "snow_depth_inches"] = np.nan
    obs.loc[obs["date"].isin(gap_dates), "new_snow_24hr"] = np.nan
    clim = trip.climatology(obs, 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    q = clim[dowy]["quality"]
    from ski.score import density_score
    # True wf=0.10 -> this exact quality; a mismatched-denominator bug would inflate
    # the apparent wf (and so DEFLATE quality) well below this reference value.
    assert abs(q - density_score(0.10)) < 3.0


def test_tier1_density_ignores_trace_noise_days():
    # Regression: a hair of sensor jitter (real SWE, near-zero depth) must not get
    # full weight in the ratio -- a single such day, mixed into an otherwise clean
    # wf=0.10 storm record, must not measurably move the recovered quality.
    obs = _obs([2021, 2022, 2023], daily_snow=2.0, wf=0.10)
    noisy_dates = {pd.Timestamp(y, 1, 20) for y in [2021, 2022, 2023]}
    mask = obs["date"].isin(noisy_dates)
    obs.loc[mask, "new_snow_24hr"] = 0.05          # trace depth jitter
    obs.loc[mask, "swe_inches"] += 1.0              # but a big, real SWE jump
    clim = trip.climatology(obs, 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    from ski.score import density_score
    assert abs(clim[dowy]["quality"] - density_score(0.10)) < 5.0


def test_density_quality_breaks_a_depth_tie_far_out():
    # Two mountains identical in depth/fresh/season -- the ONLY difference is snow
    # quality. Before this proxy they tied (abs_quality was always None); now the
    # cold/dry climate must out-rank the wet one on the historical baseline.
    dry = trip.climatology(_obs([2021, 2022, 2023], depth=50, daily_snow=1.5, wf=0.05),
                           10, 1, "new_snow")
    wet = trip.climatology(_obs([2021, 2022, 2023], depth=50, daily_snow=1.5, wf=0.22),
                           10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    rows = [trip.baseline_row(dry, dowy, "dry", "Cascades"),
            trip.baseline_row(wet, dowy, "wet", "Cascades")]
    trip.score_baseline(rows)
    by = {r["key"]: r for r in rows}
    assert by["dry"]["baseline_score"] > by["wet"]["baseline_score"]


def test_depth_only_station_without_temp_has_no_density_quality():
    # No SWE channel and no temperature (all-NaN swe, no mean_temp_f col) -> neither
    # tier has data -> abs_quality None, exactly like the live gate. It must still rank
    # (on base/fresh/season), just without quality.
    obs = _obs([2021, 2022, 2023])
    obs["swe_inches"] = np.nan
    clim = trip.climatology(obs, 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert clim[dowy]["quality"] is None
    assert trip.baseline_row(clim, dowy, "m", "R")["abs_quality"] is None


def test_tier2_temperature_density_cold_beats_warm():
    # Depth-only networks (no SWE) fall back to Tier-2: the snowfall-weighted air
    # temperature. A cold climate (10 F snow = blower) must score a HIGHER quality
    # than a near-freezing one (32 F = wet cement), and both must be real 0-100.
    cold = trip.climatology(_obs([2021, 2022, 2023], temp_f=10.0, swe_channel=False),
                            10, 1, "new_snow")
    warm = trip.climatology(_obs([2021, 2022, 2023], temp_f=32.0, swe_channel=False),
                            10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert cold[dowy]["quality"] is not None and warm[dowy]["quality"] is not None
    assert cold[dowy]["quality"] > warm[dowy]["quality"]


def test_tier1_swe_takes_precedence_over_temperature():
    # A station with BOTH a SWE channel and temperature uses the measured Tier-1 read,
    # not the temperature proxy: here SWE says light (wf=0.05) while the temp is warm
    # (34 F). Tier-1 must win, so quality lands high, matching a no-temp dry station.
    both = trip.climatology(_obs([2021, 2022, 2023], wf=0.05, temp_f=34.0),
                            10, 1, "new_snow")
    swe_only = trip.climatology(_obs([2021, 2022, 2023], wf=0.05), 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert abs(both[dowy]["quality"] - swe_only[dowy]["quality"]) < 1e-6


def test_density_prior_pure_when_trust_zero():
    # A known-broken network (trust 0, e.g. CDEC) must ignore its measured density
    # and read the regional prior outright -- the "drop CDEC from Tier-1" behavior.
    from ski.score import density_score
    prior_wf = 0.105                       # a maritime prior
    clim = trip.climatology(_obs([2021, 2022, 2023], wf=0.05),   # measured says light
                            10, 1, "new_snow",
                            density_prior=prior_wf, density_trust=0.0)
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert abs(clim[dowy]["quality"] - density_score(prior_wf)) < 0.5


def test_density_prior_blends_when_trust_partial():
    # At partial trust the quality lands strictly between the pure-measured and
    # pure-prior scores (shrinkage toward the literature).
    from ski.score import density_score
    prior_wf = 0.105
    measured = trip.climatology(_obs([2021, 2022, 2023], wf=0.05), 10, 1, "new_snow")
    blended = trip.climatology(_obs([2021, 2022, 2023], wf=0.05), 10, 1, "new_snow",
                               density_prior=prior_wf, density_trust=0.3)
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    q_meas = measured[dowy]["quality"]
    q_prior = density_score(prior_wf)
    assert q_prior < blended[dowy]["quality"] < q_meas   # between, closer to prior


def test_no_prior_leaves_measured_unchanged():
    # Legacy behavior: with no prior passed, quality is the pure measured read.
    a = trip.climatology(_obs([2021, 2022, 2023], wf=0.08), 10, 1, "new_snow")
    b = trip.climatology(_obs([2021, 2022, 2023], wf=0.08), 10, 1, "new_snow",
                         density_prior=None, density_trust=0.3)
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert a[dowy]["quality"] == b[dowy]["quality"]


def test_preservation_cold_beats_warm_and_falls_back_to_prior():
    # Measured from temperature: a climate that rarely thaws preserves better than one
    # that often does; both are real 0-100.
    cold = trip.climatology(_obs([2021, 2022, 2023], temp_f=15.0, swe_channel=False),
                            10, 1, "new_snow", preservation_prior=70, preservation_trust=0.6)
    warm = trip.climatology(_obs([2021, 2022, 2023], temp_f=36.0, swe_channel=False),
                            10, 1, "new_snow", preservation_prior=70, preservation_trust=0.6)
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert cold[dowy]["preservation"] > warm[dowy]["preservation"]
    # No temperature channel -> preservation is the regional prior outright.
    swe = trip.climatology(_obs([2021, 2022, 2023]), 10, 1, "new_snow",
                           preservation_prior=70, preservation_trust=0.0)
    assert abs(swe[dowy]["preservation"] - 70) < 1e-6


def test_season_swe_gain_uses_fresh_snowfall_ratio():
    # swe_gain season is converted to fresh SNOWFALL inches (~10:1), not settled depth
    # (3:1), so SWE stations aren't buried against snowfall-reporting stations. With
    # wf=0.1 the synthetic SWE accrues daily_snow*0.1 water; over the season that x10
    # should recover ~the raw snowfall total, far above the old x3.
    from config import SEASON_SWE_TO_SNOWFALL_RATIO, COVER_GATE
    clim = trip.climatology(_obs([2021, 2022, 2023], daily_snow=2.0, wf=0.1),
                            10, 1, "swe_gain")
    dowy = trip.target_dowy(date(2021, 2, 15), 10)
    season = clim[dowy]["season_in"]
    # sanity: the fresh ratio yields a materially larger season than the settled ratio
    assert SEASON_SWE_TO_SNOWFALL_RATIO > COVER_GATE["swe_to_depth_ratio"]
    assert season > 100   # ~mid-Feb of 2"/day @ 0.1 wf -> big on the fresh scale


def test_priors_lookup_helpers():
    # Region -> prior and source -> trust wiring the build/endpoint use.
    dp, dt = trip.density_priors("Tahoe & Sierra", "cdec")
    assert dp == 0.105 and dt == 0.0            # maritime prior, CDEC distrusted
    dp2, dt2 = trip.density_priors("Utah", "snotel")
    assert dp2 == 0.076 and dt2 == 0.3
    _, dt3 = trip.density_priors("Utah", "openmeteo")
    assert dt3 == 0.6                            # Tier-2 temperature trusted more
    pp, pt = trip.preservation_priors("Australia", "openmeteo")
    assert pp == 45 and pt == 0.6
    _, pt2 = trip.preservation_priors("Colorado", "snotel")
    assert pt2 == 0.0                            # no temp -> pure preservation prior


def test_baseline_row_carries_static_terrain_facts():
    # Mountain character (config.TERRAIN_STATS) doesn't depend on the climatology
    # or the target date at all -- baseline_row must carry the SAME real per-
    # mountain vertical/acreage/difficulty every time, not something derived.
    clim = trip.climatology(_obs([2021, 2022, 2023]), 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    jh = trip.baseline_row(clim, dowy, "jackson_hole", "Northern Rockies")
    gt = trip.baseline_row(clim, dowy, "grand_targhee", "Northern Rockies")
    assert jh["abs_vertical_ft"] == 4139
    assert jh["abs_acres"] == 2500
    assert jh["abs_pct_advanced_expert"] == 50
    assert gt["abs_vertical_ft"] == 2270
    # A key with no config.MOUNTAINS entry / no terrain data -> None, not a crash.
    unknown = trip.baseline_row(clim, dowy, "not_a_real_mountain_key", "Nowhere")
    assert unknown["abs_vertical_ft"] is None


def test_consistency_reliable_beats_boom_bust():
    # Same long-run mean snowfall, different inter-year variance. A steady climate must
    # score HIGHER consistency than a boom/bust one (the Sierra feast-or-famine knock).
    steady = pd.concat([_obs([y], daily_snow=2.0) for y in range(2011, 2021)],
                       ignore_index=True)
    boom = pd.concat([_obs([y], daily_snow=(3.5 if y % 2 else 0.5)) for y in range(2011, 2021)],
                     ignore_index=True)
    cs = trip.climatology(steady, 10, 1, "new_snow")
    cb = trip.climatology(boom, 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2015, 2, 15), 10)
    assert cs[dowy]["consistency"] is not None and cb[dowy]["consistency"] is not None
    assert cs[dowy]["consistency"] > cb[dowy]["consistency"] + 10   # clearly higher


def test_consistency_needs_min_years():
    # Fewer than CONSISTENCY_MIN_YEARS of history -> consistency None (drops out of the
    # ranking rather than reporting a variance from 3 years as if it were reliable).
    clim = trip.climatology(_obs([2021, 2022, 2023]), 10, 1, "new_snow")
    dowy = trip.target_dowy(date(2021, 1, 15), 10)
    assert clim[dowy]["consistency"] is None
    assert trip.baseline_row(clim, dowy, "m", "R")["abs_consistency"] is None


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
