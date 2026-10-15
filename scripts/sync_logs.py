"""Mirror the live-prediction logs (forecast_log, quality_log) to committed CSVs.

WHY: both tables live in data/ski.db, which the daily Action keeps ONLY in the
Actions cache. A cache eviction (7 days unused, or the repo's cache size cap) is
self-healing for raw_observations -- a full re-ingest refills it -- but NOT for
these logs: what a forecast said on a past morning can never be re-fetched. A
season of logs is the only way the forecast weight and the live-only quality
weights (wind/thaw/warmth) can ever be backtested (scripts/backtest_forecast.py),
so the logs are also committed to the repo, one CSV per table per month.

  python scripts/sync_logs.py export   # DB -> logs/<table>/YYYY-MM.csv
  python scripts/sync_logs.py import   # logs/*.csv -> DB (ON CONFLICT DO NOTHING)

Export rewrites each month's file from the DB, sorted, so a daily run's git diff
is just that day's new rows. Import is idempotent and runs BEFORE the build, so a
cold cache gets its log history back before today's rows are appended. Export
unions with the committed file, so it can never shrink the committed record.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ski import forecast_log, quality_log  # noqa: E402
from ski.pipeline import DB_PATH  # noqa: E402

LOG_DIR = ROOT / "logs"

# table -> (connect fn that creates the schema, primary-key columns)
TABLES = {
    "forecast_log": (forecast_log.connect, ["mountain_key", "as_of", "horizon_hours"]),
    "quality_log": (quality_log.connect, ["mountain_key", "as_of"]),
}


def _read_table(db_path, table: str) -> pd.DataFrame:
    connect, _ = TABLES[table]
    conn = connect(db_path)
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    finally:
        conn.close()


def export(db_path=DB_PATH, log_dir: Path = LOG_DIR) -> dict[str, int]:
    """Write each table's rows to log_dir/<table>/YYYY-MM.csv. Returns rows written."""
    written = {}
    for table, (_, pk) in TABLES.items():
        df = _read_table(db_path, table)
        written[table] = 0
        if df.empty:
            continue
        out_dir = log_dir / table
        out_dir.mkdir(parents=True, exist_ok=True)
        df = df.round(6)
        df["_month"] = df["as_of"].str[:7]
        for month, part in df.groupby("_month"):
            f = out_dir / f"{month}.csv"
            part = part.drop(columns="_month").sort_values(pk)
            if f.exists():
                # Union with what's already committed (committed rows first, so
                # first-of-day still wins): a DB that lost history can't shrink it.
                part = (pd.concat([pd.read_csv(f), part])
                        .drop_duplicates(subset=pk, keep="first")
                        .sort_values(pk))
            # Round so a CSV->DB->CSV round trip is byte-stable (a re-parsed
            # 15-digit float's repr can drift in the last place).
            part.round(6).to_csv(f, index=False, lineterminator="\n")
            written[table] += len(part)
    return written


def import_(db_path=DB_PATH, log_dir: Path = LOG_DIR) -> dict[str, int]:
    """Insert every committed CSV row the DB is missing. Returns rows inserted."""
    inserted = {}
    for table, (connect, _) in TABLES.items():
        files = sorted((log_dir / table).glob("*.csv"))
        inserted[table] = 0
        if not files:
            continue
        conn = connect(db_path)
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for f in files:
                df = pd.read_csv(f)
                df = df[[c for c in cols if c in df.columns]]
                df = df.astype(object).where(df.notna(), None)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(df.columns)}) "
                    f"VALUES ({', '.join('?' * len(df.columns))}) ON CONFLICT DO NOTHING",
                    df.itertuples(index=False, name=None),
                )
            conn.commit()
            inserted[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - before
        finally:
            conn.close()
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    if args.action == "export":
        print("exported:", export(args.db))
    else:
        print("imported:", import_(args.db))
