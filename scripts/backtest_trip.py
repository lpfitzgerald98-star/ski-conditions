"""Phase-2 backtest of the Trip Predictor. Design frozen in VALIDATION_RUN.md.

Point-in-time discipline is enforced, not assumed: every prediction-side frame
goes through ski.backtest.obs_asof and is checked with assert_no_leak, which
raises rather than warns.

Run:  python scripts/backtest_trip.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MOUNTAINS, TRIP_BASELINE_WEIGHTS  # noqa: E402
from ski import comparable, pipeline, trip  # noqa: E402
from ski import score as score_mod  # noqa: E402
from ski.backtest import assert_no_leak, obs_asof, outcome_window  # noqa: E402
from ski.db import read_observations  # noqa: E402
from ski.regions import region_for  # noqa: E402

OUTCOME_WINDOW_DAYS = 7
LEAD_DAYS = 90
TARGET_DATES = [date(y, 2, 14) for y in (2021, 2022, 2023, 2024, 2025, 2026)]
MIN_POOL = 20

_OBS_CACHE: dict[str, pd.DataFrame] = {}


def _obs(key):
    m = MOUNTAINS[key]
    st = pipeline.mountain_station(m)
    if st not in _OBS_CACHE:
        _OBS_CACHE[st] = read_observations(pipeline.DB_PATH, st)
    return _OBS_CACHE[st]


def predict(target: date, T: date) -> dict[str, float]:
    """Point-in-time Trip Predictor ranking for `target`, knowing only data <= T."""
    clim_by_station, meta = {}, {}
    for key, m in MOUNTAINS.items():
        st = pipeline.mountain_station(m)
        wy, metric = pipeline.mountain_wy_start(m), pipeline.mountain_metric(m)
        meta[key] = {"station": st, "wy_start": wy, "region": region_for(m)}
        if st in clim_by_station:
            continue
        cut = obs_asof(_obs(key), T)
        assert_no_leak(cut, T, label=f"prediction/{key}")     # hard gate
        if cut is None or cut.empty:
            clim_by_station[st] = {}
            continue
        src = m.get("data_source", pipeline.DEFAULT_SOURCE)
        d_prior, d_trust = trip.density_priors(region_for(m), src)
        p_prior, p_trust = trip.preservation_priors(region_for(m), src)
        clim_by_station[st] = trip.climatology(
            cut, wy, pipeline.mountain_season_start(m), metric,
            density_prior=d_prior, density_trust=d_trust,
            preservation_prior=p_prior, preservation_trust=p_trust,
            siting_factor=pipeline.siting_factor(key, cut, wy, metric))
    rows = trip.roster_baseline_rows(target, list(MOUNTAINS), meta, clim_by_station)
    return rows


def outcome(target: date) -> dict[str, float]:
    """FROZEN outcome: mean realised skiability over (target, target+W]."""
    out = {}
    for key in MOUNTAINS:
        w = outcome_window(_obs(key), target, OUTCOME_WINDOW_DAYS)
        if w is None or w.empty:
            continue
        vals = []
        for _, r in w.iterrows():
            base = r.get("snow_depth_inches")
            if pd.isna(base):
                swe = r.get("swe_inches")
                base = None if pd.isna(swe) else swe * 3.0
            fresh = r.get("new_snow_24hr")
            fresh = 0.0 if pd.isna(fresh) else float(fresh)
            if base is None or pd.isna(base):
                continue
            s = score_mod.skiability_score(float(base), fresh, fresh, 0.0)
            if s.value is not None:
                vals.append(s.value)
        if vals:
            out[key] = float(np.mean(vals))
    return out


def _window_quality(w: pd.DataFrame) -> tuple[float, float, float]:
    """(recent_density_factor, thaw, refreeze) measured from the outcome window
    itself. Phase 2B; design frozen in VALIDATION_RUN.md. Neutral values when a
    channel is absent, so a station missing temp/SWE simply degrades to
    quantity-only rather than being dropped."""
    # --- density -> powder factor -------------------------------------------
    wf = None
    swe = w["swe_inches"] if "swe_inches" in w else pd.Series(dtype=float)
    depth_gain = w["new_snow_24hr"].clip(lower=0) if "new_snow_24hr" in w else pd.Series(dtype=float)
    if swe.notna().sum() >= 2 and depth_gain.notna().any():
        sg = swe.diff().clip(lower=0)
        paired = sg.notna() & depth_gain.notna() & (depth_gain > 0.3)
        if paired.any() and depth_gain[paired].sum() > 0:
            wf = float(sg[paired].sum() / depth_gain[paired].sum())
            wf = max(0.02, min(0.40, wf))
    if wf is None and "mean_temp_f" in w and w["mean_temp_f"].notna().any():
        t, s = w["mean_temp_f"], depth_gain.fillna(0.0)
        valid = t.notna() & (s > 0)
        if valid.any() and s[valid].sum() > 0:
            snow_temp = float((t[valid] * s[valid]).sum() / s[valid].sum())
            wf = score_mod.density_from_temp(snow_temp)
    density_factor = score_mod.density_powder_factor(wf)

    # --- thaw / refreeze -----------------------------------------------------
    thaw = refreeze = 0.0
    if "mean_temp_f" in w and w["mean_temp_f"].notna().any():
        t = w["mean_temp_f"].dropna()
        if len(t):
            thaw = float((t > 34.0).mean())
            if len(t) > 1:
                above = (t > 32.0).astype(int)
                refreeze = float((above.diff().abs() > 0).sum() / max(1, len(t) - 1))
    elif swe.notna().sum() >= 2:
        drops = swe.diff()
        thaw = float((drops < -0.1).mean()) if drops.notna().any() else 0.0
    return density_factor, min(1.0, thaw), min(1.0, refreeze)


def outcome_quality_aware(target: date) -> dict[str, float]:
    """Phase-2B FROZEN outcome: realised skiability with measured density, thaw
    and refreeze folded in (wind and sky unavailable retrospectively)."""
    out = {}
    for key in MOUNTAINS:
        w = outcome_window(_obs(key), target, OUTCOME_WINDOW_DAYS)
        if w is None or w.empty:
            continue
        dens, thaw, refreeze = _window_quality(w)
        vals = []
        for _, r in w.iterrows():
            base = r.get("snow_depth_inches")
            if pd.isna(base):
                swe_v = r.get("swe_inches")
                base = None if pd.isna(swe_v) else swe_v * 3.0
            fresh = r.get("new_snow_24hr")
            fresh = 0.0 if pd.isna(fresh) else float(fresh)
            if base is None or pd.isna(base):
                continue
            s = score_mod.skiability_score(
                float(base), fresh, fresh, 0.0,
                weather_q=None, refreeze=refreeze, thaw=thaw,
                recent_density_factor=dens)
            if s.value is not None:
                vals.append(s.value)
        if vals:
            out[key] = float(np.mean(vals))
    return out


def spearman(a: dict, b: dict) -> tuple[float, int]:
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return float("nan"), len(keys)
    x = pd.Series([a[k] for k in keys]).rank()
    y = pd.Series([b[k] for k in keys]).rank()
    return float(x.corr(y, method="pearson")), len(keys)


def run(weights=None, dates=None, verbose=True, outcome_fn=None):
    weights = weights or TRIP_BASELINE_WEIGHTS
    dates = dates or TARGET_DATES
    outcome_fn = outcome_fn or outcome
    per_date, pooled_pred, pooled_out = {}, [], []
    regional = {}
    for D in dates:
        T = D - timedelta(days=LEAD_DAYS)
        rows = predict(D, T)
        pred = comparable.score_population(rows, weights)
        pred = {k: v for k, v in pred.items() if v is not None}
        act = outcome_fn(D)
        common = sorted(set(pred) & set(act))
        if len(common) < MIN_POOL:
            if verbose:
                print(f"  {D}: SKIPPED (pool {len(common)} < {MIN_POOL})")
            continue
        rho, n = spearman(pred, act)
        per_date[str(D)] = {"rho": rho, "n": n}
        # top-5 hit rate: predicted top5 landing in actual top quartile
        top5 = [k for _, k in sorted(((pred[k], k) for k in common), reverse=True)[:5]]
        cutoff = np.percentile([act[k] for k in common], 75)
        per_date[str(D)]["top5_hit"] = sum(act[k] >= cutoff for k in top5) / 5.0
        # worst misses
        pr = {k: i for i, (_, k) in enumerate(sorted(((pred[k], k) for k in common), reverse=True))}
        ar = {k: i for i, (_, k) in enumerate(sorted(((act[k], k) for k in common), reverse=True))}
        miss = sorted(common, key=lambda k: -(pr[k] - ar[k]))
        per_date[str(D)]["worst_overrated"] = [(k, pr[k] + 1, ar[k] + 1) for k in miss[:3]]
        per_date[str(D)]["worst_underrated"] = [(k, pr[k] + 1, ar[k] + 1) for k in miss[-3:]]
        for k in common:
            pooled_pred.append(pred[k]); pooled_out.append(act[k])
            reg = region_for(MOUNTAINS[k])
            regional.setdefault(reg, {"p": [], "a": []})
            regional[reg]["p"].append(pred[k]); regional[reg]["a"].append(act[k])
        if verbose:
            print(f"  {D}: rho={rho:+.3f}  n={n}  top5_hit={per_date[str(D)]['top5_hit']:.2f}")
    pooled_rho = float(pd.Series(pooled_pred).rank().corr(pd.Series(pooled_out).rank())) \
        if len(pooled_pred) > 3 else float("nan")
    reg_out = {}
    for reg, d in regional.items():
        if len(d["p"]) >= 8:
            reg_out[reg] = {
                "rho": float(pd.Series(d["p"]).rank().corr(pd.Series(d["a"]).rank())),
                "n": len(d["p"]),
            }
    return {"per_date": per_date, "pooled_rho": pooled_rho,
            "regional": reg_out, "n_pooled": len(pooled_pred)}


if __name__ == "__main__":
    print("=== BASELINE (current weights) ===")
    res = run()
    print(f"\nPOOLED Spearman: {res['pooled_rho']:+.3f}  (n={res['n_pooled']})")
    print("\nPer-region:")
    for reg, d in sorted(res["regional"].items(), key=lambda x: -x[1]["rho"]):
        print(f"  {reg:22} rho={d['rho']:+.3f}  n={d['n']}")
    Path("backtest_results.json").write_text(json.dumps(res, indent=2, default=str))
    print("\nwrote backtest_results.json")
