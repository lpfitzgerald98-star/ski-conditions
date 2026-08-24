"""Point-in-time backtest of the LIVE board (config.GLOBAL_SCORE_WEIGHTS).

WHY THIS EXISTS: unlike the Trip Predictor (backtest_trip.py, Phase 2B refit),
the live global/regional leaderboard has NEVER been backtested -- config.py says
so explicitly at GLOBAL_SCORE_WEIGHTS's own docstring. This script closes that
gap for the components that CAN be honestly reconstructed from stored history,
and is explicit about the ones that can't.

WHAT CAN'T BE RECONSTRUCTED, AND WHY (read before touching the weights)
-------------------------------------------------------------------------
Two live-board inputs are fundamentally forward-looking and have no historical
archive to replay against:

  - `forecast` (16.13% of GLOBAL_SCORE_WEIGHTS): "how much more is coming" --
    there is no stored forecast-as-it-was-issued archive deep enough to replay
    (forecast_log.py only started logging recently). Always left None here, so
    it drops out of every mountain's blend via comparable._blend's existing
    renormalization -- NOT zero-weighted by editing the weights dict, just
    genuinely absent, the same as it would be for an offline mountain.

  - `wind` (22% of SNOW_QUALITY_WEIGHTS, folded into the `quality` component):
    raw_observations has NO wind column, ever -- wind quality is pulled LIVE
    from Open-Meteo's forecast API and never persisted. There is no historical
    wind to reconstruct at ANY window length. Always left None.

  - `thaw` (18% of SNOW_QUALITY_WEIGHTS): the live pipeline's thaw signal is the
    FORWARD incoming-rain/warmth risk from the forecast outlook -- also
    forecast-shaped, also unarchived. Always left None. (NOT the same as crust/
    refreeze below, which is backward-looking and IS reconstructable.)

  - `weather_q`/"warmth" (12% of SNOW_QUALITY_WEIGHTS): live current
    temp+wind+sky. Temp is stored but wind/sky are not, so a partial proxy would
    be weak and arguably misleading; left None rather than faked.

WHAT CAN BE RECONSTRUCTED, FAITHFULLY (not approximated -- the SAME functions
the live pipeline itself calls, because they were already pure functions of
`obs` + `as_of` with no live/network dependency):
  - base / fresh / season: pipeline.grade_base / fresh_snow_total /
    grade_season_to_date -- identical calls to mountain_scorecard's live path.
  - siting_factor: identical call, already proven point-in-time-safe (it's the
    same function backtest_trip.py's predict() already uses).
  - density (Tier 1 measured SWE:depth, Tier 2 temp-derived): Tier 1 via
    pipeline.measured_new_snow_density (obs+as_of only, verbatim). Tier 2 needs
    a snowfall-weighted recent temperature -- computed here from stored
    mean_temp_f over the SAME trailing window backtest_trip.py's own Tier-2
    fallback uses, just applied backward (as-of T) instead of forward (outcome
    window T..T+7).
  - crust (buried-crust severity, pillow stations only): pipeline.
    buried_crust_index -- also obs+as_of only, verbatim, already backward-only
    by construction (a rolling lookback window ending at as_of).
  - terrain (vertical/acreage/difficulty): static, TERRAIN_STATS, unconditional.

So `quality` here = density + crust only (renormalized within
snow_quality_score, same "missing component drops out" convention as live).
That is real information (roughly matching what SNOW_QUALITY_WEIGHTS assigns
those two: 0.28 + 0.20 = 0.48 of the full quality signal) -- not the full
picture, but not nothing, and not leaked.

LEAK DISCIPLINE: every prediction-side frame goes through ski.backtest.obs_asof
+ assert_no_leak, same choke point backtest_trip.py uses. Outcome side reuses
backtest_trip.outcome_quality_aware UNCHANGED (it's explicitly allowed to see
the future -- that's the ground truth we're scoring against).

Run:  python scripts/backtest_live.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))              # sibling import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))       # project root

from config import (COMPARABLE_FRESH_WINDOW_DAYS, DENSITY_MIN_SNOW_IN,  # noqa: E402
                    GLOBAL_SCORE_WEIGHTS, MOUNTAINS)
from ski import comparable, pipeline  # noqa: E402
from ski import score as score_mod  # noqa: E402
from ski.backtest import assert_no_leak, obs_asof  # noqa: E402
from ski.db import read_observations  # noqa: E402
from ski.regions import region_for  # noqa: E402

import backtest_trip as trip_bt  # noqa: E402 -- reuse TARGET_DATES / outcome / spearman

TARGET_DATES = trip_bt.TARGET_DATES
MIN_POOL = trip_bt.MIN_POOL
outcome_quality_aware = trip_bt.outcome_quality_aware
spearman = trip_bt.spearman

_OBS_CACHE: dict[str, pd.DataFrame] = {}


def _obs(key: str) -> pd.DataFrame:
    m = MOUNTAINS[key]
    st = pipeline.mountain_station(m)
    if st not in _OBS_CACHE:
        _OBS_CACHE[st] = read_observations(pipeline.DB_PATH, st)
    return _OBS_CACHE[st]


def _tier2_snow_temp(cut: pd.DataFrame, as_of: date, window_days: int) -> float | None:
    """Snowfall-weighted mean temp over the trailing `window_days` ending at
    `as_of` -- the Tier-2 density fallback, backward-looking mirror of
    backtest_trip._window_quality's forward-window version. obs+as_of only."""
    if cut is None or cut.empty or "mean_temp_f" not in cut:
        return None
    end = pd.Timestamp(as_of)
    start = end - pd.Timedelta(days=window_days - 1)
    win = cut[(cut["date"] >= start) & (cut["date"] <= end)]
    if win.empty or win["mean_temp_f"].notna().sum() == 0:
        return None
    depth_gain = win["new_snow_24hr"].clip(lower=0) if "new_snow_24hr" in win else pd.Series(dtype=float)
    t = win["mean_temp_f"]
    valid = t.notna() & (depth_gain.reindex(win.index).fillna(0.0) > 0)
    if not valid.any():
        return None
    s = depth_gain.reindex(win.index).fillna(0.0)
    total = float(s[valid].sum())
    if total < DENSITY_MIN_SNOW_IN:
        return None
    return float((t[valid] * s[valid]).sum() / total)


def predict(target: date) -> dict[str, dict]:
    """Point-in-time GLOBAL_SCORE_WEIGHTS-shaped rows for `target`, using ONLY
    data with date <= target (no lead-time shift -- the live board scores
    TODAY's conditions, so T == target, unlike the trip predictor's T = D - lead).
    Returns {key: row} for comparable.score_population."""
    rows: dict[str, dict] = {}
    for key, m in MOUNTAINS.items():
        wy = pipeline.mountain_wy_start(m)
        metric = pipeline.mountain_metric(m)
        cut = obs_asof(_obs(key), target)
        assert_no_leak(cut, target, label=f"live-backtest/{key}")
        if cut is None or cut.empty:
            continue
        siting = pipeline.siting_factor(key, cut, wy, metric)
        base = pipeline.grade_base(cut, as_of=target, wy_start_month=wy)
        season = pipeline.grade_season_to_date(
            cut, as_of=target, metric=metric,
            season_start_dowy=pipeline.mountain_season_start(m), wy_start_month=wy)
        fresh_7d = pipeline.fresh_snow_total(cut, as_of=target)
        fresh_72h = pipeline.fresh_snow_total(
            cut, as_of=target, window_days=COMPARABLE_FRESH_WINDOW_DAYS)
        offset = m.get("base_offset_in", pipeline.DEFAULT_BASE_OFFSET_IN)
        eff_depth = pipeline.settled_cover_depth(cut, target, base_offset_in=offset)
        in_season = score_mod.is_in_season(eff_depth, fresh_7d)

        density_ratio = pipeline.measured_new_snow_density(cut, target)
        if density_ratio is None:
            snow_temp = _tier2_snow_temp(cut, target, COMPARABLE_FRESH_WINDOW_DAYS)
            density_ratio = score_mod.density_from_temp(snow_temp)
        density_quality = score_mod.density_score(density_ratio)
        crust = pipeline.buried_crust_index(cut, target)
        quality = score_mod.snow_quality_score(
            density=density_quality, refreeze=crust, thaw=None, wind=None,
            weather_q=None).value

        season_snow = pipeline.season_snow_equivalent_in(season)
        terrain = pipeline.mountain_terrain(key)
        rows[key] = {
            "key": key,
            "region": region_for(m),
            "in_season": in_season,
            "abs_base_in": None if eff_depth is None else eff_depth * siting,
            "abs_fresh_in": None if fresh_72h is None else fresh_72h * siting,
            "abs_season_in": None if season_snow is None else season_snow * siting,
            "abs_forecast_in": None,   # unarchived -- see module docstring
            "abs_quality": quality,
            "abs_vertical_ft": terrain.get("vertical_drop_ft"),
            "abs_acres": terrain.get("skiable_acres"),
            "abs_pct_advanced_expert": terrain.get("pct_advanced_expert"),
        }
    return rows


def run(weights=None, dates=None, verbose=True):
    weights = weights or GLOBAL_SCORE_WEIGHTS
    dates = dates or TARGET_DATES
    per_date, pooled_pred, pooled_out = {}, [], []
    regional: dict[str, dict] = {}
    for D in dates:
        rows = list(predict(D).values())
        pred = comparable.score_population(rows, weights)
        pred = {k: v for k, v in pred.items() if v is not None}
        act = outcome_quality_aware(D)
        common = sorted(set(pred) & set(act))
        if len(common) < MIN_POOL:
            if verbose:
                print(f"  {D}: SKIPPED (pool {len(common)} < {MIN_POOL})")
            continue
        rho, n = spearman(pred, act)
        per_date[str(D)] = {"rho": rho, "n": n}
        for k in common:
            pooled_pred.append(pred[k]); pooled_out.append(act[k])
            reg = region_for(MOUNTAINS[k])
            regional.setdefault(reg, {"p": [], "a": []})
            regional[reg]["p"].append(pred[k]); regional[reg]["a"].append(act[k])
        if verbose:
            print(f"  {D}: rho={rho:+.3f}  n={n}")
    pooled_rho = float(pd.Series(pooled_pred).rank().corr(pd.Series(pooled_out).rank())) \
        if len(pooled_pred) > 3 else float("nan")
    reg_out = {}
    for reg, d in regional.items():
        if len(d["p"]) >= 8:
            reg_out[reg] = {"rho": float(pd.Series(d["p"]).rank().corr(pd.Series(d["a"]).rank())),
                            "n": len(d["p"])}
    return {"per_date": per_date, "pooled_rho": pooled_rho, "regional": reg_out,
            "n_pooled": len(pooled_pred)}


if __name__ == "__main__":
    print("=== LIVE BOARD BACKTEST (reconstructable components only) ===")
    print("forecast: excluded (unarchived) | wind/thaw/warmth: excluded (unarchived/leaky)")
    print("quality = density + crust only (renormalized)\n")
    res = run()
    print(f"\nPOOLED Spearman: {res['pooled_rho']:+.3f}  (n={res['n_pooled']})")
    print("\nPer-region:")
    for reg, d in sorted(res["regional"].items(), key=lambda x: -x[1]["rho"]):
        print(f"  {reg:22} rho={d['rho']:+.3f}  n={d['n']}")
    Path("live_backtest_results.json").write_text(json.dumps(res, indent=2, default=str))
    print("\nwrote live_backtest_results.json")
