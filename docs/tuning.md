# Grade-curve tuning notes (Alta / Snowbird 766)

Tuned against the full Snowbird SNOTEL record (37 water years, 1989–2026),
ingested live. This file records *why* the config values are what they are so
they can be re-tuned deliberately, not by guesswork.

## Station identity (verified)

- NRCS report header returns literally: `SNOTEL 766: Snowbird, UT`.
- The station's published coordinates (40.5613, -111.6553) reverse-geocode via
  the NWS API to **"Alta UT"** (grid SLC 107,165), high in Little Cottonwood —
  **not** Salt Lake City (which is grid SLC 100,175, ~4,200 ft in the valley).
- Conclusion: the historical baseline is genuinely the Alta/Snowbird snowpack.

## Season metric: SWE-gain, not snow-depth change

Snow depth (SNWD) at this station only begins ~WY2003; SWE (WTEQ) goes back to
1989. So:

| Metric | Usable yrs | Notes |
|---|---|---|
| cumulative `new_snow_24hr` (depth change) | ~23 | undercounts settling (~256"/yr median vs Snowbird's real ~500") |
| **cumulative SWE-gain (positive WTEQ increments)** | **36** | 100% coverage every year; matches reputation |

We use **SWE-gain** (`config.SEASON_METRIC = "swe_gain"`). Sanity check — it ranks
the famous years correctly with zero hand-tuning: WY2011 (82" water) & 2005 top;
WY2015/2018 (drought) bottom.

## Curve generosity

Percentile rank is uniform by construction, so the curve is purely a judgment of
strictness, anchored to real seasons:

- **F = ~11–14% of years** (4–5 seasons): reserved for genuinely bad Utah winters
  (2015, 2018, 1994, 1992), not merely below-average ones.
- **Granular at the top** so A+/A/A- are distinct: A+ ≈ top 4% (WY2011, 2005),
  A ≈ next (2023, 2006, 1993), A- (2017, 2019, 1995).
- **Median season → B-** (e.g. WY2010, 2020).

See `config.GRADE_THRESHOLDS`. Re-check any change with `python cli.py analyze`.

## Storm grading: two baselines on purpose

The 24hr snowfall distribution is **mostly zeros** (median 0" — most days it
doesn't snow), so ranking a storm against *all* windows saturates: an ordinary
11" powder day and the all-time 33" record both read ~98–100th percentile.

- **Storm LETTER grade** ranks against *meaningful-snow* windows only
  (`grade_baseline_min_inches = 4"`). Now 11" → ~80th (good day), 33" → 100th
  (record). The letter actually discriminates.
- **Storm ALERT** ranks against *all* windows and additionally requires an
  absolute floor (`min_inches` = 10"/24hr, 18"/72hr). The floor stays the binding
  constraint; the percentile just prevents a dry-microclimate false alarm — the
  spec's original intent.

## Overall score: strictness and dynamic weighting

- **Power-mean blend.** The overall score is a weighted *power mean* of the
  sub-scores (`SCORE_BLEND_EXPONENT`, default 0.5), not a plain average. A plain
  average compresses toward the middle, so a bad mountain with one mediocre
  sub-score still reads C-. The power mean makes a weak component bite: WY2015
  mid-Feb went 16.3 (C-) → 13.3 (D) under the season profile, while the great
  WY2011 barely moved (75.5 → 74.5). Bad cases get stricter; good cases don't.
- **Dynamic weighting.** The default `dynamic` profile interpolates
  `DYNAMIC_WEIGHTS` by `season_progress` (0 at each mountain's season start, 1 at
  its end). Early season leans on season-history + forecast (time for it to play
  out); late season leans on current conditions (now-or-never). Progress is
  computed from a per-mountain `season_window` of (month, day) bounds, so it is
  hemisphere/calendar agnostic -- a Southern-Hemisphere resort just sets e.g.
  (6,15)->(10,10) and the same code works. NOTE: the accumulation *water year*
  (Oct 1 start) is still Northern-Hemisphere; that also needs per-mountain config
  before adding SH mountains.

## Worked example that motivates keeping season ≠ storm

WY2026 (2025–26): **season grade D** (14th pct, below-average snowpack) but it
produced the **biggest 24hr and 72hr storms in the 37-year record** (Feb 19,
33"/44", A+ alerts). One number would hide both truths; two don't.

## Overall letter curve (OVERALL_GRADE_THRESHOLDS)

The overall value is NOT a percentile — it's a strict power-mean (p=0.5) of
mixed sub-scores, scaled by the multiplicative cover gate — so its distribution
sits far below uniform: borrowing the percentile curve graded a median mountain
in a median week C/D. It gets its own curve, calibrated empirically:

- **Method:** backtest the offline overall value (dynamic profile, no forecast —
  exactly what the map paints) across the full 79-mountain roster × the last
  ~15 completed seasons × 3 dates per season (25/50/75% through each mountain's
  `season_window`). n=3494 values; median 33.3, p90 73.9.
- **Cutoffs** sit at the same cumulative fractions the percentile curve targets
  (A+ ≈ top 4%, median → B-, D/F ≈ bottom 14%), read off that distribution:
  A+ ≥85, A ≥71, A- ≥60, B+ ≥50, B ≥39, B- ≥30, C+ ≥23, C ≥17, C- ≥13, D ≥9.
- **Sanity (Alta, Feb 15):** WY2019 (monster Feb) 94 → A+; WY2023 (record year,
  83rd pct by mid-Feb) 60 → A-; WY2012 (median-ish week in a lean year) 37 → B-;
  WY2015 (the bust) 16 → C-.
- **Re-tune trigger:** any change to SCORE_BLEND_EXPONENT, the cover gate, the
  conditions mix, or the sub-score set shifts the value distribution — re-run
  the backtest and re-read the cutoffs before trusting the letters again.

## Forecast: two-sided, additive, global

Three linked changes (in the same rework as the letter-curve split):

- **Global coverage.** A provider-neutral `Outlook` (ski/sources/outlook.py):
  NWS on US grids, Open-Meteo's forecast API everywhere else — every mountain
  now has forecast + live-weather sub-scores, so cross-hemisphere score
  compositions match instead of silently renormalizing around missing parts
  (the card's `sources` block shows who supplied what).
- **Thaw downside.** A forecast can be bad news: `thaw_index` ramps 0→1 on 72h
  rain (full penalty by 1") and sustained warmth (max temp 35→55°F, capped at
  half weight), and `forecast_score` nets it against any incoming-snow boost.
  Dry AND benign still drops out (None) — but "rain incoming" no longer scores
  the same as "nothing incoming".
- **Additive blend.** The forecast sub-score is excluded from the power mean and
  applied as a delta around neutral 50 (weight-normalized, clamped). Under the
  strict exponent a merely-neutral forecast used to sit below the other
  components and drag them — mountains were penalized for HAVING a forecast.
  As a delta: neutral = exactly 0, snow boosts, thaw drags.

## Conditions de-dup: depth lives in the gate only

Absolute base depth used to appear three times: `base_rel` (depth percentile),
`base_abs` (absolute depth curve) inside conditions, AND the multiplicative
cover gate. Thin cover was triple-counted. `base_abs` is gone; the cover gate is
the single absolute-depth anchor (it's multiplicative, so it still caps a
thin-base hill no matter how good its percentiles), and the conditions mix is
now base_rel 0.40 / fresh 0.35 / weather 0.25. Fresh snow stays absolute — it's
a flow, not the stock the gate already measures.

## Warmth is season-relative; a refrozen crust is not

Two linked temperature signals, split by a single principle: **rain-on-snow and
a refrozen crust are bad in any month; bare warmth is only a threat when it's out
of season.**

- **Forward thaw, season-tapered** (`thaw_index`, config `FORECAST_THAW`). Rain
  stays absolute. The *warmth* component's weight fades with `season_progress`:
  full early (a 45°F January day is a real melt event on a base you need for
  months), down to a residual `(1 - warm_taper_by_progress)` by season's end (the
  same day in April is just spring at a resort whose product is warm corn). The
  residual is kept nonzero so an extreme late-season heat wave still bites.
  `warm_zero_f` was also raised 35→40 so ordinary nice days don't register at all.
- **Backward refreeze / crust** (`refreeze_index` → `apply_refreeze`, config
  `REFREEZE`). The forward forecast can't see that yesterday's thaw refroze
  overnight into boilerplate — the penalty *vanishes* the day the surface is
  worst. So a conditions-side term looks at trailing actuals: a recent melt
  (72h rain OR warmth) × whether it refroze (24h min temp) × not-yet-healed
  (fresh 7-day snow resurfaces the crust). It multiplies the conditions sub-score
  down by up to `max_penalty` (0.40). Warmth here is NOT tapered — an April rain
  crust skis like a rink regardless of the calendar. This is also *why* fresh
  snow is a conditions input: it heals the crust.
- **Data.** Recent actuals come from Open-Meteo's forecast endpoint with
  `past_days=3` — one cheap global call, so US mountains (NWS forward) borrow it
  for the crust signal rather than scraping station observations.
- **Calibration.** Unaffected: `OVERALL_GRADE_THRESHOLDS` was backtested offline
  (`use_network=False`), where thaw=0 and refreeze=0. Both terms only move
  live/network scores, and only in the intended direction.

## Snow-quality rework: skiability distribution after Phases 2–5 (2026-07-17)

The quality signals — new-snow density (Phase 2), wind loading/scour (Phase 3),
continuous powder recency decay (Phase 5a), and buried rain/melt crust (Phase 5b)
— plus a lowered skiability quality floor (`SKI_QUALITY.floor` 0.35 → 0.22, Phase
4) all move the **skiability** value, so its distribution was re-checked against
`SKIABILITY_GRADE_THRESHOLDS`.

- **`OVERALL_GRADE_THRESHOLDS`: untouched, no re-tune.** The overall/self-relative
  path (season/in_season/forecast/conditions, cover gate, power mean) was not
  changed by this rework; the quality signals feed the skiability headline, the
  SnowQuality aggregate, and commentary, not the overall value. The buried-crust
  term (Phase 5b) folds only into skiability, never into `conditions`'
  `apply_refreeze`, which still uses the trailing-72h refreeze alone.

- **`SKIABILITY_GRADE_THRESHOLDS`: measured, still valid, no change.** Backtest of
  the OFFLINE-measurable shift (density Tier 1 on the 37 pillow stations, recency
  decay for all, buried crust) across the roster × 3 winter dates (Jan/Feb/Mar
  2026), in-season only, n=247: min 8.5, p25 29.8, **median 43.6**, p75 56.2, p90
  67.6. Grade histogram stays smooth and well-spread — A+ 3.6%, A/A- 6.9%, the
  B/B-/C+ middle ~48%, a real D/F tail 6.5% — matching the curve's semantic intent
  (A+ = deep base + real storm + good surface; median ≈ B-/B; thin/crusted/heavy
  → C/D/F). The thresholds hold.

- **Live-only shift is DEFERRED, by design.** Wind scour, incoming thaw, the Tier-2
  (temperature) density estimate for the 76 non-pillow mountains, and the lowered
  floor mostly bite only with a live outlook, so they can't be reconstructed from
  the stored history (`raw_observations` has no wind/precip/temperature record).
  They push quality-compromised days lower — the lowered floor deliberately opens
  the D/F range for a big-but-wind-hammered-and-rained-on day that the old 0.35
  floor propped up at C. Re-fitting the cutoffs against the incomplete offline
  distribution would be worse than waiting, so: **keep the thresholds; revisit
  after a season of live data.** Enabling that cleanly wants a quality-signal log
  alongside `forecast_log` (accumulate live density/wind/crust/thaw per card), then
  re-read the skiability distribution from real live values.

- **Re-tune trigger (skiability):** any change to `SKI_BASE_MAX`/`SKI_POWDER_MAX`,
  `POWDER_SCORE_CURVE`, the `SKI_QUALITY` penalties or floor, `DENSITY_*`,
  `POWDER_DECAY`, `WIND`, or `CRUST_MEMORY` shifts the skiability distribution —
  re-run the offline backtest above and, once live quality data has accumulated,
  the live one before trusting the letters.
