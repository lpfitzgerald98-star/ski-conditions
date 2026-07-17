"""Rules-based grade commentary: the one-or-two sentence explanation shown under
a mountain's letter grade, written by plain Python logic instead of a model.

This is the DEFAULT commentary generator (config.COMMENTARY_MODE == "rules"). It
produces the same shape of output as the AI path in ski/commentary.py -- a short,
casual, honest sentence phrasing the card's own numbers -- but with three
properties the AI path can't offer without a key:

  * No network, no ANTHROPIC_API_KEY, no external dependency. Pure stdlib.
  * Deterministic: the same (mountain, day, grade) always yields the same text,
    so a rebuilt snapshot never churns the prose.
  * Honest by construction: like the AI prompt, it only ever states numbers that
    are actually on the scorecard, and skips any field that is null.

VARIETY WITHOUT A MODEL. The obvious failure mode of a template engine is "every
mountain says the same three sentences." We avoid that two ways: (1) the text is
assembled from independent clause pools -- a lead clause chosen by whichever
signal actually drives the grade (fresh snow / season-vs-history / base depth),
an optional forecast clause, and an optional surface caveat -- so the number of
distinct outputs is combinatorial, not additive; and (2) which phrasing is drawn
from each pool is chosen by a per-(mountain, day) seeded RNG, so two mountains
with identical numbers still read differently, while any single card stays
stable day to day.

THE OFF-SEASON GATE IS SOMEONE ELSE'S JOB. Callers reach this module through
commentary.get_or_generate, which first runs commentary.facts_from_card; that
returns None (and we are never called) for off-season, unknown, or unscored
mountains. So `render` may assume it has a real, in-season, scored card.
"""

from __future__ import annotations

import random
from typing import Any

# A ceiling on what we'll call a "base" in prose. Some reanalysis grids over
# glaciated high-altitude terrain (Zermatt) report tens of metres of standing
# snow/ice as snow depth -- a real number on the card, but not a skiable base a
# reader would recognise. Above this we simply don't lead with base depth (the
# raw figure still lives in the conditions panel); another driver carries the
# sentence instead. Not a data fix -- a guard so the prose never says "1312 inches".
_MAX_PLAUSIBLE_BASE_IN = 250.0

# ---------------------------------------------------------------------------
# Small phrasing helpers
# ---------------------------------------------------------------------------


def _inches(x: float) -> str:
    """A rounded inch count as English, singular-aware: '1 inch', '12 inches'."""
    n = int(round(x))
    return f"{n} inch" if n == 1 else f"{n} inches"


def _inch_adj(x: float) -> str:
    """Adjectival form for use before a noun: '30-inch base', '1-inch base'."""
    return f"{int(round(x))}-inch"


def _article(grade: str) -> str:
    """'an A' / 'an F' / 'a B' -- the two vowel-sound grades take 'an'."""
    return "an" if grade[:1] in ("A", "F") else "a"


def _window(hours: Any) -> str:
    """Forecast horizon in words. 24/48/72h are the windows the forecast uses."""
    try:
        h = int(hours)
    except (TypeError, ValueError):
        return "the coming days"
    return {24: "the next day", 48: "the next couple of days",
            72: "the next three days"}.get(h, f"the next {h} hours")


def _tier(grade: str) -> str:
    """Collapse a letter grade to a quality tier for phrasing decisions."""
    letter = grade[:1]
    return {"A": "great", "B": "good", "C": "fair"}.get(letter, "poor")


# Percentile -> descriptor pools. A season-to-date percentile compares a mountain
# to ITS OWN history for the date, so the language is about "for this mountain,
# this time of year," never about other resorts.
_SEASON_DESC = {
    "high": [  # >= 85
        "one of the snowiest starts on record here",
        "well into the top tier of seasons this mountain has logged",
        "running far ahead of a typical winter for the date",
        "among the deepest this mountain has been this early",
    ],
    "good": [  # >= 65
        "ahead of a normal season for this point on the calendar",
        "running better than most winters here for the date",
        "comfortably above its usual pace",
        "shaping up better than an average year",
    ],
    "mid": [  # >= 40
        "right about on pace with a normal season",
        "tracking close to its historical average for the date",
        "a fairly ordinary season so far by its own standards",
    ],
    "low": [  # >= 20
        "running behind a normal season for the date",
        "a bit lean compared with its own history",
        "below its usual pace for this time of year",
    ],
    "poor": [  # < 20
        "one of the leaner starts in this mountain's record",
        "well behind a typical winter for the date",
        "among the thinnest seasons it has logged this early",
    ],
}


def _season_band(pct: float) -> str:
    if pct >= 85:
        return "high"
    if pct >= 65:
        return "good"
    if pct >= 40:
        return "mid"
    if pct >= 20:
        return "low"
    return "poor"


# ---------------------------------------------------------------------------
# Lead clause -- the grade's main driver, phrased around whichever signal is
# actually carrying it. Each returns a full first sentence.
# ---------------------------------------------------------------------------


def _open(r: random.Random, grade: str) -> str:
    """A varied opening that embeds the letter grade, e.g. 'Grading an A-'."""
    art = _article(grade)
    return r.choice([
        f"Grading {art} {grade}",
        f"{grade} conditions",
        f"Scoring {art} {grade}",
        f"This one grades out at {art} {grade}",
        f"An {grade} week" if art == "an" else f"A {grade} week",
    ])


def _lead_fresh(r: random.Random, grade: str, fresh: float) -> str:
    body = r.choice([
        f"{_inches(fresh)} of fresh snow in the last week",
        f"{_inches(fresh)} of new snow over the past seven days",
        f"about {_inches(fresh)} of fresh in the last week",
        f"a solid week of snow, {_inches(fresh)} of it",
    ])
    return f"{_open(r, grade)}: {body}."


def _lead_season(r: random.Random, grade: str, pct: float) -> str:
    desc = r.choice(_SEASON_DESC[_season_band(pct)])
    return r.choice([
        f"{_open(r, grade)}: the season here is {desc}.",
        f"{_open(r, grade)} -- {desc}.",
        f"{_open(r, grade)}. The winter so far is {desc}.",
    ])


def _lead_base(r: random.Random, grade: str, base: float) -> str:
    body = r.choice([
        f"a {_inch_adj(base)} base underfoot",
        f"{_inches(base)} of settled base to ride on",
        f"a deep {_inch_adj(base)} base",
    ])
    return f"{_open(r, grade)}: {body}."


def _lead_generic(r: random.Random, grade: str) -> str:
    """No single number stands out -- speak to the tier honestly."""
    tier = _tier(grade)
    body = {
        "great": ["conditions are about as good as it gets here right now",
                  "just about everything is lining up on the mountain"],
        "good": ["a solid setup on the mountain right now",
                 "conditions are in good shape overall"],
        "fair": ["a middling setup -- rideable, nothing special right now",
                 "okay conditions, neither great nor grim"],
        "poor": ["thin conditions on the mountain right now",
                 "not much to work with at the moment"],
    }[tier]
    return r.choice([
        f"{_open(r, grade)}: {r.choice(body)}.",
        f"{_open(r, grade)} -- {r.choice(body)}.",
    ])


def _lead(r: random.Random, grade: str, f: dict) -> str:
    """Choose the lead by whichever signal is genuinely driving the grade."""
    fresh = f.get("fresh_snow_last_7_days_inches")
    base = f.get("base_depth_inches")
    season = f.get("season_to_date_percentile_vs_history")
    # An implausible base (glacier depth from reanalysis) must never lead the
    # sentence; treat it as unavailable for phrasing. See _MAX_PLAUSIBLE_BASE_IN.
    if base is not None and base > _MAX_PLAUSIBLE_BASE_IN:
        base = None

    if fresh is not None and fresh >= 8:
        return _lead_fresh(r, grade, fresh)
    if season is not None and season >= 70:
        return _lead_season(r, grade, season)
    if base is not None and base >= 45:
        return _lead_base(r, grade, base)
    if fresh is not None and fresh >= 3:
        return _lead_fresh(r, grade, fresh)
    if season is not None and season <= 30:
        return _lead_season(r, grade, season)
    if base is not None and base >= 24:
        return _lead_base(r, grade, base)
    if season is not None:
        return _lead_season(r, grade, season)
    return _lead_generic(r, grade)


# ---------------------------------------------------------------------------
# Forecast clause -- optional second sentence about what's coming.
# ---------------------------------------------------------------------------


def _forecast_active(r: random.Random, f: dict, card: dict | None) -> str | None:
    """Actual forecast NEWS -- an incoming storm, a thaw, or a little snow.

    Returns None when the forecast is simply dry (no news), so a more useful
    surface/data caveat can take the second sentence ahead of bland filler."""
    incoming = f.get("incoming_snow_inches")
    window = _window(f.get("incoming_snow_window_hours"))
    alert = f.get("storm_alert")
    thaw = ((card or {}).get("outlook") or {}).get("thaw_index")

    # A meaningful incoming storm always wins the second sentence.
    if incoming is not None and incoming >= 6:
        if alert:
            return r.choice([
                f"A storm alert is up: around {_inches(incoming)} expected over {window}.",
                f"Bigger news is the forecast -- roughly {_inches(incoming)} on the way in {window}.",
                f"And there's a storm on the board: about {_inches(incoming)} forecast over {window}.",
            ])
        return r.choice([
            f"More is on the way -- about {_inches(incoming)} forecast over {window}.",
            f"The forecast adds another {_inches(incoming)} or so over {window}.",
            f"And there's more coming: roughly {_inches(incoming)} in {window}.",
        ])

    # A warm, wet spell coming in -- the surface is about to suffer.
    if thaw is not None and thaw >= 0.5:
        return r.choice([
            "A warm, wet spell in the forecast is likely to soften the surface, though.",
            "Watch the forecast, though -- warm rain is coming and the snow will go heavy.",
            "The catch is a thaw on the way that should leave things sloppy.",
        ])

    # A little something, not a storm.
    if incoming is not None and incoming >= 2:
        return r.choice([
            f"A little more is in the forecast: {_inches(incoming)} over {window}.",
            f"Only light snow ahead -- about {_inches(incoming)} over {window}.",
        ])
    return None  # dry forecast is not "news" -- see _forecast_dry


def _forecast_dry(r: random.Random, f: dict) -> str | None:
    """The dry-forecast filler, read up or down by how good things already are.
    Lowest priority for the second sentence, behind any real caveat."""
    tier = _tier(f["overall_grade"])
    if tier in ("great", "good"):
        return r.choice([
            "Nothing new in the forecast, but there's plenty already down.",
            "The forecast is quiet, so it's about riding what's there.",
        ])
    if tier == "poor":
        return r.choice([
            "And no new snow in the forecast to turn it around.",
            "The forecast is dry, so don't wait on a rescue.",
        ])
    return None  # fair + dry: let the lead stand alone


# ---------------------------------------------------------------------------
# Caveat clause -- a short surface/data note, used only when there's no forecast
# sentence, so we never exceed two sentences.
# ---------------------------------------------------------------------------


def _caveat(r: random.Random, f: dict, card: dict | None) -> str | None:
    card = card or {}
    refreeze = (card.get("outlook") or {}).get("refreeze_index")
    base = f.get("base_depth_inches")
    stale = card.get("stale")
    age = card.get("data_age_days")

    if refreeze is not None and refreeze >= 0.5:
        return r.choice([
            "Expect a firm, crusty surface early after a recent melt-freeze.",
            "A recent melt-freeze means an icy start until it softens.",
        ])
    if base is not None and base < 18 and _tier(f["overall_grade"]) != "poor":
        return r.choice([
            "Coverage is still thin, so mind the early-season hazards.",
            "The base is shallow, though -- watch for buried obstacles.",
        ])
    if stale and age is not None:
        days = int(age)
        unit = "day" if days == 1 else "days"
        return f"(Latest station reading is {days} {unit} old.)"
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render(facts: dict, card: dict | None = None) -> str:
    """The one-or-two sentence grade explanation for an in-season, scored card.

    `facts` is commentary.facts_from_card(card) -- already gated non-None, so a
    grade and its driving numbers are present. `card` is the full scorecard, used
    only for the optional forecast/surface extras (thaw, refreeze, staleness).
    """
    grade = facts["overall_grade"]
    # Stable per (mountain, day, grade); varied across mountains. A regrade on the
    # same day reshuffles, which is fine -- the numbers changed too.
    seed = f"{facts.get('mountain')}|{facts.get('date')}|{grade}"
    r = random.Random(seed)

    parts = [_lead(r, grade, facts)]
    # Priority for the one optional second sentence: real forecast news, then a
    # surface/data caveat (crust, thin base, stale station), then dry-forecast
    # filler last -- so a stale-station note never loses to "nothing new".
    second = (_forecast_active(r, facts, card)
              or _caveat(r, facts, card)
              or _forecast_dry(r, facts))
    if second:
        parts.append(second)
    return " ".join(parts)
