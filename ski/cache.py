"""Render cache for per-mountain score summaries.

READ THIS BEFORE ADDING TO IT. `db.py` states the project's design rule: store
only raw daily observations; compute percentiles, grades and alerts on read,
never on write. This module deliberately breaks that rule, in one narrow way, for
one reason.

Scoring the full 79-mountain roster from raw observations takes ~10s even with no
network. That is fine for a CLI and far too slow for a first paint. So we keep a
cache of the *rendered* summary each mountain last produced, purely so the map
has something to draw at t=0 while live data streams in behind it.

The invariants that keep this from rotting into a second source of truth:

  - Nothing in `ski/` ever READS this table to compute a score. It is written by
    the streaming layer and read by the streaming layer, and that is all. Grading
    still runs on raw observations, every time.
  - A cached row is a snapshot of what `service.score_mountain` returned, not a
    partial or transformed version of it. Retuning the grading curve doesn't need
    a migration here; it needs the cache to be stale, which it will be, and which
    the `stale` flag on every served row already announces.
  - Rows are keyed by (mountain_key, as_of) and overwritten in place, so the
    table stays roughly roster-sized rather than growing without bound.

If you find yourself wanting to read a score out of here for anything other than
"what should I paint before the real number arrives", the answer is to call the
scoring service instead.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cached_scores (
    mountain_key TEXT NOT NULL,
    as_of        TEXT NOT NULL,   -- ISO 'YYYY-MM-DD', the date scored FOR
    payload_json TEXT NOT NULL,   -- a service.score_mountain() summary dict
    fetched_at   TEXT NOT NULL,   -- ISO-8601 UTC, when it was actually computed
    PRIMARY KEY (mountain_key, as_of)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs + schema) a connection to the cache DB.

    Shares the file with `raw_observations`; separate table, separate rules.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    apply_pragmas(conn)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


# The stream writes one row per mountain as it lands, from a thread pool, while
# other clients are reading the snapshot off the same file. Under SQLite's default
# rollback journal a writer locks out readers, and with busy_timeout=0 a second
# writer fails instantly with "database is locked" -- which is exactly what killed
# the stream the first time this ran with two clients connected.
#
# WAL lets readers proceed against the last committed snapshot while a writer
# works, so the t=0 paint never queues behind a live fetch. busy_timeout makes
# concurrent writers wait their turn instead of raising.
BUSY_TIMEOUT_S = 5.0


def apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
    # We can afford to lose the last cache write on a hard crash; it's a cache.
    conn.execute("PRAGMA synchronous=NORMAL")


def put(db_path: str | Path, mountain_key: str, as_of: date, payload: dict) -> bool:
    """Cache one mountain's fresh summary, overwriting any prior row for that date.

    Best-effort: returns False (never raises) if the write loses a race for the
    lock. A missed cache write costs the next page load a gray pin for a few
    seconds. Letting it propagate costs the caller their whole SSE stream.
    """
    try:
        conn = connect(db_path)
    except sqlite3.OperationalError:
        return False
    try:
        conn.execute(
            """
            INSERT INTO cached_scores (mountain_key, as_of, payload_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mountain_key, as_of) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at   = excluded.fetched_at
            """,
            (mountain_key, as_of.isoformat(), json.dumps(payload),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def get_all(db_path: str | Path, as_of: date) -> dict[str, dict]:
    """Every cached summary for `as_of`, keyed by mountain.

    Falls back to the most recent prior date per mountain when `as_of` itself was
    never cached -- a day-old snapshot is a much better first paint than an empty
    map, and the `stale` flag tells the frontend not to trust it. `fetched_at` is
    folded into each payload so the UI can say how old it is.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT mountain_key, payload_json, fetched_at, as_of
            FROM cached_scores
            WHERE as_of <= ?
            ORDER BY mountain_key, as_of DESC
            """,
            (as_of.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    out: dict[str, dict] = {}
    for key, payload_json, fetched_at, row_as_of in rows:
        if key in out:            # ORDER BY as_of DESC -> first row per key is newest
            continue
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            continue              # a corrupt row is a cache miss, not a crash
        payload["fetched_at"] = fetched_at
        payload["cached_as_of"] = row_as_of
        out[key] = payload
    return out
