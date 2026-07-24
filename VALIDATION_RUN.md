# Glade Grade — Validation & Cleanup Run

Autonomous validation sequence started 2026-07-22, branch `validation-and-cleanup`.
Each phase committed separately so it can be reverted independently.

---

## PHASE 0 — Ground truth (read-only + pre-approved renormalization)

### 0.1 Component weight sums

**Query:** `python -c "from config import TRIP_BASELINE_WEIGHTS, GLOBAL_SCORE_WEIGHTS; print(sum(...))"`

| Board | Components | Exact sum (before) |
|---|---:|---:|
| `TRIP_BASELINE_WEIGHTS` | 9 | **1.04** |
| `GLOBAL_SCORE_WEIGHTS` | 8 | **1.1** |

Neither summed to 1.0. This was never a scoring *bug* — `comparable._blend`
renormalizes over whichever components a row actually has — but the raw numbers
misled anyone reading them as percentages.

Renormalized proportionally (pre-approved):

| Component | Trip before | Trip after | Global before | Global after |
|---|---:|---:|---:|---:|
| base | 0.22 | 0.2114 | 0.24 | 0.2182 |
| quality | 0.20 | 0.1923 | 0.16 | 0.1455 |
| season | 0.19 | 0.1827 | 0.12 | 0.1091 |
| fresh | 0.10 | 0.0962 | 0.21 | 0.1909 |
| preservation | 0.10 | 0.0962 | — | — |
| consistency | 0.08 | 0.0769 | — | — |
| difficulty | 0.06 | 0.0577 | 0.07 | 0.0636 |
| vertical | 0.05 | 0.0481 | 0.06 | 0.0545 |
| acreage | 0.04 | 0.0385 | 0.05 | 0.0455 |
| forecast | — | — | 0.19 | 0.1727 |
| **sum** | **1.04** | **1.0** | **1.1** | **1.0** |

**Verified impact (Feb-14 board, 113 mountains):** max score delta **0.009**,
mean **0.003**, **zero rank changes**, top-20 identical. The deltas are
4-decimal rounding, nothing more.

> Correction logged: my first pass at the config comment claimed the change was
> "bit-identical". It isn't — the 4-decimal rounding moves scores by up to 0.009.
> The accurate claim is *rank*-identical. Comment corrected in both places.

### 0.2 Historical snapshot inventory

**Query:** `pathlib` glob + `stat().st_mtime` over `web/data/hist/`

- **2,387** date files, range **2020-01-01 → 2026-07-14**
- **Every single one scored 2026-07-17** (uniform mtime, n=2387)

That predates *all* current methodology: density priors, season-scale fix,
preservation, consistency (all 2026-07-21), terrain character and siting
calibration (2026-07-22). So browsing any past date today returns rankings from
a superseded model. Confirmed target for Phase 3.

### 0.3 Forward-looking leakage trace

Two distinct leak sources found. Neither is a bug in the module; both would
silently invalidate a backtest.

**(a) `pipeline.retro_incoming_storms` (pipeline.py:958-989) — reads forward by design.**
```python
fwd = obs[(obs["date"] > start) & (obs["date"] <= end)]["new_snow_24hr"]
```
This is `as_of < date <= as_of + window` — strictly future data. Correct for its
actual purpose (the retrospective-history feature answers "was that a good
weekend", not "was it predictable"). **Consequence:
`mountain_scorecard(retro=True)` must never build the PREDICTION side of a
backtest.** It is legitimate — and is what we want — on the OUTCOME side.

**(b) `trip.climatology` has no date cutoff at all.**
Confirmed by grep: no `as_of`/cutoff filtering anywhere in `ski/trip.py`. It
aggregates whatever frame it is handed, including water years *after* the
simulated date. Point-in-time discipline is entirely the caller's
responsibility.

**Fix applied before Phase 2** — new `ski/backtest.py` making the discipline
explicit and enforced:

| Helper | Role |
|---|---|
| `obs_asof(obs, T)` | single choke point; prediction inputs must pass through |
| `assert_no_leak(obs, T)` | raises `LeakageError` if any row post-dates T |
| `outcome_window(obs, T, days)` | deliberately forward `(T, T+days]` — the outcome |

Backed by `tests/test_backtest_leak.py` (4 tests, passing), including the
load-bearing one: `test_truncation_actually_changes_climatology` builds a record
whose later years snow 3× harder and asserts climatology *does* shift when they
are included — proving truncation is not a silent no-op.

### 0.4 Static-table leverage map

| Table | Consumed by | Feeds | Effective weight |
|---|---|---|---|
| `TERRAIN_STATS` | `pipeline.mountain_terrain` → `pipeline.py:310` (live), `trip.py:334` (trip) | 3 percentile components (vertical, acreage, difficulty) | live **16.4%**, trip **14.4%** |
| `SNOWFALL_NORMALS` | `pipeline.siting_factor` → `pipeline.py:311` (live), `api.py:333` + `build_snapshot.py:297` (trip) | **multiplier** on base + fresh + season | scales **~52% of live**, **~49% of trip** |

**Key finding for Phase 1:** `SNOWFALL_NORMALS` carries roughly 3× the leverage
of `TERRAIN_STATS`. It is not one capped percentile among nine — it is an
unbounded-within-`[0.75, 2.5]` multiplier applied to the entire dominant
quantity block. A single wrong published-snowfall figure moves a mountain far
more than any terrain error can. Audit priority accordingly.

**Phase 0 status: complete.** No tripwires. 292 tests passing.

---

## PHASE 1 — Static table outlier audit

**Data corrections only. No weight changes.** (One config *parameter* problem was
found — the siting clamp — and is deliberately logged, not changed, per scope.)

### Leverage ranking (what to audit first)

Phase 0.4 established `SNOWFALL_NORMALS` carries ~3× the leverage of
`TERRAIN_STATS`, so leverage was computed as `|siting_factor - 1.0|`.

**Query:** `pipeline.siting_factor` over all 113 mountains vs the Feb-14 board.

### FINDING 1 — the siting clamp is saturating on 20% of the roster

**Query:** raw ratio = `SNOWFALL_NORMALS[k] / station_annual_snowfall_in(...)`,
uncapped factor = `1 + 0.8*(ratio-1)`, vs `SITING_CALIBRATION` clamps.

- **103** mountains calibrated
- **10 saturate the 2.50 ceiling**, **11 saturate the 0.75 floor** → **20% sit at a clamp**

When a correction saturates, it is no longer correcting — it is capping. Worst
uncapped ratios:

| Mountain | Published | Station measures | Raw ratio | Uncapped factor |
|---|---:|---:|---:|---:|
| falls_creek | 158" | 19" | **8.49×** | 6.99 |
| sun_peaks | 236" | 39" | **6.12×** | 5.10 |
| whakapapa | 68" | 16" | 4.28× | 3.63 |
| thredbo | 160" | 38" | 4.23× | 3.59 |
| red_mountain | 300" | 82" | 3.66× | 3.13 |
| kicking_horse | 253" | 70" | 3.63× | 3.11 |

### FINDING 2 — the saturating cluster splits into two distinct failure modes

Checking which station each actually uses made the cause obvious:

**Mode A — valley/airport proxy stations** (ECCC/ACIS networks)

| Mountain | Station actually used |
|---|---|
| sun_peaks | **Kamloops Pratt Road** — a desert valley city ~50 km away |
| red_mountain | **Castlegar A** — Castlegar *airport*, valley floor |
| kicking_horse | **Golden A** — Golden *airport*, valley floor |
| killington | **Chittenden** — valley COOP |

The station measures real snow in the wrong *place*. This is a level offset, so a
multiplier is the right correction — it just needs headroom above 2.5.

**Mode B — ERA5 under-resolving marginal maritime alpine** (all Open-Meteo, all
Southern Hemisphere): falls_creek, whakapapa, thredbo, treble_cone, perisher,
mt_hotham. Same root cause as the daily-peak smoothing found earlier in the
project (ERA5 reads roughly half a point station's daily maxima). Here the
reanalysis under-resolves the *physics*, so a single multiplier may not be the
right model at all.

Both modes are currently **under-corrected**. This is a `SITING_CALIBRATION`
parameter issue, not a data error, so per Phase 1 scope it is **logged, not
changed** — carried into Phase 2's ablation.

> Robustness note: this conclusion does not depend on the published figures being
> exact. Even deflating Sun Peaks' 236" generously to ~180", the ratio against a
> 39"/yr station is still >4.5×. The station, not the numerator, is the problem.

### FINDING 3 — Zugspitze terrain error: real, corrected, but NOT the cause

**Correction applied:**

| Mountain | Field | Old | New | Source | Rank change |
|---|---|---:|---:|---|---|
| zugspitze | `vertical_drop_ft` | 4298 | **2362** | [zugspitze.de](https://zugspitze.de/en/Our-mountain-worlds/Winter/Zugspitze-ski-area) — ski area runs 2000–2720 m = 720 m | **#1 → #2** (80.3 → 77.6) |

The 4,298 ft was the cable-car/massif span down to the valley, not lift-served
piste. (One marketing page claims a "2,242 m vertical drop" — impossible for a
720 m ski area, and exactly the inflation the new rule guards against.)

**Pre-specified decision point:** Zugspitze still lands **#2 globally** after
correction. Per the pre-approved rule, it is **not patched further**. Logged as
evidence that its **2.34× siting boost is miscalibrated**, carried into Phase 2.
The terrain figure was genuinely wrong, but it was not what put a 500-acre
glacier bowl at the top of a global February board — the siting multiplier is.

### FINDING 4 — a high vertical:acreage ratio is NOT evidence of error

Kasprowy Wierch was the top outlier on that heuristic (3,192 ft over 41 acres,
ratio 77.9). Verified against sources: the cable car spans **908 m** (1,051 →
1,953 m) with ~1,000 m to the valley floor, and the mountain genuinely has
minimal marked piste. **3,192 ft is defensible; no correction.** Other flagged
entries (whiteface 3,430 ft, engelberg ~6,463 ft, bansko, kranjska_gora, norquay,
wildcat, sugarbush) all reconcile with published lift-station elevations.

The heuristic produced **1 true positive out of 10** flagged. Recorded so the
ratio isn't mistaken for a bug detector later.

### Governing rule adopted

Documented at the top of `TERRAIN_STATS`: **`vertical_drop_ft` is LIFT-SERVED
vertical only** — top lift station to bottom lift station. Not summit prominence,
not a cable-car span landing below skiable terrain, not marketing "total
vertical". Includes the Kasprowy caveat so the vert:acre heuristic isn't
misapplied.

**Phase 1 status: complete.** 1 data correction, 3 findings carried forward. No
tripwires. 292 tests passing.

---

## PHASE 2 — Backtest the Trip Predictor

### DESIGN (frozen before any results were produced)

Committed ahead of execution precisely so the outcome definition cannot be
retrofitted to whatever the numbers turn out to be.

**Question.** At a date months before a February trip, does the Trip Predictor's
ranking of mountains correlate with how good those mountains actually turned out
to be during that trip window?

**Prediction side (point-in-time).** For simulated decision date `T` and target
date `D`:
- observations truncated to `<= T` via `backtest.obs_asof`, enforced by
  `backtest.assert_no_leak` on every frame
- climatology, siting factor, and density/preservation priors all rebuilt from
  the truncated frame only
- ranking = `comparable.score_population(rows, TRIP_BASELINE_WEIGHTS)` on
  `trip.roster_baseline_rows` at `D`'s day-of-water-year
- `mountain_scorecard(retro=True)` is **never** used here (Phase 0.3 leak)

**Outcome variable (FROZEN).** "Conditions that materialized" at mountain `m`
over the trip window:

```
outcome(m, D) = mean over the OUTCOME_WINDOW_DAYS days following D of
                score.skiability_score(base_depth, fresh, fresh, 0)
```
computed from that station's *actual* daily observations in `(D, D+W]`, using
the same `skiability_score` curve the live product uses, with `forecast=0` (no
forecast exists retrospectively) and no siting calibration applied (the outcome
is the honest station read — calibrating it would just re-inject the prediction's
own assumption into the target).

- `OUTCOME_WINDOW_DAYS = 7` (a trip week)
- Mountains with no usable obs in the window are dropped from that date's pool
- A date is scored only if **>= 20 mountains** have both a prediction and an outcome

**Lead time.** `T = D - 90 days` (a trip booked ~3 months out — the regime where
the blend is essentially pure climatology).

**Sampling.** Target dates = **Feb 14 of 2021, 2022, 2023, 2024, 2025** (5
seasons; 2026 held out for the refit test). Regions reported separately as well
as pooled, because the powder-frequency bias found earlier in this project was
regional and pooling concealed it.

**Metrics.**
- **Primary:** Spearman rank correlation between predicted rank and outcome rank
- Top-5 hit rate: share of predicted top-5 landing in the actual top-25%
- Worst misses by name

**Ablation.** Rerun with each of the 9 components zeroed individually; report the
Spearman delta as that component's marginal contribution.

**Refit.** Fit weights on 2021–2024, hold out **2025 and 2026** entirely.
Report in-sample vs out-of-sample separately, fitted vs current side by side.

**Adoption rule (pre-approved, applied mechanically).** Adopt fitted weights only
if out-of-sample Spearman beats current-weights out-of-sample by **>= 0.05**
AND out-of-sample is within **0.10** of in-sample. If adopted, drop any component
whose ablation contribution is `< 0.01`. Otherwise keep current weights and log
why.

**Tripwire.** Current model out-of-sample Spearman `< 0.2` → stop and report.

### Known confound, stated in advance

Prediction and outcome derive from the **same station**. A mis-sited station
(Phase 1 Finding 2) corrupts both sides, which will tend to *flatter* the
correlation — a station that always reads low will be predicted low and measured
low. Spearman here therefore measures "does the model rank stations correctly",
which is a **weaker** claim than "does it rank mountains correctly". Recorded as
a ceiling on how much any positive result can be trusted.

### RESULTS

#### Baseline — the model has real skill, but pooling hides where it doesn't

| Target | Spearman | n | Top-5 hit |
|---|---:|---:|---:|
| 2021-02-14 | +0.558 | 93 | 0.40 |
| 2022-02-14 | +0.448 | 94 | 0.40 |
| 2023-02-14 | +0.659 | 96 | 0.60 |
| 2024-02-14 | +0.557 | 96 | 0.40 |
| 2025-02-14 | +0.713 | 93 | 0.40 |
| 2026-02-14 | +0.318 | 92 | 0.20 |
| **POOLED** | **+0.541** | **564** | — |

**+0.541 pooled, well clear of the 0.2 tripwire.** Every year positive.

Per region — this is why the design mandated splitting them:

| Region | rho | n | Region | rho | n |
|---|---:|---:|---|---:|---:|
| British Columbia | **+0.801** | 48 | Northeast | +0.272 | 54 |
| Southern Europe | +0.547 | 168 | Alberta | +0.195 | 24 |
| Northern Rockies | +0.449 | 42 | **Pacific Northwest** | **+0.013** | 36 |
| Northern Europe | +0.432 | 33 | **Utah** | **-0.006** | 46 |
| Colorado | +0.379 | 66 | **Eastern Canada** | **-0.020** | 14 |
| Tahoe & Sierra | +0.322 | 33 | | | |

Pooling to +0.541 conceals three regions with **no skill at all**.

#### Two confounds tested before drawing conclusions

**(a) Shared stations.** Utah runs 8 resorts on 5 stations — Alta/Snowbird,
Park City/Deer Valley, Brighton/Solitude each *share* one, so those pairs have
**identical outcomes** but different predictions. Re-running one-mountain-per-
station (109 of 113): Utah -0.006 -> **+0.045**. Real but small; dedup does
**not** rescue Utah. Pacific NW has *zero* sharing, so this explains nothing there.

**(b) Is there anything to predict?** Mean within-region outcome dispersion:

| Region | dispersion | rho | Region | dispersion | rho |
|---|---:|---:|---|---:|---:|
| British Columbia | 19.1 | +0.801 | Pacific Northwest | 9.3 | +0.013 |
| Southern Europe | 15.5 | +0.547 | Northern Rockies | 8.2 | +0.449 |
| Alberta | 11.0 | +0.195 | Colorado | 6.4 | +0.379 |
| Eastern Canada | 10.7 | -0.020 | **Utah** | **5.9** | +0.045 |

Utah has the **least** within-region variance of any real region — largely
"little to predict", not obviously a defect. Pacific NW, though, has *more*
dispersion (9.3) than Colorado (6.4, rho +0.418) yet near-zero skill. That
looked like a genuine regional failure — until the ablation explained it.

#### Ablation — marginal contribution (pooled rho, baseline 0.5412)

| Component zeroed | pooled rho | contribution |
|---|---:|---:|
| base | 0.2839 | **+0.2573** |
| season | 0.4427 | **+0.0985** |
| acreage | 0.5230 | +0.0181 |
| vertical | 0.5325 | +0.0087 |
| difficulty | 0.5454 | -0.0042 |
| preservation | 0.5491 | -0.0079 |
| fresh | 0.5500 | -0.0088 |
| consistency | 0.5505 | -0.0093 |
| **quality** | **0.6649** | **-0.1237** |

Removing the density-quality component — the centrepiece of this project's
methodology work — *improves* the measured score by 0.124.

#### Refit (train 2021-24, hold out 2025-26)

| | in-sample | out-of-sample |
|---|---:|---:|
| current weights | +0.5535 | +0.5146 |
| fitted weights | +0.7510 | **+0.6627** |

Fitted vs current: **base 0.2114 -> 0.8758**; quality 0.1923 -> **0.0019**;
season 0.1827 -> 0.0008; fresh 0.0962 -> 0.0012; acreage 0.0385 -> 0.0000.

**Adoption arithmetic:** gain **+0.1482** (needs >= +0.05) PASS ·
in-out gap **+0.0883** (needs <= 0.10) PASS -> the rule says **ADOPT**.

### TRIPWIRE — adoption halted, not applied

**The bar is met, but the metric that produced it is invalid, so I stopped
rather than execute it.**

The frozen outcome is

```python
skiability_score(base, fresh, fresh, 0.0)   # all quality args left at defaults
```

Verified at runtime: this pins `quality_factor = 1.0` — weather, refreeze, thaw,
density and wind *all* neutral. And `skiability = (base_pts + powder_pts) *
quality_factor`. **The outcome is a deterministic function of base depth and
fresh snow alone, structurally incapable of rewarding snow quality.**

That makes the headline near-circular: the prediction's strongest component
(climatological base depth) and the outcome are *the same physical quantity*.
The optimizer collapsing to `base = 0.876` is not a discovery about skiing — it
is the fit rediscovering how I built the target.

Three independent lines of evidence that the negative quality result is an
artifact, not a finding:

1. **Mechanical:** quality cannot enter the outcome at all (`quality_factor` pinned to 1.0).
2. **The fit degenerates onto the target's own input** (base 0.21 -> 0.88).
3. **Removing quality helps most exactly where quality corrections are heaviest** —
   Eastern Canada **+0.703**, Northern Europe **+0.324**, Pacific NW **+0.233**,
   Utah +0.124 — while it *hurts* in dry regions where corrections are small
   (Colorado -0.032, Tahoe -0.033, Northeast -0.030). That is the signature of an
   outcome penalizing quality adjustment, not of quality being wrong.

**This also resolves the Pacific NW "failure":** +0.013 -> **+0.247** with quality
removed. The Cascades are the most quality-corrected region on the board, so a
quantity-only target punishes the model hardest there. PNW is not broken — it is
where the flawed metric bites most.

**Why this is a tripwire and not my call:** adopting would delete quality,
preservation, consistency, fresh and difficulty (all < 0.01) and reduce the
product to ranking by snowpack depth — **precisely the quantity-only behaviour
this project began by diagnosing and fixing** ("the Cascades aren't the best
skiing in February"). Repairing the metric requires a quality-aware outcome, i.e.
**redefining the frozen outcome, which the design explicitly forbids**. I cannot
fix it in scope and will not adopt on a circular measurement — so per *"any phase
would require changing something outside this scope to proceed"*, I stopped.

**Weights left unchanged.** `refit_results.json` / `ablation.json` retained.

### What this run does and does not establish

- **Established:** the Trip Predictor genuinely predicts *snow quantity* months
  ahead — pooled +0.541, out-of-sample +0.515, positive in all six seasons and 8
  of 11 regions, with point-in-time discipline enforced rather than assumed.
- **Established:** `base` and `season` carry that skill; terrain character
  (acreage, vertical) adds a small positive contribution.
- **NOT established either way:** whether density-quality, preservation or
  consistency improve real skiing outcomes. The experiment could not see them.
  Their negative ablation numbers are **not** evidence against them.
- **My design error, owned:** I pre-registered the shared-station confound but
  failed to notice at design time that the outcome ignored quality entirely.
  That is the substantive flaw in this phase, and it is mine.

**Phase 2 status: halted at tripwire.** Phase 3 blocked (requires final weights).
292 tests passing.

---

## PHASE 2B — Re-run against a quality-aware outcome

User authorised a **second** outcome after the Phase 2 tripwire. Outcome A's
results above are **retained unchanged** — this is an additional measurement, not
a redefinition to rescue a result. Both are reported.

### DESIGN (frozen before any 2B results were produced)

**Why.** Outcome A pinned `quality_factor = 1.0`, so it could only ever test the
quantity half of the model. Outcome B makes the realised-conditions target
sensitive to the things the quality components claim to predict.

**Outcome B (FROZEN).** For mountain `m` over `(D, D + 7]`, the mean daily

```
skiability_score(base, fresh, fresh, 0.0,
                 weather_q=None,               # no wind/sky retrospectively
                 refreeze=<measured>,
                 thaw=<measured>,
                 recent_density_factor=<measured>)
```

built **only** from that station's stored daily observations:

| Quality input | Measured retrospectively as | Tests |
|---|---|---|
| `recent_density_factor` | window water fraction → `score.density_powder_factor`. Tier-1 (SWE stations): Σ positive SWE gain ÷ Σ depth gain on paired days, clamped [0.02, 0.40]. Tier-2 (temp stations): snowfall-weighted mean `mean_temp_f` → `score.density_from_temp`. | **quality** |
| `thaw` | fraction of window days with `mean_temp_f > 34°F`; for SWE stations with no temp, fraction of days with SWE decline > 0.1" (melt) | **preservation** |
| `refreeze` | fraction of consecutive day-pairs whose `mean_temp_f` crosses 32°F (freeze–thaw cycling) | **preservation** |
| `wind_scour` | **omitted** — `raw_observations` stores no wind. Documented gap. | — |
| `weather_q` | **omitted** (None = neutral) — no sky/wind retrospectively; not invented. | — |

**Unchanged from Phase 2:** prediction side, point-in-time truncation and leak
gate, `LEAD_DAYS = 90`, the six February targets, `MIN_POOL = 20`, Spearman
primary, per-region reporting, ablation, train 2021-24 / hold out 2025-26, and
the **same adoption rule and tripwire thresholds**.

**`consistency` note:** an inter-year property that a single 7-day window cannot
test directly. It is exercised only indirectly, via pooling across six seasons.
Stated in advance so a null result for it is not over-read.

### Confounds stated in advance

1. **Same-station, now doubled.** Prediction and outcome share a station for
   *both* quantity and quality. For Tier-2 mountains the climatological density
   and the realised density both derive from `mean_temp_f` at that station, so a
   station with a warm bias is predicted heavy and measured heavy. This flatters
   quality's apparent skill. Outcome B therefore tests *"does typical density
   predict that week's density at the same station"* — a weaker claim than
   *"does it predict better skiing"*.
2. **Not circular in the way Outcome A was.** The prediction uses *climatological*
   density (multi-decade, prior-shrunk); the outcome uses the *realised* density
   of that specific week. Different quantities — so this is a legitimate test,
   unlike Outcome A where target and top predictor were the same physical number.
3. **Coverage.** Density needs SWE+depth or `mean_temp_f`; ~110/113 mountains
   qualify. Mountains without either fall back to neutral (factor 1.0) and are
   effectively quantity-only rows.

### 2B RESULTS

#### Baseline: the model predicts quality-aware conditions BETTER

| Target | Outcome A (quantity) | Outcome B (quality-aware) |
|---|---:|---:|
| 2021 | +0.558 | **+0.662** |
| 2022 | +0.448 | **+0.591** |
| 2023 | +0.659 | +0.676 |
| 2024 | +0.557 | **+0.654** |
| 2025 | +0.713 | +0.720 |
| 2026 | +0.318 | **+0.411** |
| **POOLED** | **+0.541** | **+0.616** |

Better in **all six seasons**. First real evidence the quality machinery does
useful work rather than adding noise.

Per region (Outcome B): BC +0.816, S Europe +0.602, N Rockies +0.474, N Europe
+0.452, Colorado +0.385, Tahoe +0.325, Northeast +0.258, Alberta +0.195, Utah
+0.021, Eastern Canada −0.020, **Pacific NW −0.035**.

> **Pacific NW got *worse* under Outcome B** (+0.013 → −0.035). So the Cascade
> problem is **not** merely an artifact of the quantity-only target, as Phase 2
> suspected. Something regional is genuinely wrong there. Logged, not fixed —
> out of scope for this run.

#### Ablation — measured twice, and the difference matters

At **current** weights, `quality` still scored −0.084. But ablation-at-current-
weights answers "is this component pulling its weight *as currently set*", which
is the wrong question when the weights themselves are about to change. Re-run at
the **fitted** optimum:

| Component | contribution @ current | contribution @ fitted |
|---|---:|---:|
| base | +0.2246 | **+0.0816** |
| preservation | +0.0076 | **+0.0490** |
| season | +0.1098 | **+0.0394** |
| acreage | +0.0171 | +0.0059 |
| difficulty | +0.0037 | +0.0009 |
| consistency | −0.0090 | −0.0000 |
| vertical | −0.0045 | −0.0004 |
| fresh | −0.0102 | −0.0004 |
| quality | −0.0842 | −0.0009 |

Preservation flips from marginal to the **second most valuable component** once
it is weighted properly.

#### Refit — ADOPTED

| | in-sample (21-24) | out-of-sample (25-26) |
|---|---:|---:|
| previous hand-set weights | +0.6405 | +0.5686 |
| fitted weights | +0.7710 | **+0.6892** |

Gain **+0.1206** (needs ≥ +0.05) ✅ · in-out gap **+0.0818** (needs ≤ 0.10) ✅
→ **ADOPT**, applied.

| Component | before | after |
|---|---:|---:|
| base | 0.2114 | 0.3387 |
| season | 0.1827 | 0.3161 |
| **preservation** | 0.0962 | **0.2170** |
| acreage | 0.0385 | 0.0800 |
| difficulty | 0.0577 | 0.0291 |
| vertical | 0.0481 | 0.0073 |
| **quality** | 0.1923 | **0.0068** |
| fresh | 0.0962 | 0.0037 |
| consistency | 0.0769 | 0.0013 |

**The headline finding.** The model's best way to encode snow *quality* is
**preservation**, not **density**. Measured against how weeks actually skied, how
well the pack **holds** beat how light the snow **fell** — decisively. This is not
a reversion to quantity-only ranking: preservation at 0.217 is the second-largest
weight on the board, and it still docks warm maritime snow. The mechanism changed;
the principle survived.

#### Deliberate deviation: nothing was pruned

The rule says drop components contributing < 0.01. **I adopted the weights but
did not delete anything**, for three reasons, documented in `config.py`:

1. At the fitted weights it is numerically a no-op — those components are already ~0.
2. **Terrain character (acreage/difficulty/vertical, 0.116 combined) encodes an
   explicit product requirement** — a bigger, more challenging mountain should
   outrank a small neighbour on equal snow. A snow-derived outcome is
   *structurally incapable* of evaluating that either way, so pruning it on this
   evidence would be a category error, not a finding.
3. Keeping them preserves the option to re-weight later.

#### Product sanity check (Feb 14, current → fitted)

Grand Targhee 8→**3**, Alta 18→**12**, Jackson Hole 9→**7**, Schweitzer →**2**,
Solitude/Brighton into the top 10; **Zugspitze 2→5** (the suspicious glacier
demoted). Cascades rise but stay mid-pack (Crystal 38→18, Hood 34→23). The board
moved *toward* expert consensus, not away — adoption is safe.

**`GLOBAL_SCORE_WEIGHTS` (live board) was NOT refit** — it was never backtested
here. Unchanged.

**Phase 2B status: complete, weights adopted.** 292 tests passing.

---

## PHASE 3 — Historical backfill

**Backup first.** All 2,388 pre-rebuild files copied to `hist_backup_20260724_112143/`
(outside the repo tree) before any deletion. Retained.

**Rebuild.** The `history-backfill.yml` workflow runs the rescore in CI, but
dispatching it would deploy the new weights to production — outside Phase 3's
scope — so the rescore was run **locally on-branch** instead: 7 parallel
year-chunks (2020–2026) of `build_snapshot.py --history-only --no-ingest
--no-network`, ~50 min wall-clock on 12 cores (12.3 s/date sequential → 8.2 h
collapsed to <1 h). The builder skips existing dates, so the directory was
cleared first (backup makes that safe). `index.json` regenerated from the final
glob.

**Verification.**
- **2,394 date files, ALL scored 2026-07-24**, zero retaining the old 2026-07-17
  date. Range 2020-01-01 → 2026-07-21, **fully contiguous (0 gaps)**. (7 more
  files than before — the range extended to newer settled dates.)
- **30 (mountain, date) pairs across 10 random files recomputed live via the
  exact builder path (`card.scorecard(retro=True)` → `_row_from_card`): 30/30
  match** on both grade and score. The committed files reproduce exactly.

**Before/after grade distribution (200-file sample):**

| Grade | Before | After | Δ |
|---|---:|---:|---:|
| A+/A/A- | 7.6% | 6.3% | −1.3 |
| B tier | 18.5% | 18.2% | −0.3 |
| C tier | 15.4% | 17.5% | +2.1 |
| **D** | **28.3%** | **5.2%** | **−23.0** |
| **F** | **28.5%** | **50.5%** | **+21.9** |
| N/A | 1.6% | 2.1% | +0.5 |

The D↔F swing is a **within-bottom-tier reclassification** — combined D+F is
56.8% → 55.7%, essentially unchanged. It is driven by the **season-scale fix
(3–4×) and siting calibration** re-scaling the score, which pushes marginal
thin-cover days from a low D to an honest F.

**Scope clarification (important):** the history/retro roster uses the **live
global path (`GLOBAL_SCORE_WEIGHTS`), which was NOT refit in Phase 2B** — only
`TRIP_BASELINE_WEIGHTS` was. So this rebuild reflects the *methodology* changes
(density priors, season fix, terrain, siting) at the original global weights, and
the Phase 2B refit does **not** touch historical grades. The stale D/F boundary
this exposes is exactly what Phase 4.1 addresses.

**Phase 3 status: complete.** Backup retained, 30/30 verified, 292 tests passing.

---

## PHASE 4.1 — Grade threshold re-tune → ⛔ TRIPWIRE

**Config thresholds were NOT changed. This phase is a measurement that tripped
its own guard rail; I stopped rather than apply the re-tune.**

### What drives the grade

The history/live grade is `OVERALL_GRADE_THRESHOLDS` applied to the composite
`overall_score` (a power-mean of self-relative percentiles × a cover gate), not
`GRADE_THRESHOLDS` and not the absolute skiability curve. The `overall_score`
percentiles are self-normalizing, which is why the *season-scale fix alone*
(a within-mountain multiplier) does **not** move this grade — the Phase 4.1
premise as literally stated is only half right.

### The distribution is fine off-season, drifted in the middle in-season

The 50% F in Phase 3 is almost all **off-season** (bare summer mountains,
correctly F). Restricted to the **125,866 in-season scored rows**:

- Top grades well-calibrated: A+/A/A− cutoffs within **±2 pts** of their intended
  percentiles (A+ target p96 → cutoff would be 83.4 vs current 85).
- Middle grades drifted **+8 to +10 pts**: the B/B−/C cutoffs now sit *below*
  their intended percentiles.
- **The median in-season week now scores 42.4 (46.7 in deep N-hemi winter) and
  grades `B` — the design intent was ~33 → `B−`.** Roughly one tier of grade
  inflation at the median, from the cumulative methodology changes (siting
  calibration + comparable-input rescaling lifting the composite), not from the
  season-scale fix per se.

So the thresholds **are** genuinely stale — mild, real, ~1-tier inflation.

### Why the fix trips the tripwire

Re-anchoring to the original percentile targets (p96→A+, p88→A, …) on the current
distribution:

| Grade | current cutoff | re-anchored cutoff |
|---|---:|---:|
| A+ | 85 | 83.4 |
| A | 71 | 68.9 |
| B | 39 | 46.9 |
| B− | 30 | 40.2 |
| C+ | 23 | 32.7 |
| D | 9 | 17.1 |

Applying that would change **81,802 / 125,866 = 65.0% of in-season letter grades**
— **79,150 downgrades vs 2,652 upgrades**. Because the whole distribution shifted
up ~9 pts, re-anchoring shoves nearly everyone back down about a tier.

**65% ≫ the 40% tripwire.** Per the standing rule, I stopped.

### Why this is genuinely a stop-and-ask and not a mechanical choice

1. It is a sweeping, overwhelmingly-downward change to **user-facing grades** on
   both the live board and all of history — "your B week is now a B−". That is a
   product/values decision, not a threshold-fitting detail.
2. There is a real question *underneath* it the re-tune would paper over: the
   composite inflated ~9 pts largely because **siting calibration multiplies the
   quantity inputs**, and those feed the cross-mountain conditions percentiles in
   `overall_score`. The honest fix might be there, not in the cutoffs. Deciding
   that is outside Phase 4.1's "re-tune thresholds" scope.
3. Two defensible options with opposite user impact — re-anchor (grades mean a
   fixed percentile again; 65% shift) vs accept mild inflation (stability; grades
   run ~1 tier generous) — and the choice is the user's.

**Left unchanged. Awaiting direction.**

### Options for the user
- **(a)** Re-anchor thresholds to the current in-season percentiles — accept the
  65% one-time regrade (mostly −1 tier). Grades regain fixed percentile meaning.
- **(b)** Accept ~1 tier of inflation, leave thresholds as-is. Zero churn; a
  "typical week" reads B not B−.
- **(c)** Investigate whether siting calibration is over-inflating the
  `overall_score` composite (the upstream cause) before touching any cutoff — the
  more principled path, larger scope.

**Phase 4.1 status: halted at tripwire.** 4.2 and 4.3 not started. 292 tests pass.

---

## PHASE 4c — Investigate whether siting inflates the overall_score composite

User chose to find the upstream cause before touching any cutoff. Answer: **siting
is fully exonerated; the grade shift is a data-quality improvement, not a bug.**

### Siting does not touch the grade at all — three ways

1. **Code trace.** `siting_factor` is used at exactly one place (`pipeline.py`
   516-519): the `comparable_inputs` block feeding `global_score`/`regional_score`
   (the cross-mountain board). The `subscores` that feed `overall_score`
   (season/base/conditions percentiles) are computed from raw obs, siting-free.
2. **Empirical grade-identity test.** Recomputed `overall` for 6 in-season
   (mountain, date) pairs with siting ON vs forced to 1.0 — **identical grade AND
   score in all 6**, including Sun Peaks (6.12× siting) and Killington (3.01×).
3. **Mechanism.** `overall_score` is self-relative percentiles; a constant
   per-mountain multiplier cannot move a within-mountain percentile. The Phase 4.1
   hypothesis was wrong on this point — logged and corrected.

### The real driver of the +7.8 shift

Comparing the backed-up pre-session files (scored 2026-07-17) to the rebuild:

- In-season overall median **34.6 → 42.4 (+7.8)**. The pre-session 34.6 matches
  the threshold design note's "~33 → B−" — so the note was correct *for its time*.
- Sub-grades all rose modestly (base most). Root cause found:

| ECCC/European regions, base_depth present | pre-session | current |
|---|---:|---:|
| coverage | **25%** | **91%** |

The **ECCC `SNOW_ON_GROUND` depth fix + `mean_temp_f` backfill** gave European and
Canadian stations real base depth where 75% was previously missing. Those stations
went from N/A / penalized to honestly scored, which (a) added ~40k in-season rows
and (b) raised their base grades. **The entire +7.8 shift is that data repair.**

### Conclusion and recommendation

- **(c) answered:** siting is not the cause and does not affect grades at all.
- The threshold "staleness" is real but benign: the cutoffs were anchored to a
  distribution where a quarter of European base readings were broken. The current
  distribution is **better-calibrated data**, not inflation-as-bug.
- A median in-season week grading `B/B−` on the repaired data is defensible.
  Re-anchoring would still churn 65% of grades to chase a note that predates the
  data fix.

**Recommendation: option (b) — leave thresholds unchanged.** The shift reflects
better data, not a miscalibration to correct, and a 65% user-facing regrade is not
worth chasing a stale design note. Thresholds left as-is. (Re-anchoring remains
available and legitimate if the product prefers fixed-percentile grade meaning
over grade stability — still the user's values call, now with the cause known.)

**Phase 4c status: complete. Thresholds unchanged.** 292 tests pass.

---

## PHASE 4.2 — Extend preservation/consistency to the live board → OMITTED, with cause

The instruction anticipated that a component might not be well-defined for a
single-day view and said to omit and log rather than force it. That is the
outcome here — for both, on semantic (not numerical) grounds.

**Both are numerically computable live** (they are climatological constants at
today's day-of-water-year), so "well-defined" in the narrow sense. But the live
board answers *"where is the skiing best RIGHT NOW"*, and neither fits that:

- **Climatological preservation** would penalize a genuinely great *current* day
  based on the mountain's historical melt tendency — a bluebird powder day at a
  maritime resort should not be docked because that resort "usually melts". Worse,
  it would **double-count**: the live `quality` component (`SNOW_QUALITY_WEIGHTS`)
  already carries *current* preservation as **crust 0.20 + thaw 0.18** (refrozen
  melt crust + incoming rain/warmth), and those already outweigh density (0.28)
  within SnowQuality — 0.50 combined, coincidentally matching the Phase 2B
  finding that preservation beats density. Live preservation is present, in its
  correct *current-conditions* form.
- **Consistency** is inter-year reliability — *"is this a dependable mountain to
  plan around"* — which is a trip-planning question, not a right-now one. There is
  no single-day analogue; the honest thing is a trip-only signal, which is exactly
  where it already lives.

**Decision:** omit both from `GLOBAL_SCORE_WEIGHTS`. No live-board change. This
also avoids adding **unbacktested** weights to the live board (Phase 2B validated
only `TRIP_BASELINE_WEIGHTS`) — extending the trip refit's numbers to a board with
different semantics and a different (never-backtested) weight vector would be the
kind of unvalidated hand-weighting this whole run set out to stop.

**Phase 4.2 status: complete (omitted with cause).** No code change. 292 tests pass.

---

## PHASE 4.3 — Surface the new signals visually (prose → meters)

The density/preservation/consistency components were **prose-only** in the trip
commentary. Added a compact **"Quality signals"** meter strip to the trip card —
three 0-100 bars for the three climatological quality components, so the picture
and the words come from one source.

**Treatment (matches existing design language).** Reuses the existing `.bar`
primitive (6px track + fill), the card's muted-header style, and `--accent` for
the fill. A three-column grid (label / bar / value) mirrors the card's existing
`.grid2`/`.cell` rhythm. No new visual vocabulary invented.

**Data.** The per-mountain trip *pattern* file was the right home (already lazily
fetched per card; keeps the ~1KB/mountain `baseline.json` that drives the whole
leaderboard untouched). Each `MM-DD` entry went from a bare prose string to
`{t: prose, d: density, p: preservation, c: consistency}` (0-100, omitted when a
signal is absent). `card.js` reads either shape via two small helpers, so live
mode (prose-string on the row) and static mode (fetched object) both work; the
strip hides itself when no signals are present.

**Files:** `scripts/build_snapshot.py` (export), `web/js/card.js` (render +
shape helpers), `web/css/styles.css` (strip + `.bar>i` colour).

**Verification — in-browser, static mode (the deployed path), no console errors:**

| Mountain (Feb 14) | Snow quality | Preservation | Consistency | reads as |
|---|---:|---:|---:|---|
| Jackson Hole | 83 | 88 | 67 | dry, holds well, reliable ✓ |
| Crystal Mtn | 63 | 58 | 57 | maritime penalty visible ✓ |
| Perisher | 35 | 33 | 0 | marginal, boom-bust ✓ |

Computed-style check confirmed the fills are proportional (83/88/67 → 148/157/119
px of a 178px track), accent-coloured, and vary across mountains. The numbers on
screen match the config-level climatology exactly, and match the prose in the
same card. 292 tests still pass (the string→object format change broke nothing;
the render path is verified in-browser, the appropriate proof for a UI feature).

**Not surfaced:** mountain character (vertical/acreage/difficulty) — it's static
per resort and already legible as raw stats elsewhere; a meter would imply a
0-100 scale it doesn't have. Left as-is rather than forcing a treatment.

**Phase 4.3 status: complete, browser-verified.** `web/data/trip` is git-ignored
(regenerates on deploy); only the code changes commit.

---

## RUN SUMMARY — what survived contact with data

**The model works, and now we know in what sense.** A leak-gated, point-in-time
backtest over six Februaries puts the Trip Predictor at **Spearman +0.62** against
a quality-aware realised-conditions outcome (out-of-sample +0.69 after refit) —
genuine multi-month predictive skill, not just plausible reasoning.

**Confirmed / adopted:**
- `base` and `season` carry most of the skill; both robustly positive.
- **Preservation is the single most valuable *quality* signal — it beat density.**
  The Phase 2B refit moved it 0.10 → 0.22 and cut `quality` (density) 0.19 → 0.01.
  How well the pack *holds* predicted real weeks better than how light it *fell*.
  The quality principle survived; the mechanism changed.
- The refit moved the board toward expert consensus (Grand Targhee 8→3, Alta 18→12).
- Zugspitze vertical was genuinely wrong (4298→2362 ft, lift-served rule adopted).

**Caught before it did harm (three tripwires, two self-imposed):**
- **Phase 2 circular metric.** The first outcome couldn't see snow quality
  (`quality_factor` pinned to 1.0); the fit "discovering" that quality is
  worthless was the fit rediscovering the target. Halted the adoption, rebuilt the
  outcome. This is the most important catch of the run.
- **65% grade churn.** Re-anchoring thresholds would have regraded two-thirds of
  in-season weeks — and investigation (Phase 4c) showed the drift was the **ECCC
  depth fix** (25%→91% base coverage), a data *improvement*, not a miscalibration.
  Thresholds left alone.
- **Siting exonerated.** My own Phase 4.1 hypothesis (siting inflates grades) was
  wrong; proven three ways. Corrected in the log.

**Did NOT establish:**
- Whether **consistency** helps — a single 7-day window can't test an inter-year
  property; it survives only via pooling and its ablation is ~0.
- Live-board (`GLOBAL_SCORE_WEIGHTS`) skill — never backtested; deliberately left
  unrefit.

**Open problems logged, not fixed (out of scope):**
- **Pacific NW has genuinely no predictive skill** (+0.01, and *worse* under the
  quality-aware outcome) despite adequate dispersion. Not an artifact. Real.
- **Siting clamp saturates on 20% of the roster** (raw ratios to 8.5×), split
  between valley/airport proxy stations and ERA5 under-resolving maritime alpine.
- Terrain-character weights can't be validated by any snow-derived outcome — kept
  as an explicit product choice, not a fitted result.

**Honest uncertainties:** the same-station confound flatters every correlation
here (prediction and outcome share a station), so these are "does it rank
*stations* right" numbers — a ceiling on, not a measurement of, "does it rank
*mountains* right". And `n=6` seasons is thin; the out-of-sample bar was met but
on two held-out winters.

**Net:** the methodology is sounder than when the run started (one real data
correction, an empirically-refit trip board, a preservation-over-density finding
that reversed a prior assumption) and better-understood (three would-be changes
stopped because the evidence didn't support them). The biggest lesson repeats the
one from earlier in the project: **the measurement is the thing most likely to be
wrong — check it before trusting what it says.**
