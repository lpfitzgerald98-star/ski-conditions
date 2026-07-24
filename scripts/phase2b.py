"""Phase 2B ablation + refit against the quality-aware outcome.

Caches the expensive point-in-time rows and the outcome once per date, so the
ablation and the refit only re-run comparable.score_population.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import TRIP_BASELINE_WEIGHTS  # noqa: E402
from ski import comparable  # noqa: E402
from scripts.backtest_trip import (LEAD_DAYS, MIN_POOL, TARGET_DATES,  # noqa: E402
                                   outcome_quality_aware, predict)

TRAIN = [date(y, 2, 14) for y in (2021, 2022, 2023, 2024)]
TEST = [date(y, 2, 14) for y in (2025, 2026)]
COMPONENTS = sorted(TRIP_BASELINE_WEIGHTS)


def build_cache(dates):
    cache = {}
    for D in dates:
        cache[D] = (predict(D, D - timedelta(days=LEAD_DAYS)), outcome_quality_aware(D))
        print(f"  cached {D}", flush=True)
    return cache


def rho_for(weights, cache, dates):
    P, A = [], []
    for D in dates:
        rows, act = cache[D]
        pred = comparable.score_population(rows, weights)
        common = [k for k in sorted(set(pred) & set(act)) if pred[k] is not None]
        if len(common) < MIN_POOL:
            continue
        P += [pred[k] for k in common]
        A += [act[k] for k in common]
    if len(P) < 4:
        return float("nan")
    return float(pd.Series(P).rank().corr(pd.Series(A).rank()))


def fit(cache, dates, n_iter=4000, seed=0):
    rng = np.random.default_rng(seed)
    best_w, best = dict(TRIP_BASELINE_WEIGHTS), rho_for(TRIP_BASELINE_WEIGHTS, cache, dates)
    for _ in range(n_iter):
        v = rng.dirichlet(np.ones(len(COMPONENTS)) * rng.choice([0.4, 1.0, 3.0]))
        w = {c: float(v[j]) for j, c in enumerate(COMPONENTS)}
        r = rho_for(w, cache, dates)
        if r == r and r > best:
            best, best_w = r, w
    return best_w, best


if __name__ == "__main__":
    print("=== caching (Outcome B) ===", flush=True)
    cache = build_cache(TARGET_DATES)
    alld = TARGET_DATES

    base_rho = rho_for(TRIP_BASELINE_WEIGHTS, cache, alld)
    print(f"\nBASELINE pooled rho (Outcome B): {base_rho:+.4f}\n", flush=True)

    print("ABLATION (Outcome B)")
    print(f"{'component zeroed':18}{'pooled rho':>12}{'contribution':>14}")
    abl = []
    for c in COMPONENTS:
        w = {k: v for k, v in TRIP_BASELINE_WEIGHTS.items() if k != c}
        r = rho_for(w, cache, alld)
        abl.append((base_rho - r, c, r))
    for d, c, r in sorted(abl, reverse=True):
        print(f"{c:18}{r:>12.4f}{d:>+14.4f}")

    cur_in, cur_out = rho_for(TRIP_BASELINE_WEIGHTS, cache, TRAIN), rho_for(TRIP_BASELINE_WEIGHTS, cache, TEST)
    fitted, fit_in = fit(cache, TRAIN)
    tot = sum(fitted.values())
    fitted = {k: round(v / tot, 4) for k, v in fitted.items()}
    fit_out = rho_for(fitted, cache, TEST)

    print(f"\nCURRENT: in={cur_in:+.4f}  out={cur_out:+.4f}")
    print(f"FITTED : in={fit_in:+.4f}  out={fit_out:+.4f}")
    print(f"\n{'component':16}{'current':>9}{'fitted':>9}")
    for c in COMPONENTS:
        print(f"{c:16}{TRIP_BASELINE_WEIGHTS[c]:>9.4f}{fitted[c]:>9.4f}")

    gain, gap = fit_out - cur_out, fit_in - fit_out
    adopt = gain >= 0.05 and gap <= 0.10
    print(f"\nADOPTION: gain={gain:+.4f} (need >=+0.05) | in-out gap={gap:+.4f} (need <=0.10)")
    print("DECISION:", "ADOPT" if adopt else "KEEP current weights")

    json.dump({"base_rho": base_rho, "ablation": {c: d for d, c, _ in abl},
               "cur_in": cur_in, "cur_out": cur_out, "fit_in": fit_in,
               "fit_out": fit_out, "fitted": fitted, "gain": gain,
               "gap": gap, "adopt": adopt},
              open("phase2b_results.json", "w"), indent=2)
    print("wrote phase2b_results.json")
