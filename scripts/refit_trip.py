"""Phase-2 weight refit. Design frozen in VALIDATION_RUN.md.

Key optimisation over backtest_trip.run(): the expensive step is the
point-in-time climatology rebuild in predict(). That depends only on the DATE,
not on the weights, so rows are computed ONCE per date and cached; the fit loop
then only re-runs comparable.score_population, which is cheap.

Train 2021-2024, hold out 2025 + 2026. Adoption rule applied mechanically.
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
from scripts.backtest_trip import (LEAD_DAYS, MIN_POOL, outcome,  # noqa: E402
                                   predict)

TRAIN = [date(y, 2, 14) for y in (2021, 2022, 2023, 2024)]
TEST = [date(y, 2, 14) for y in (2025, 2026)]
COMPONENTS = sorted(TRIP_BASELINE_WEIGHTS)


def build_cache(dates):
    """rows + outcome per date -- the expensive part, done once."""
    cache = {}
    for D in dates:
        rows = predict(D, D - timedelta(days=LEAD_DAYS))
        cache[D] = (rows, outcome(D))
        print(f"  cached {D}")
    return cache


def rho_for(weights, cache, dates):
    """Pooled Spearman across `dates` for a weight vector."""
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
    """Random search over the simplex. Spearman is non-differentiable and the
    space is only 9-D, so a seeded random search is honest and reproducible."""
    rng = np.random.default_rng(seed)
    best_w = dict(TRIP_BASELINE_WEIGHTS)
    best = rho_for(best_w, cache, dates)
    start = best
    for i in range(n_iter):
        # Dirichlet draw, occasionally sparse so components can be dropped
        alpha = np.ones(len(COMPONENTS)) * rng.choice([0.4, 1.0, 3.0])
        v = rng.dirichlet(alpha)
        w = {c: float(v[j]) for j, c in enumerate(COMPONENTS)}
        r = rho_for(w, cache, dates)
        if r == r and r > best:
            best, best_w = r, w
    return best_w, start, best


if __name__ == "__main__":
    print("=== caching point-in-time rows (train) ===")
    cache = build_cache(TRAIN + TEST)

    cur_in = rho_for(TRIP_BASELINE_WEIGHTS, cache, TRAIN)
    cur_out = rho_for(TRIP_BASELINE_WEIGHTS, cache, TEST)
    print(f"\nCURRENT weights:  in-sample(2021-24)={cur_in:+.4f}   out-of-sample(2025-26)={cur_out:+.4f}")

    fitted, _, fit_in = fit(cache, TRAIN)
    tot = sum(fitted.values())
    fitted = {k: round(v / tot, 4) for k, v in fitted.items()}
    fit_out = rho_for(fitted, cache, TEST)
    print(f"FITTED  weights:  in-sample(2021-24)={fit_in:+.4f}   out-of-sample(2025-26)={fit_out:+.4f}")

    print("\n  component        current   fitted")
    for c in COMPONENTS:
        print(f"  {c:16}{TRIP_BASELINE_WEIGHTS[c]:>8.4f}{fitted[c]:>9.4f}")

    gain = fit_out - cur_out
    overfit = fit_in - fit_out
    print(f"\nADOPTION RULE: gain={gain:+.4f} (need >= +0.05); "
          f"in-out gap={overfit:+.4f} (need <= 0.10)")
    adopt = gain >= 0.05 and overfit <= 0.10
    print("DECISION:", "ADOPT fitted weights" if adopt else "KEEP current weights")

    json.dump({"current": TRIP_BASELINE_WEIGHTS, "fitted": fitted,
               "cur_in": cur_in, "cur_out": cur_out,
               "fit_in": fit_in, "fit_out": fit_out,
               "gain": gain, "overfit_gap": overfit, "adopt": adopt},
              open("refit_results.json", "w"), indent=2)
    print("wrote refit_results.json")
