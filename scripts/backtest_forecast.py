"""Mid-season check of the LIVE-ONLY inputs: does the forecast (and do wind/thaw/
warmth) actually earn their weight on the live board?

WHY THIS EXISTS: scripts/backtest_live.py validated the reconstructable majority
of GLOBAL_SCORE_WEIGHTS (pooled rho +0.670) but has to leave out `forecast` (16%)
and the live-only quality inputs (wind/thaw/warmth, ~79% of SNOW_QUALITY_WEIGHTS
after the Aug-2026 re-tune) because no history of them existed. forecast_log and
quality_log now record what each live card saw, once a day. This script replays
those logs, so it only has something to say once a season's worth has piled up
-- it runs automatically mid-season via .github/workflows/forecast-check.yml.

THREE QUESTIONS, ANSWERED SEPARATELY
------------------------------------
1. Forecast skill: is the logged 24/48/72h (and 4-10 day) prediction any good
   against what the station later recorded? pipeline.forecast_accuracy pairs
   them; reported per horizon as bias / MAE / rank correlation / storm hit rate,
   plus MAE skill vs a "predict zero" baseline.
2. Forecast rank value: on each date with logs, rebuild the live board exactly as
   backtest_live.predict does (point-in-time, leak-checked), then score it
   (A) without the forecast -- today's validated baseline -- and (B) with the
   logged 72h forecast, the input the live board really uses. Each is ranked
   against backtest_trip.outcome_quality_aware (the realised next-7-day
   skiability -- the same frozen outcome both earlier backtests use).
3. Live-only quality value: (C) = B plus the logged wind/thaw/warmth folded into
   `quality` via the SAME score.snow_quality_score the live card calls, and each
   of the three added alone, to see which (if any) helps.

A date is only eligible once its whole outcome window has been observed, and
only if MIN_POOL in-season mountains have both a prediction and an outcome.

Run:  python scripts/backtest_forecast.py [--since 2026-11-01] [--md report.md]
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))              # sibling import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))       # project root

from config import FORECAST_WEIGHT_SCHEDULE, GLOBAL_SCORE_WEIGHTS, MOUNTAINS  # noqa: E402
from ski.watercalendar import season_progress  # noqa: E402
from ski import comparable, forecast_log, pipeline, quality_log  # noqa: E402
from ski import score as score_mod  # noqa: E402
from ski.backtest import obs_asof  # noqa: E402

import backtest_live as live_bt  # noqa: E402
import backtest_trip as trip_bt  # noqa: E402

OUTCOME_WINDOW_DAYS = trip_bt.OUTCOME_WINDOW_DAYS
MIN_POOL = trip_bt.MIN_POOL
STORM_IN = {24: 4.0, 48: 6.0, 72: 8.0}   # "a real storm" per horizon, for hit rates
DEFAULT_STORM_IN = 10.0                   # medium-range horizons
# A variant must beat the baseline by this much mean rho, on most dates, to be
# called helpful (and lose by it to be called harmful). Below it: inconclusive.
DECISION_MARGIN = 0.02
MIN_DATES = 10                            # fewer eligible dates -> "not enough data"


# --- 1. forecast skill ---------------------------------------------------------
def forecast_skill(db_path, since: date, until: date) -> dict:
    frames = []
    for key in MOUNTAINS:
        df = pipeline.forecast_accuracy(key, db_path, as_of=until)
        if df.empty:
            continue
        df = df[pd.to_datetime(df["as_of"]).dt.date >= since]
        df = df.assign(mountain_key=key).dropna(subset=["predicted_inches"])
        frames.append(df)
    if not frames:
        return {}
    all_ = pd.concat(frames, ignore_index=True)
    # The 4-10 day band's horizon is whatever the source actually reached (151h,
    # 175h, 240h...), so bucket every horizon past 72h together.
    all_["bucket"] = all_["horizon_hours"].map(lambda h: f"{h}h" if h <= 72 else "4-10d")
    out = {}
    for h, g in all_.groupby("bucket", sort=False):
        p = g["predicted_inches"].astype(float)
        a = g["actual_inches"].astype(float)
        thr = STORM_IN.get(int(h[:-1]) if h.endswith("h") else 0, DEFAULT_STORM_IN)
        hit = int(((p >= thr) & (a >= thr)).sum())
        miss = int(((p < thr) & (a >= thr)).sum())
        false = int(((p >= thr) & (a < thr)).sum())
        mae = float((a - p).abs().mean())
        mae_zero = float(a.abs().mean())
        out[h] = {
            "n": int(len(g)),
            "n_mountains": int(g["mountain_key"].nunique()),
            "mean_predicted_in": round(float(p.mean()), 2),
            "mean_actual_in": round(float(a.mean()), 2),
            "bias_in": round(float((a - p).mean()), 2),          # + = under-forecast
            "mae_in": round(mae, 2),
            "mae_skill_vs_zero": None if mae_zero == 0 else round(1 - mae / mae_zero, 3),
            "pearson": _corr(p, a, "pearson"),
            "spearman": _corr(p, a, "spearman"),
            "storm_threshold_in": thr,
            "storm_pod": None if hit + miss == 0 else round(hit / (hit + miss), 3),
            "storm_far": None if hit + false == 0 else round(false / (hit + false), 3),
        }
    return out


def _corr(x: pd.Series, y: pd.Series, method: str):
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return None
    return round(float(x.corr(y, method=method)), 3)


# --- 2 + 3. rank value on the live board --------------------------------------
# name -> (forecast on?, live-only quality parts on, forecast weight override, compared vs)
# The B@w rows sweep the forecast's share of GLOBAL_SCORE_WEIGHTS (the other snow
# components rescaled to fill the rest, terrain held fixed), so the report
# suggests a weight rather than only a yes/no on the current one. B@w=0.1613 is
# the pre-2026-10-15 production weight: it's the "was the cut to 0.12 right?" row.
VARIANTS = {
    "A_no_forecast":           (False, set(), None, None),
    "B_forecast":              (True, set(), None, "A_no_forecast"),
    "B@w=0.04":                (True, set(), 0.04, "A_no_forecast"),
    "B@w=0.08":                (True, set(), 0.08, "A_no_forecast"),
    "B@w=0.1613":              (True, set(), 0.1613, "B_forecast"),
    "B@w=0.24":                (True, set(), 0.24, "A_no_forecast"),
    # The seasonal schedule (config.FORECAST_WEIGHT_SCHEDULE, per-mountain
    # season_progress) vs flat B -- the row that decides whether to enable it.
    "B_schedule":              (True, set(), "schedule", "B_forecast"),
    "C_forecast_full_quality": (True, {"wind", "thaw", "warmth"}, None, "B_forecast"),
    "B+wind":                  (True, {"wind"}, None, "B_forecast"),
    "B+thaw":                  (True, {"thaw"}, None, "B_forecast"),
    "B+warmth":                (True, {"warmth"}, None, "B_forecast"),
}


def weights_with_forecast(w: float) -> dict[str, float]:
    """GLOBAL_SCORE_WEIGHTS with forecast set to `w` (terrain fixed, other snow
    components rescaled) -- comparable.with_forecast_weight."""
    return comparable.with_forecast_weight(GLOBAL_SCORE_WEIGHTS, w)


def _logs(db_path):
    flog = forecast_log.read_log(db_path)
    qlog = quality_log.read_log(db_path)
    f72 = {}
    if not flog.empty:
        for r in flog[flog["horizon_hours"] == 72].itertuples(index=False):
            f72[(r.mountain_key, r.as_of.date())] = r.predicted_inches
    q = {}
    if not qlog.empty:
        for r in qlog.to_dict("records"):
            q[(r["mountain_key"], r["as_of"].date())] = r
    return f72, q


def _quality(row_key: str, D: date, logged: dict | None, use: set[str]) -> float | None:
    """The board's `quality` input with the chosen live-only parts switched on.
    density + buried crust are always in (backtest_live's reconstructable
    slice); `use` adds any of wind/thaw/warmth from the log. Same scorer the
    live card calls."""
    cut = obs_asof(live_bt._obs(row_key), D)
    density_ratio = pipeline.measured_new_snow_density(cut, D)
    if density_ratio is None and logged is not None and _ok(logged.get("density_ratio")):
        density_ratio = logged["density_ratio"]   # the live Tier-2 value it used
    if density_ratio is None:
        density_ratio = score_mod.density_from_temp(
            live_bt._tier2_snow_temp(cut, D, pipeline.COMPARABLE_FRESH_WINDOW_DAYS))
    crust = pipeline.buried_crust_index(cut, D)
    if logged is not None and _ok(logged.get("refreeze_index")):
        crust = max(crust or 0.0, logged["refreeze_index"])   # live: worse of the two
    lg = logged or {}
    return score_mod.snow_quality_score(
        density=score_mod.density_score(density_ratio),
        refreeze=crust,
        wind=lg.get("wind_quality") if "wind" in use and _ok(lg.get("wind_quality")) else None,
        thaw=lg.get("thaw_index") if "thaw" in use and _ok(lg.get("thaw_index")) else None,
        weather_q=(lg.get("weather_quality")
                   if "warmth" in use and _ok(lg.get("weather_quality")) else None),
    ).value


def _season_progress(key: str, D: date) -> float | None:
    w = MOUNTAINS[key].get("season_window")
    return season_progress(D, w["start"], w["end"]) if w else None


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def rank_value(db_path, since: date, until: date) -> dict:
    f72, q = _logs(db_path)
    logged_dates = sorted({d for (_, d) in f72} | {d for (_, d) in q})
    last_obs = _last_obs_date(db_path)
    dates = [d for d in logged_dates
             if since <= d <= until and last_obs is not None
             and d + timedelta(days=OUTCOME_WINDOW_DAYS) <= last_obs]
    per_date: dict[str, dict] = {}
    for D in dates:
        base_rows = live_bt.predict(D)
        act = trip_bt.outcome_quality_aware(D)
        res = {}
        for v, (use_fc, use_q, fc_w, _) in VARIANTS.items():
            rows = []
            for key, r in base_rows.items():
                r = dict(r)
                if use_fc:
                    fc = f72.get((key, D))
                    r["abs_forecast_in"] = fc if _ok(fc) else None
                if use_q:
                    r["abs_quality"] = _quality(key, D, q.get((key, D)), use_q)
                rows.append(r)
            row_weights = None
            if fc_w == "schedule":
                weights = GLOBAL_SCORE_WEIGHTS
                sched = {**FORECAST_WEIGHT_SCHEDULE, "enabled": True}   # test it even while off
                row_weights = lambda r, sched=sched, D=D: comparable.scheduled_weights(  # noqa: E731
                    _season_progress(r["key"], D), GLOBAL_SCORE_WEIGHTS, sched)
            else:
                weights = GLOBAL_SCORE_WEIGHTS if fc_w is None else weights_with_forecast(fc_w)
            pred = {k: s for k, s in comparable.score_population(
                rows, weights, row_weights=row_weights).items() if s is not None}
            if len(set(pred) & set(act)) < MIN_POOL:
                res = None
                break
            rho, n = trip_bt.spearman(pred, act)
            res[v] = {"rho": round(rho, 4), "n": n}
        if res:
            per_date[D.isoformat()] = res
    summary = {}
    if per_date:
        for v, (_, _, _, ref_name) in VARIANTS.items():
            vals = np.array([r[v]["rho"] for r in per_date.values()])
            ref = np.array([r[ref_name or v]["rho"] for r in per_date.values()])
            delta = vals - ref
            summary[v] = {
                "mean_rho": round(float(vals.mean()), 4),
                "vs": ref_name or "-",
                "mean_delta": round(float(delta.mean()), 4),
                "dates_better": int((delta > 0).sum()),
                "dates_worse": int((delta < 0).sum()),
                "verdict": _verdict(delta) if ref_name else "baseline",
            }
    return {"n_dates": len(per_date), "summary": summary, "per_date": per_date,
            "logged_dates": len(logged_dates)}


def _verdict(delta: np.ndarray) -> str:
    if len(delta) < MIN_DATES:
        return f"not enough data ({len(delta)} of {MIN_DATES} dates)"
    m = float(delta.mean())
    better = int((delta > 0).sum())
    if m >= DECISION_MARGIN and better > len(delta) / 2:
        return "HELPS -- earns its weight"
    if m <= -DECISION_MARGIN and better < len(delta) / 2:
        return "HURTS -- candidate to cut"
    return "inconclusive (within noise)"


def _last_obs_date(db_path) -> date | None:
    """Newest observation date in the DB: an outcome window (or forecast horizon)
    past it isn't observed yet, however much wall-clock time has gone by."""
    conn = sqlite3.connect(db_path)
    try:
        v = conn.execute("SELECT MAX(date) FROM raw_observations").fetchone()[0]
    finally:
        conn.close()
    return date.fromisoformat(v) if v else None


# --- report ---------------------------------------------------------------------
def to_markdown(skill: dict, value: dict, since: date, until: date) -> str:
    L = [f"## GladeGrade mid-season check: forecast + live-only quality",
         f"Window: **{since} -> {until}** | eligible board dates: **{value['n_dates']}** "
         f"(of {value['logged_dates']} logged; each needs a full {OUTCOME_WINDOW_DAYS}-day "
         f"outcome and {MIN_POOL}+ in-season mountains)", ""]
    L += ["### 1. Forecast skill (logged prediction vs what the station recorded)", ""]
    if not skill:
        L += ["_No logged forecasts with elapsed horizons in this window yet._", ""]
    else:
        L += ["| horizon | n | mtns | mean pred | mean actual | bias (+=under) | MAE | MAE skill vs 0 | spearman | storm POD | storm FAR |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        order = {"24h": 0, "48h": 1, "72h": 2, "4-10d": 3}
        for h, s in sorted(skill.items(), key=lambda kv: order.get(kv[0], 9)):
            L.append(f"| {h} | {s['n']} | {s['n_mountains']} | {s['mean_predicted_in']}\" | "
                     f"{s['mean_actual_in']}\" | {s['bias_in']}\" | {s['mae_in']}\" | "
                     f"{_f(s['mae_skill_vs_zero'])} | {_f(s['spearman'])} | "
                     f"{_f(s['storm_pod'])} (>= {s['storm_threshold_in']}\") | {_f(s['storm_far'])} |")
        L.append("")
    L += ["### 2-3. Does it improve the live board's ranking?", "",
          "Spearman vs realised next-7-day skiability (same frozen outcome as backtest_live/backtest_trip).", ""]
    if not value["summary"]:
        L += ["_Not enough eligible dates yet._", ""]
    else:
        L += ["| variant | mean rho | vs | mean delta | better / worse dates | verdict |",
              "|---|---|---|---|---|---|"]
        for v, s in value["summary"].items():
            L.append(f"| {v} | {s['mean_rho']:+.3f} | {s['vs']} | {s['mean_delta']:+.3f} | "
                     f"{s['dates_better']} / {s['dates_worse']} | {s['verdict']} |")
        L.append("")
    L += ["_A = today's backtest-validated board (no forecast). B = + logged 72h forecast "
          "at the current weight (0.12 since 2026-10-15); B@w = the same at forecast weight w "
          "(other snow components rescaled, terrain fixed); B@w=0.1613 is the old weight. "
          "B_schedule = the seasonal forecast weight (config.FORECAST_WEIGHT_SCHEDULE, "
          "0.12 -> 0.08 at 25% through each mountain's season) vs flat B -- enable it on a "
          "HELPS here. C = B + logged wind/thaw/warmth in `quality`; B+x adds one at a time. "
          f"Verdict needs >= {MIN_DATES} dates and a mean delta of at least {DECISION_MARGIN}._"]
    return "\n".join(L) + "\n"


def _f(v):
    return "-" if v is None else f"{v:+.3f}" if isinstance(v, float) else str(v)


def run(db_path=None, since: date | None = None, until: date | None = None) -> dict:
    db_path = db_path or pipeline.DB_PATH
    until = until or date.today()
    last_obs = _last_obs_date(db_path)
    if last_obs is not None:
        until = min(until, last_obs)   # never score against days not yet observed
    since = since or default_since(until)
    skill = forecast_skill(db_path, since, until)
    value = rank_value(db_path, since, until)
    return {"since": since.isoformat(), "until": until.isoformat(),
            "forecast_skill": skill, "rank_value": value,
            "markdown": to_markdown(skill, value, since, until)}


def default_since(until: date) -> date:
    """Start of the current Northern Hemisphere season (Nov 1) -- the check runs
    Dec-Mar, when NA/Europe/Japan supply the in-season pool."""
    y = until.year if until.month >= 11 else until.year - 1
    return date(y, 11, 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--until", type=date.fromisoformat)
    ap.add_argument("--md", help="also write the markdown report here")
    ap.add_argument("--json", default="forecast_backtest_results.json")
    args = ap.parse_args()
    res = run(since=args.since, until=args.until)
    print(res["markdown"])
    if args.md:
        Path(args.md).write_text(res["markdown"], encoding="utf-8")
    Path(args.json).write_text(json.dumps({k: v for k, v in res.items() if k != "markdown"},
                                          indent=2, default=str))
