"""AI commentary: one plain-language sentence or two explaining a grade.

"Grading an A this week: 18 inches of fresh snow in the last 7 days, well above
the 90th percentile for mid-January, with more on the way this weekend." The
letter grade compresses a lot of scoring machinery into one character; this
module decompresses it back into language a casual skier reads at a glance.

Three rules keep it honest and cheap:

  1. THE MODEL PHRASES, IT DOES NOT MEASURE. `facts_from_card` extracts the
     exact numbers already on the scorecard (grade, percentiles, fresh snow,
     base, incoming) and the prompt forbids inventing values. If the model
     can't be reached, the card simply has no commentary -- never a made-up one.
  2. GENERATED IN THE SCORING JOB, NEVER PER PAGE LOAD. Callers go through
     `get_or_generate`, which caches one row per mountain per as_of date in
     SQLite. The daily build (and the first live card request of the day) pays
     for one API call per mountain; everything after reads the cache.
  3. OFF-SEASON IS SILENT. The roster suppresses ranks without positive
     evidence of cover (see service.rank_within_regions); commentary follows
     the same gate. No prose for a bare or unknown mountain -- an explanation
     of a suppressed score would just re-lie the lie the suppression removed.

The Anthropic client resolves credentials from the environment (ANTHROPIC_API_KEY
or an `ant auth login` profile). With no credentials, generation quietly yields
None -- a snapshot build without a key still succeeds, just without commentary.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from config import COMMENTARY_MODE, DB_PATH

MODEL = "claude-opus-4-8"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commentary (
    mountain_key TEXT NOT NULL,
    as_of        TEXT NOT NULL,   -- ISO date the commentary describes
    grade        TEXT,            -- the overall grade it explains (staleness check)
    text         TEXT NOT NULL,
    model        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (mountain_key, as_of)
);
"""

_SYSTEM = """\
You write the one-or-two-sentence explanation shown under a ski mountain's
letter grade (A+ best, F worst). Plain language for a casual skier -- no
jargon, no percent-sign soup, no greetings, no hedging.

Rules:
- Use ONLY the numbers provided in the JSON. Never invent, extrapolate, or
  round beyond what is given. Skip any field that is null.
- Lead with the grade's main driver: recent snowfall, how the season compares
  to this mountain's own history (the percentiles), base depth, or incoming
  snow.
- SURFACE QUALITY OVERRIDES QUANTITY. `surface_quality_factor` is 1.0 for a
  pristine surface and lower when the snow skis badly. When snow totals are high
  but this factor is low, LEAD with the surface problem, not the inches: a
  high `refreeze_crust_index` means a refrozen, firm/crusty surface; a high
  `incoming_thaw_index` means warm rain is about to turn it heavy and wet; a high
  `new_snow_density` (water fraction, ~0.15+) means the fresh snow itself fell
  heavy and wet rather than light and dry; a high `wind_scour_index` (~0.4+) means
  sustained wind has scoured or wind-slabbed the fresh snow. The grade already
  reflects the poor surface, so the sentence must too -- "big totals but
  crusty/heavy/wind-hammered" reads very differently from the same totals cold,
  light, calm, and clean.
- "percentile" fields compare this mountain to ITS OWN historical record for
  this point in the season, not to other mountains.
- At most two sentences. No markdown, no lists.\
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.execute(_SCHEMA)
    return conn


def facts_from_card(card: dict) -> dict | None:
    """The structured inputs the model is allowed to phrase, or None when the
    off-season gate applies (no positive evidence of cover, or no score).

    The grade explained here is `skiability` (absolute "how good is the
    skiing right now") -- the same grade that drives the pin, the leaderboard,
    and the card's headline tile. Explaining the self-relative `overall`
    grade instead would let the prose's tone disagree with the number the
    reader is looking at."""
    if card.get("in_season") is not True:
        return None
    skiability = card.get("skiability") or {}
    if skiability.get("score") is None:
        return None

    g = card.get("grades") or {}
    season, month = g.get("season") or {}, g.get("in_season") or {}
    cond = card.get("conditions") or {}
    fc = card.get("forecast") or {}
    outlook = card.get("outlook") or {}
    sq = card.get("snow_quality") or {}
    return {
        "mountain": (card.get("mountain") or {}).get("name"),
        "date": card.get("as_of"),
        "grade": skiability.get("grade"),
        "fresh_snow_last_7_days_inches": cond.get("fresh_7d"),
        "base_depth_inches": cond.get("base_depth"),
        "season_to_date_percentile_vs_history": season.get("percentile"),
        "last_30_days_percentile_vs_history": month.get("percentile"),
        "incoming_snow_inches": fc.get("inches"),
        "incoming_snow_window_hours": fc.get("window_hours"),
        "storm_alert": fc.get("alert") or None,
        "season_progress_fraction": card.get("season_progress"),
        # Surface-quality signals (Phase 1): the SAME numbers that scale the
        # skiability grade down, exposed here so BOTH the rules path and the AI
        # path can explain the grade in skiability terms rather than dressing a
        # quantity number in adjectives. quality_factor is 1.0 (pristine) down to
        # SKI_QUALITY['floor']; the two indices are 0 (clean) .. 1 (severe).
        "surface_quality_factor": skiability.get("quality_factor"),
        "refreeze_crust_index": outlook.get("refreeze_index"),
        "incoming_thaw_index": outlook.get("thaw_index"),
        "weather_quality": cond.get("weather_quality"),
        # New-snow water fraction (Phase 2): ~0.05 light/dry .. ~0.20+ heavy/wet.
        # Lets the prose say WHY a big-totals day still skis heavy, and vouch for
        # light snow when it's genuinely dry.
        "new_snow_density": skiability.get("new_snow_density"),
        # Wind scour (Phase 3), 0 (calm) .. 1 (fresh snow stripped/slabbed). Lets
        # the prose flag a wind-hammered storm, and vouch for wind-sheltered snow.
        "wind_scour_index": skiability.get("wind_scour"),
        # Buried rain/melt crust (Phase 5b), 0..1 -- an OLD ice layer lurking under
        # newer snow, distinct from a fresh melt-freeze. Lets the prose name it.
        "buried_crust_index": skiability.get("buried_crust"),
        # The explainable snow-quality number itself (Phase 0 scaffold; density/
        # wind land in Phases 2/3). Carried for the AI path and future clauses.
        "snow_quality_score": sq.get("score"),
    }


def generate(facts: dict) -> str | None:
    """One API call: facts in, one-or-two sentences out. None on any failure
    (no credentials, network trouble, refusal) -- commentary is an ornament,
    and no caller should break for want of it."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts)}],
        )
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return text.strip() or None
    except Exception:  # noqa: BLE001 -- see docstring
        return None


def get_or_generate(key: str, as_of: date, card: dict,
                    db_path: str | Path | None = None) -> str | None:
    """The one entry point: cached commentary for (mountain, day), generating
    and storing it on the first miss. Returns None for off-season/unscored
    cards and when generation isn't possible (nothing is cached then, so a
    later run with credentials can fill the gap)."""
    facts = facts_from_card(card)
    if facts is None:
        return None

    # Default path: deterministic, free, no key required. Same off-season gate
    # (facts is None above), same output field. A flip of config.COMMENTARY_MODE
    # (or the COMMENTARY_MODE env var) swaps to the AI path below -- see config.
    if COMMENTARY_MODE != "ai":
        from ski import commentary_rules
        return commentary_rules.render(facts, card)

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT text, grade FROM commentary WHERE mountain_key=? AND as_of=?",
            (key, as_of.isoformat())).fetchone()
        grade = facts.get("grade")
        # A same-day regrade (live rescoring moved the letter) invalidates the
        # cached prose -- it would explain a grade the card no longer shows.
        if row and row[1] == grade:
            return row[0]

        text = generate(facts)
        if text is None:
            return None
        conn.execute(
            "INSERT OR REPLACE INTO commentary VALUES (?, ?, ?, ?, ?, ?)",
            (key, as_of.isoformat(), grade, text, MODEL,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.commit()
        return text
    finally:
        conn.close()
