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
    off-season gate applies (no positive evidence of cover, or no score)."""
    if card.get("in_season") is not True:
        return None
    overall = (card.get("overall") or {}).get(card.get("default_profile")) or {}
    if overall.get("score") is None:
        return None

    g = card.get("grades") or {}
    season, month = g.get("season") or {}, g.get("in_season") or {}
    cond = card.get("conditions") or {}
    fc = card.get("forecast") or {}
    return {
        "mountain": (card.get("mountain") or {}).get("name"),
        "date": card.get("as_of"),
        "overall_grade": overall.get("grade"),
        "fresh_snow_last_7_days_inches": cond.get("fresh_7d"),
        "base_depth_inches": cond.get("base_depth"),
        "season_to_date_percentile_vs_history": season.get("percentile"),
        "last_30_days_percentile_vs_history": month.get("percentile"),
        "incoming_snow_inches": fc.get("inches"),
        "incoming_snow_window_hours": fc.get("window_hours"),
        "storm_alert": fc.get("alert") or None,
        "season_progress_fraction": card.get("season_progress"),
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
        grade = facts.get("overall_grade")
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
