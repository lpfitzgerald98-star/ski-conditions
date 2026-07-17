"""SQLite storage for RAW daily observations.

Design rule from the spec: store only raw daily observations keyed by
(station_id, date). Percentiles, grades and alerts are computed on READ, never
stored. That way re-tuning the grading curve is a config change, never a DB
migration.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date
from pathlib import Path

import pandas as pd

from ski import cache  # for the shared-file concurrency pragmas only

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_observations (
    station_id        TEXT NOT NULL,
    date              TEXT NOT NULL,   -- ISO 'YYYY-MM-DD'
    swe_inches        REAL,            -- snow water equivalent (NRCS WTEQ)
    snow_depth_inches REAL,            -- snow depth (NRCS SNWD)
    new_snow_24hr     REAL,            -- derived: positive day-over-day depth change
    PRIMARY KEY (station_id, date)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs + schema) a connection to the obs DB.

    Shares its file with `cache.cached_scores`, which the live stream writes to
    concurrently. `journal_mode` is a persistent property of the file, but
    `busy_timeout` is per-connection -- so a reader opened here still has to be
    told to wait for a writer rather than raise "database is locked".
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=cache.BUSY_TIMEOUT_S)
    cache.apply_pragmas(conn)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def upsert_observations(db_path: str | Path, station_id: str, df: pd.DataFrame) -> int:
    """Insert-or-replace daily rows for a station.

    `df` must have columns: date (date/Timestamp), swe_inches,
    snow_depth_inches, new_snow_24hr. Returns the number of rows written.
    """
    required = {"date", "swe_inches", "snow_depth_inches", "new_snow_24hr"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"observations frame missing columns: {sorted(missing)}")

    rows = []
    for r in df.itertuples(index=False):
        d = pd.Timestamp(r.date).date().isoformat()
        rows.append((
            station_id,
            d,
            _nan_to_none(r.swe_inches),
            _nan_to_none(r.snow_depth_inches),
            _nan_to_none(r.new_snow_24hr),
        ))

    conn = connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO raw_observations
                (station_id, date, swe_inches, snow_depth_inches, new_snow_24hr)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(station_id, date) DO UPDATE SET
                swe_inches        = excluded.swe_inches,
                snow_depth_inches = excluded.snow_depth_inches,
                new_snow_24hr     = excluded.new_snow_24hr
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def max_observation_date(db_path: str | Path, station_id: str) -> "date | None":
    """The most recent stored date for a station (as a `date`), or None if the
    station has no rows yet. Drives incremental ingest: fetch only the tail
    after this instead of the whole period of record every run.
    """
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM raw_observations WHERE station_id = ?",
            (station_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return _date.fromisoformat(row[0])


def read_observations(db_path: str | Path, station_id: str) -> pd.DataFrame:
    """Return all stored observations for a station as a DataFrame.

    `date` comes back as a datetime64 column, sorted ascending. Empty frame with
    the right columns if the station has no rows yet.
    """
    conn = connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT date, swe_inches, snow_depth_inches, new_snow_24hr
            FROM raw_observations
            WHERE station_id = ?
            ORDER BY date ASC
            """,
            conn,
            params=(station_id,),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def _nan_to_none(v):
    """SQLite stores NULL, not NaN -- convert so missing stays missing."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)
