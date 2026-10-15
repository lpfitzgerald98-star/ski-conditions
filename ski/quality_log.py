"""Log of the LIVE-ONLY snow-quality inputs, for backtesting the quality weights.

WHY: wind (22%+ of SNOW_QUALITY_WEIGHTS), thaw and warmth/weather_q are pulled
live from the forecast provider and were never persisted -- raw_observations has
no wind column, ever -- so scripts/backtest_live.py has to leave them None and
those weights have never been tested (see that script's docstring). Logging what
the live card actually saw, one row per (mountain, as_of), accumulates the
history a later backtest needs (scripts/backtest_forecast.py reads it back).

Same separate-table-same-file pattern as forecast_log.py: never read by the
scoring path, written best-effort by `pipeline` (a logging failure must never
sink a card render), first row of the day wins.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ski import cache  # shared-file concurrency pragmas + BUSY_TIMEOUT_S

# Every logged signal is a nullable REAL, in this order (also the CSV column order
# scripts/sync_logs.py writes). Raw inputs AND the derived 0-100/0..1 values the
# score used, so a backtest can either replay the scoring functions on the raw
# inputs (if they're re-tuned later) or use what the card showed verbatim.
FIELDS = (
    "wind_sustained_72h_mph",   # Recent.wind_sustained_72h_mph
    "wind_quality",             # score.wind_quality(wind)            0-100
    "wind_scour",               # score.wind_scour_index(wind, fresh) 0..1
    "thaw_index",               # forward rain/warmth risk            0..1
    "refreeze_index",           # trailing-72h melt-freeze crust      0..1
    "weather_quality",          # current temp/wind/sky               0-100
    "current_temp_f",
    "current_wind_mph",
    "sky_cover_pct",
    "rain_72h_in",              # forward 72h rain (outlook)
    "tmax_72h_f",               # forward 72h warmest
    "recent_rain_72h_in",       # trailing 72h rain (Recent)
    "recent_tmax_72h_f",
    "recent_tmin_24h_f",
    "recent_snowfall_72h_in",
    "recent_snow_temp_72h_f",
    "density_ratio",            # new-snow water fraction the card used
    "snow_quality",             # SnowQuality.value                   0-100
)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS quality_log (\n"
    "    mountain_key TEXT NOT NULL,\n"
    "    as_of        TEXT NOT NULL,   -- ISO date the card was scored FOR\n"
    "    provider     TEXT,            -- outlook provider (nws | openmeteo)\n"
    + "".join(f"    {f} REAL,\n" for f in FIELDS)
    + "    fetched_at   TEXT NOT NULL,   -- ISO-8601 UTC, when this row was recorded\n"
    "    PRIMARY KEY (mountain_key, as_of)\n"
    ");"
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=cache.BUSY_TIMEOUT_S)
    cache.apply_pragmas(conn)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def record(db_path: str | Path, mountain_key: str, as_of: date,
           provider: str | None, values: dict) -> bool:
    """Log one card's live quality inputs, once per (mountain, as_of).

    `values` maps FIELDS names to numbers (missing/None -> NULL; unknown keys are
    ignored). ON CONFLICT DO NOTHING: the first scoring of the day wins, matching
    forecast_log. Best-effort: never raises."""
    cols = ["mountain_key", "as_of", "provider", *FIELDS, "fetched_at"]
    row = [mountain_key, as_of.isoformat(), provider,
           *(_num(values.get(f)) for f in FIELDS),
           datetime.now(timezone.utc).isoformat()]
    try:
        conn = connect(db_path)
    except sqlite3.OperationalError:
        return False
    try:
        conn.execute(
            f"INSERT INTO quality_log ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))}) "
            "ON CONFLICT(mountain_key, as_of) DO NOTHING",
            row,
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def read_log(db_path: str | Path, mountain_key: str | None = None) -> pd.DataFrame:
    """Every logged row, optionally filtered to one mountain."""
    conn = connect(db_path)
    try:
        q = "SELECT * FROM quality_log"
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


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN -> NULL
