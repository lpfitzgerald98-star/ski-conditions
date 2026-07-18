# Glade Grade ΓÇö Snow Quality / Skiability Improvement Plan

**Status:** Approved 2026-07-17. Audit + design done (Opus). Implementation in progress.

## Progress (2026-07-17)

- **Phase 0 ΓÇö DONE.** `SnowQuality` scaffold surfaced on the card, weight 0.
- **Phase 1 ΓÇö DONE.** Commentary linkage: quality signals in `facts` (serves rules + AI),
  quality-drag lead branch, de-dup, conservative positive note.
- **Phase 2 ΓÇö DONE.** New-snow density, tiered (measured ╬öSWE/╬ödepth on 37 SNOTEL+Mammoth;
  snowfall-weighted-temperature Tier 2 elsewhere). Feeds skiability via effective_powder_in +
  the density quality component + a "heavy/wet" commentary clause. Verified on real Alta data.
- **Phase 3 ΓÇö DONE.** Wind loading/scour (magnitude-only), from Open-Meteo hourly wind. Wind
  penalty in skiability quality_factor (gated by fresh snow) + wind quality component + wind
  commentary clause + "untouched/cold smoke" positive note. Verified live (SH roster).
- **Phase 4 ΓÇö DONE.** SnowQuality folded into the comparable leaderboard as a 5th weighted
  component (`quality`, moderate 0.16 raw / ~13% effective, to ramp after validation).
  SKI_QUALITY floor lowered 0.35 -> 0.22 so big-but-terrible can grade down. Verified live (SH roster).
- **Phase 5a ΓÇö DONE.** Continuous powder recency decay (POWDER_DECAY) replacing the
  recent/week/7-day-cliff step; half-life shortens with the (effective) crust index (cold snow
  lingers, thawed snow fades). Contained to the skiability headline.
- **Phase 5b ΓÇö DONE (SNOTEL-only, user chose to build it).** Buried rain/melt crust from the
  SWE-gain-without-depth-gain pulse signature: pillow stations (37) only, None elsewhere (falls
  back to trailing refreeze). Folds into skiability + quality + commentary (a distinct "old buried
  crust" clause), never the self-relative overall. Verified on real Alta data (4 real pulses;
  spring crust 0.375). Aggregate stays outlook-gated so the offline null (Phase 0) is preserved.
- **Phase 6 ΓÇö DONE.** `OVERALL_GRADE_THRESHOLDS` untouched (overall path unchanged). Skiability
  distribution re-checked offline (roster ├ù 3 winter dates, n=247): median 43.6, A+ 3.6%, smooth
  spread, D/F tail 6.5% ΓÇö thresholds still valid, no change. Live-only shift (wind/thaw/Tier-2
  density/floor) deferred pending accumulated live data; documented in docs/tuning.md.

Final: **222 tests pass** (was 179). B7 (sun/aspect) remains the only deferred item (needs new
per-resort aspect data). Nothing committed yet (batching per PR workflow).


## Background / problem

Grading historically leaned on snow **quantity** (base depth, recent totals, SWE). Quantity is a
poor proxy for skiability: 12" of dense, wind-affected, sun-crusted snow skis very differently from
12" of cold, dry, well-bonded powder.

Audit finding (important correction to the original premise): a quality layer **already exists** ΓÇö
`score.skiability_score` applies a punitive `quality_factor` built from `refreeze_index`,
`thaw_index`, and `weather_quality`, and that skiability grade is already the card headline the
commentary explains. The real gaps are narrower:

1. **Leaderboard sort is still pure quantity.** `comparable.global_score` / `regional_score` rank on
   4 absolute-inch inputs only. A wind-hammered 20" outranks a cold-dry 14".
2. **Two quality signals missing despite data on hand:** snow **density (SWE:depth)** ΓÇö SNOTEL stores
   both `WTEQ` and `SNWD` daily but the ratio is only used as a hardcoded `3.0` unit constant; and
   **wind loading/scour** ΓÇö not ingested for the surface at all.
3. **Quality never reaches the commentary lead clause.** `commentary_rules._lead` leads with
   inches/percentile. The AI generation path only receives the quantity-shaped `facts` dict and
   literally cannot see the quality signals.

## Architecture decision

Introduce a single explicit **`SnowQualityScore` (0ΓÇô100)** computed once in the pipeline, with named
components (`density`, `wind`, `crust`, `thaw`, `warmth`). It:
- feeds the skiability `quality_factor` as today (**multiplicative** ΓÇö preserves existing tuning),
- becomes a **weighted component** in the comparable global/regional blend (**additive** ΓÇö so quality
  can reorder the leaderboard),
- is surfaced as a first-class card field with its component breakdown (explainable, not a black box).

## Decisions (user sign-off 2026-07-17)

1. **Leaderboard weighting:** quality *should* reorder the leaderboard. Staged: land at moderate
   weight, validate reorderings, ramp toward strong. (User wanted strong; agreed to ramp because half
   the signal rides on coarse ERA5 wind.)
2. **Density:** **absolute** in the cross-mountain comparable score; **relative to own history** in the
   self-relative historical score (matches that score's existing percentile-vs-own philosophy).
3. **Coverage:** add **Open-Meteo (ERA5)** density + wind everywhere so ACIS/ECCC (no SWE) and Canada
   (no NWS wind) aren't blind. Accept model coarseness for consistency.
4. **Penalty floor:** lower `SKI_QUALITY.floor` (and revisit `REFREEZE.max_penalty`) so a
   big-but-terrible day can actually grade poorly.
5. **Commentary:** AI path is a longer-term goal; update **both** rules and AI (facts dict) paths.
6. **Re-tune:** re-backtest grade thresholds after the distribution shifts ΓÇö its own pass.

## Phases (each gated on confirm-before-building)

- **Phase 0 ΓÇö scaffold + observability.** `SnowQualityScore` computed from existing signals, surfaced
  on the card, **weight 0** in all consumers. No grade change. Look before it's load-bearing.
- **Phase 1 ΓÇö commentary linkage (B1).** Pipe `quality_factor`/`refreeze`/`thaw` into `facts_from_card`
  (serves rules + future AI). Add quality-driven lead branch in `commentary_rules._lead`.
- **Phase 2 ΓÇö snow density (B2).** Derive storm-window SWE:depth from `raw_observations`; backfill via
  ERA5 for coverage. Absolute for comparable, relative for historical. Feed density into quality score
  and `effective_powder_in`. Add density commentary clause.
- **Phase 3 ΓÇö wind loading/scour (B3), magnitude-only.** Add Open-Meteo wind (forecast + ERA5 history).
  Sustained wind on fresh snow ΓåÆ penalty. Store direction for later aspect work. Add wind clause.
- **Phase 4 ΓÇö leaderboard fold (B4) + floor.** Add `SnowQualityScore` as weighted component in
  `comparable`, renormalizing over available components. Ramp moderateΓåÆstrong. Lower skiability floor.
- **Phase 5 ΓÇö recency decay (B5) + rain-on-snow persistence (B6).** Continuous age decay modulated by
  preservation; buried-crust memory from season rain-on-snow history.
- **Phase 6 ΓÇö re-backtest thresholds.** Re-tune `OVERALL_GRADE_THRESHOLDS` /
  `SKIABILITY_GRADE_THRESHOLDS` against the quality-shifted distribution.

**Deferred:** B7 (sun/aspect exposure) ΓÇö needs new per-resort aspect data. Revisit only if B1ΓÇôB5 fall short.

## Open verification items

- Confirm CDEC/BCSWS stations return **both** SWE and depth against live data (Phase 2). If not, those
  mountains lean on ERA5 like ACIS/ECCC.

## Key files

`ski/score.py`, `ski/comparable.py`, `ski/pipeline.py`, `ski/commentary_rules.py`, `ski/commentary.py`,
`ski/sources/openmeteo.py`, `ski/sources/snotel.py`, `ski/card.py`, `config.py`.
