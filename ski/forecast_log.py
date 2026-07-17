"""Log of live forecast predictions, for backtesting forecast skill against
what SNOTEL/ACIS/etc. later observed (see config.FORECAST_HORIZON_WEIGHTS,
pipeline.forecast_accuracy).

Same separate-table-same-file pattern cache.py uses for rendered scores: never
read by the scoring path (a logging failure must not sink a card render), only
written by `pipeline.mountain_scorecard` and read by the backtest report.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ski import cache  # shared-file concurrency pragmas + BUSY_TIMEOUT_S

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_log (
    mountain_key          TEXT NOT NULL,
    as_of                 TEXT NOT NULL,   -- ISO date the forecast was made FOR
    horizon_hours         INTEGER NOT NULL,
    predicted_inches      REAL,
    predicted_percentile  REAL,
    tmax_f                REAL,
    fetched_at            TEXT NOT NULL,   -- ISO-8601 UTC, when this row was recorded
    PRIMARY KEY (mountain_key, as_of, horizon_hours)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=cache.BUSY_TIMEOUT_S)
    cache.apply_pragmas(conn)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def record(db_path: str | Path, mountain_key: str, as_of: date, horizon_hours: int,
          predicted_inches: float | None, predicted_percentile: float | None,
          tmax_f: float | None) -> bool:
    """Log one horizon's live prediction, once per (mountain, as_of, horizon).

    `ON CONFLICT DO NOTHING`: the FIRST prediction of the day wins, so a mountain
    streamed a dozen times over the day doesn't spam the table, and the logged
    value stays close to the morning forecast the freshness spec cares about
    most. Best-effort: never raises.
    """
    try:
        conn = connect(db_path)
    except sqlite3.OperationalError:
        return False
    try:
        conn.execute(
            """
            INSERT INTO forecast_log
                (mountain_key, as_of, horizon_hours, predicted_inches,
                 predicted_percentile, tmax_f, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mountain_key, as_of, horizon_hours) DO NOTHING
            """,
            (mountain_key, as_of.isoformat(), horizon_hours, predicted_inches,
             predicted_percentile, tmax_f, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def read_log(db_path: str | Path, mountain_key: str | None = None) -> pd.DataFrame:
    """Every logged forecast, optionally filtered to one mountain."""
    conn = connect(db_path)
    try:
        q = ("SELECT mountain_key, as_of, horizon_hours, predicted_inches, "
            "predicted_percentile, tmax_f, fetched_at FROM forecast_log")
        params: tuple = ()
        if mountain_key is not None:
            q += " WHERE mountain_key = ?"
            params = (mountain_key,)
        df = pd.read_sql_query(q, conn, params=params)
    finally:
        conn.close()
    if not df.empty:
        df["as_of"] = pd.to_datetime(df["as_of"])
    return df
