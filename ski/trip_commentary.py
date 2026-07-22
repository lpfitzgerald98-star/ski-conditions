"""Trip Predictor commentary -- the longer-form "why" behind a future-date score.

The live scorecard's commentary (ski/commentary.py, ski/commentary_rules.py) is
capped at two sentences by CARD SPACE, not by lack of things to say. A Trip
Predictor result has no such constraint -- there's no scorecard squeezing it --
so it earns three genuinely different things to say instead of one compressed
grade explanation:

  1. SEASONAL PATTERN (this module, `seasonal_pattern_text`): what this
     mountain's snowpack typically looks like during that calendar window,
     across its own history. Rules-based, same clause-pool + seeded-RNG
     technique as commentary_rules.py, for the same reasons: free, no network,
     deterministic, and honest by construction (it can only ever describe
     numbers actually in the climatology, never invented ones).

     Part 1 now has a second half, QUALITY & CHARACTER (`_quality_character_
     text`): what the snow is actually LIKE, not just how much of it there is
     -- density (a real snow-to-liquid-derived water-content %, not just a
     0-100 score), how well the pack holds between storms (preservation), and
     whether this mountain is reliable or boom-or-bust year to year
     (consistency). These are exactly the ranking components ski.trip.
     climatology computes (config.REGION_DENSITY_PRIOR / PRESERVATION / the
     inter-year CV) -- this paragraph is the receipts for THOSE numbers, so a
     skeptical reader can see the actual water-content percentage and
     reliability figure behind a mountain's score instead of just trusting a
     ranking position. Silent (contributes nothing) wherever a signal is
     genuinely absent -- e.g. a depth-only station with no measured density
     and no regional prior -- never a fabricated placeholder.

  2. HOW THIS YEAR IS TRACKING and 3. THE TAKEAWAY are NOT here. They need
     TODAY's live score and the picked date's lead time, both of which are
     already sitting on every trip row in BOTH the static JS blend
     (web/js/main.js asTripRow) and the live /trip response -- no new data,
     nothing this module could compute more cheaply than the caller already
     has. Duplicating a second prose engine in Python for numbers Python
     doesn't even need to look up again would just be two engines to keep in
     sync; those two parts are written once, in JS, in web/js/card.js.

WHY THIS PART IS RULES-BASED, NOT AI (matches the live default, deliberately):
this mountain's seasonal pattern for "March 14" is the same fact for every user
who ever asks about it -- but the LEAD-TIME framing and CURRENT-CONDITIONS
comparison (parts 2/3) change every day the roster rescoes. Precomputing the
whole three-part text with an LLM would mean regenerating up to 113 mountains x
366 calendar days on every rebuild just to keep the daily-changing half fresh --
nothing like the live path's 113-cards-once-a-day cost profile. Splitting the
STABLE half (this module) from the DAILY-CHANGING half (JS, computed from
numbers already on hand) sidesteps that entirely, keeps the static (GitHub
Pages, no backend) deployment fully capable, and needs no API key.

NO INVENTED SIGNALS. There is no historical wind or temperature anywhere in
this codebase (raw_observations stores only station_id/date/swe/depth/
new_snow) and no per-mountain aspect data in config.MOUNTAINS -- so this module
never claims a freeze-thaw frequency or a wind-loading pattern; those claims
would be fabricated, not derived. What IS real and mountain-specific: each
mountain's own hand-curated `season_window` (config.MOUNTAINS), which places
the target date on a 0..1 progress through THIS mountain's actual season via
the existing `watercalendar.season_progress`; and the climatology trajectory
itself (ski.trip.climatology already computes all 366 days of base/fresh/
season-to-date per mountain) -- reading a couple of neighboring days around the
target tells whether the pack is typically still building, holding, or already
past its climatological peak for that mountain, at zero additional computation
over what climatology() already produced.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from config import LOW_CONFIDENCE_YEARS
from ski.commentary_rules import _MAX_PLAUSIBLE_BASE_IN
from ski.score import is_in_season
from ski.trip import DOWY_MAX, target_dowy
from ski.watercalendar import season_progress

# How far on either side of the target day to look for the base-depth TREND
# (still building / holding / past its typical high). 14 days is short enough
# to reflect the target date's own stretch of the season, long enough that a
# single noisy day in the smoothed climatology can't flip the trend.
_TREND_HALF_SPAN_DAYS = 14

# Inches of typical base-depth change over the trend window to call it a real
# trend rather than noise -- small next to DEPTH_SCORE_CURVE's 12-80" span.
_TREND_THRESHOLD_IN = 3.0

# Season-progress bands (0 = season just opened, 1 = at/past its usual close),
# from the mountain's OWN season_window -- see watercalendar.season_progress.
_EARLY, _BUILDING, _CORE, _LATE = 0.12, 0.40, 0.75, 0.92


def _progress_band(p: float) -> str:
    if p < _EARLY:
        return "early"
    if p < _BUILDING:
        return "building"
    if p < _CORE:
        return "core"
    if p < _LATE:
        return "late"
    return "spring"


def _plausible_base(v: float | None) -> float | None:
    """Same guard as commentary_rules: some reanalysis grids over glaciated
    terrain report implausible standing depth. Never quote it in prose."""
    if v is None or v > _MAX_PLAUSIBLE_BASE_IN:
        return None
    return v


def _wrapped_dowy(d: int, offset: int) -> int:
    """dowy +/- offset, wrapped into the 1..DOWY_MAX ring (a trend window near
    day 1 or day 366 must still look at the OTHER side of the season boundary,
    not fall off the end of the year)."""
    return (d - 1 + offset) % DOWY_MAX + 1


def _base_trend(clim: dict[int, dict], dowy: int) -> str | None:
    """'rising' / 'holding' / 'declining', or None when there's not enough
    data on both sides of the window to judge (e.g. right at an off-season
    edge). Compares settled base depth a couple weeks before vs after the
    target day -- the SAME climatology dict callers already built, just two
    more dict lookups, no new computation."""
    lo = clim.get(_wrapped_dowy(dowy, -_TREND_HALF_SPAN_DAYS), {})
    hi = clim.get(_wrapped_dowy(dowy, _TREND_HALF_SPAN_DAYS), {})
    b_lo, b_hi = _plausible_base(lo.get("base_in")), _plausible_base(hi.get("base_in"))
    if b_lo is None or b_hi is None:
        return None
    delta = b_hi - b_lo
    if delta >= _TREND_THRESHOLD_IN:
        return "rising"
    if delta <= -_TREND_THRESHOLD_IN:
        return "declining"
    return "holding"


# ---------------------------------------------------------------------------
# Clause pools -- combinatorial variety without a model (same trick as
# commentary_rules.py): pick one phrasing per slot with a seed stable per
# (mountain, calendar day) so the SAME target date always reads the same way
# for a given mountain, while different mountains/dates read differently.
# ---------------------------------------------------------------------------
_STAGE_OPEN = {
    "early": [
        "{date} sits early in {short}'s season",
        "This is still early season at {name}",
        "{date} lands right at the start of {short}'s typical season",
    ],
    "building": [
        "{date} falls in the early-to-mid stretch of {short}'s season",
        "By {date}, {name} is usually still working its way into the season",
        "{date} is early-season territory at {name}",
    ],
    "core": [
        "{date} sits in the heart of {short}'s season",
        "By {date}, {name} is typically deep into its core season",
        "{date} lands squarely in {short}'s usual mid-season stretch",
    ],
    "late": [
        "{date} is late in {short}'s typical season",
        "By {date}, {name} is usually well into its late-season stretch",
        "{date} falls late in {short}'s season, historically",
    ],
    "spring": [
        "{date} is deep into spring for {name}",
        "By {date}, {name} is typically past its core season and into spring",
        "{date} sits at the tail end of {short}'s usual season",
    ],
}

_TREND_CLAUSE = {
    "rising": [
        "and the base here is typically still climbing through this stretch",
        "and the snowpack usually keeps building for a while yet",
        "with the base historically still on its way up at this point",
    ],
    "holding": [
        "and the base here typically holds fairly steady through this stretch",
        "with conditions usually fairly settled around this point in the season",
        "and the snowpack tends to plateau through this window",
    ],
    "declining": [
        "though the base here typically starts settling from around this point",
        "with the snowpack usually easing off its high by this stretch",
        "and coverage historically starts trending down through this window",
    ],
}

_NO_TREND_FALLBACK = [
    "There isn't enough of a historical record right around this date to say whether the base is typically still building or already settling.",
    "The station history here is too thin around this date to call a clear trend.",
]

_BASE_NUMBER_CLAUSE = [
    "typical base runs around {base} this time of year",
    "the base here typically sits near {base} for this stretch",
    "a typical base around {base} is the historical norm here",
]

_NO_HISTORY_FALLBACK = [
    "{name} doesn't have enough historical record for this date to describe a typical pattern.",
    "There isn't a reliable historical baseline for {name} around this date.",
]

_LOW_CONFIDENCE_CLAUSE = [
    "with a shorter station record here, this is a loose read rather than a firm one",
    "the record here only spans a handful of years, so treat this as a rough guide",
]


_OFF_SEASON_CLAUSE = [
    "{date} typically falls outside {short}'s ski season, with little to no base or fresh snow on record for this window",
    "Historically, {date} sits outside {short}'s usual season -- there's rarely meaningful snow cover on record this time of year",
    "{short} doesn't typically have skiable conditions around {date}; this window falls outside its usual season",
]


# ---------------------------------------------------------------------------
# Quality & character clauses -- the receipts behind the ranking's density/
# preservation/consistency components (config.REGION_DENSITY_PRIOR etc.).
# Bands are placed at the same cutoffs the DENSITY_SCORE_CURVE / preservation /
# consistency math already uses to draw meaningful distinctions elsewhere in
# the codebase, not arbitrary new thresholds.
# ---------------------------------------------------------------------------
_DENSITY_BAND = [  # (max water fraction, band)
    (0.07, "very_light"), (0.10, "light"), (0.14, "moderate"),
    (0.20, "dense"), (1.0, "heavy"),
]
_DENSITY_CLAUSE = {
    "very_light": [
        "{short} typically sees very light, dry snow -- around {pct}% water content",
        "the snow here typically runs very light and dry, roughly {pct}% water content",
    ],
    "light": [
        "{short} typically sees fairly light, dry snow -- around {pct}% water content",
        "snow here is typically on the drier side, roughly {pct}% water content",
    ],
    "moderate": [
        "{short} typically sees moderate-density snow, around {pct}% water content",
        "the snow here runs moderate density on average, roughly {pct}% water content",
    ],
    "dense": [
        "{short} typically sees fairly dense, damp snow -- around {pct}% water content",
        "snow here tends to run heavier than average, roughly {pct}% water content",
    ],
    "heavy": [
        "{short} typically sees heavy, wet snow -- around {pct}% water content",
        "the snow here characteristically runs quite heavy and wet, roughly {pct}% water content",
    ],
}

_PRESERVATION_BAND = [  # (min score, band)
    (80, "high"), (60, "good"), (40, "fair"), (0, "poor"),
]
# All standalone declarative clauses (no leading conjunction) -- _quality_
# character_text supplies "and "/"though " when attaching one to the density
# clause, and uses them bare when preservation stands as its own sentence.
_PRESERVATION_CLAUSE = {
    "high": [
        "the pack historically holds up well between storms, with limited midwinter melt exposure",
        "coverage here typically holds steady between storms rather than melting out",
    ],
    "good": [
        "the pack generally holds onto snow reasonably well between storms",
        "conditions here typically hold up decently between storms",
    ],
    "fair": [
        "there's some real midwinter melt/rain exposure here between storms",
        "the pack here is only moderately good at holding between storms",
    ],
    "poor": [
        "this mountain is prone to real midwinter melt or rain exposure between storms",
        "coverage here can degrade meaningfully between storms historically",
    ],
}
# "and" for the good bands (reinforcing the density clause), "though" for the
# weak ones (a contrast/caveat) -- picked by band, not randomized, since the
# connector's TONE has to match the clause it's joining, unlike phrasing choice.
_PRESERVATION_CONNECTOR = {"high": "and", "good": "and", "fair": "though", "poor": "though"}

_CONSISTENCY_BAND = [  # (min score, band)
    (80, "high"), (60, "good"), (40, "fair"), (0, "poor"),
]
_CONSISTENCY_CLAUSE = {
    "high": [
        "{short} is also historically very consistent year to year",
        "this is a historically reliable mountain -- season totals don't vary much",
    ],
    "good": [
        "{short}'s season-to-season variability is historically fairly modest",
        "year-to-year variability here is historically fairly contained",
    ],
    "fair": [
        "{short} shows real year-to-year variability -- some seasons run well above or below normal",
        "there's a meaningful boom/bust element here from year to year historically",
    ],
    "poor": [
        "{short} is a genuinely boom-or-bust mountain historically -- excellent seasons and weak ones both happen regularly",
        "year-to-year swings here are historically large -- this isn't a mountain that delivers the same season twice",
    ],
}


def _band(value: float, table: list[tuple[float, str]], descending: bool = False) -> str:
    """First band whose threshold `value` clears. `table` sorted ascending by
    threshold; `descending=True` for min-score tables (>=80 -> 'high' etc)."""
    if descending:
        for threshold, band in table:
            if value >= threshold:
                return band
        return table[-1][1]
    for threshold, band in table:
        if value <= threshold:
            return band
    return table[-1][1]


def _sentence(clause: str) -> str:
    """A standalone clause -> a capitalized, period-terminated sentence."""
    return clause[:1].upper() + clause[1:] + "."


def _quality_character_text(c: dict, short: str, r: random.Random) -> str:
    """The quality & character paragraph: density (real water-content %),
    preservation, and consistency -- the receipts behind the ranking's
    quality/preservation/consistency components, in plain technical language.
    Empty string when NONE of the three signals are available (never invents
    one), and degrades gracefully when only some are present."""
    wf = c.get("water_fraction")
    pres = c.get("preservation")
    cons = c.get("consistency")
    if wf is None and pres is None and cons is None:
        return ""

    parts: list[str] = []
    if wf is not None:
        pct = f"{wf * 100:.0f}" if wf * 100 >= 1 else f"{wf * 100:.1f}"
        band = _band(wf, _DENSITY_BAND)
        clause = r.choice(_DENSITY_CLAUSE[band]).format(short=short, pct=pct)
        if pres is not None:
            pband = _band(pres, _PRESERVATION_BAND, descending=True)
            connector = _PRESERVATION_CONNECTOR[pband]
            clause += f", {connector} " + r.choice(_PRESERVATION_CLAUSE[pband])
        parts.append(_sentence(clause))
    elif pres is not None:
        pband = _band(pres, _PRESERVATION_BAND, descending=True)
        parts.append(_sentence(r.choice(_PRESERVATION_CLAUSE[pband])))

    if cons is not None:
        cband = _band(cons, _CONSISTENCY_BAND, descending=True)
        parts.append(_sentence(r.choice(_CONSISTENCY_CLAUSE[cband]).format(short=short)))

    return " ".join(parts)


def _fmt_date(target: date) -> str:
    """'January 5' -- %-d/%#d (no leading zero) is platform-specific (glibc vs
    MSVC), so build it by hand rather than picking one and breaking the other."""
    return f"{target.strftime('%B')} {target.day}"


def _short_name(name: str) -> str:
    """'Alta, UT' -> 'Alta'. Every roster name is 'Name, STATE/COUNTRY'
    (verified: config.MOUNTAINS has no exceptions) -- the possessive
    templates below need the bare name; "Whistler Blackcomb, BC's season"
    reads like the state code owns the season, not the mountain."""
    return name.split(",", 1)[0].strip()


def seasonal_pattern_text(key: str, name: str, wy_start: int,
                          season_window: dict | None,
                          clim: dict[int, dict], target: date,
                          low_confidence_years: int = LOW_CONFIDENCE_YEARS) -> str:
    """The seasonal-pattern paragraph (Part 1 of trip commentary): 1-2
    sentences on what THIS mountain's conditions typically look like during
    the calendar window around `target`, grounded only in real numbers --
    `season_window` (a real per-mountain config fact) and the climatology
    trajectory already computed by ski.trip.climatology (no new data).

    Deterministic per (mountain, calendar day) -- reshuffles only if the
    underlying data changes, unlike the live commentary which reshuffles with
    every regrade (this doesn't depend on "today" at all).
    """
    dowy = target_dowy(target, wy_start)
    c = clim.get(dowy, {}) if clim else {}
    n_years = c.get("n_years", 0)
    base = _plausible_base(c.get("base_in"))
    seed = f"{key}|{target.strftime('%m-%d')}"
    r = random.Random(seed)
    short = _short_name(name)

    if not clim or (base is None and c.get("season_in") is None):
        return r.choice(_NO_HISTORY_FALLBACK).format(name=name)

    date_str = _fmt_date(target)
    # Gate on the SAME in-season test ski.trip.baseline_row uses for this exact
    # climatology point, so this paragraph never calls a historically-bare
    # window "early season" just because it precedes the mountain's OWN
    # season_window (season_progress reads "before the season starts" and
    # "early in the season" as the same 0.0 -- they are not the same thing).
    if is_in_season(c.get("base_in"), c.get("fresh_in")) is not True:
        return r.choice(_OFF_SEASON_CLAUSE).format(date=date_str, short=short) + "."

    if season_window and season_window.get("start") and season_window.get("end"):
        progress = season_progress(target, season_window["start"], season_window["end"])
        stage = _progress_band(progress)
    else:
        stage = "core"  # no season_window on record; describe generically

    open_clause = r.choice(_STAGE_OPEN[stage]).format(date=date_str, name=name, short=short)
    trend = _base_trend(clim, dowy)
    if trend is not None:
        sentence1 = f"{open_clause}, {r.choice(_TREND_CLAUSE[trend])}."
    else:
        sentence1 = f"{open_clause}."

    parts = [sentence1]
    if base is not None:
        parts.append(r.choice(_BASE_NUMBER_CLAUSE).format(
            base=f"{int(round(base))} inches") + ".")
    elif trend is None:
        parts.append(r.choice(_NO_TREND_FALLBACK))
    if n_years and n_years < low_confidence_years:
        parts.append(r.choice(_LOW_CONFIDENCE_CLAUSE) + ".")

    pattern = " ".join(p[:1].upper() + p[1:] for p in parts)
    quality = _quality_character_text(c, short, r)
    return f"{pattern} {quality}" if quality else pattern
