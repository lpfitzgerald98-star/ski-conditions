"""Backfill mean_temp_f (and, for ECCC, the now-corrected snow depth) by re-pulling
the full period of record for every DEPTH-ONLY station -- ACIS / ECCC / Open-Meteo.

The 38 SWE stations (SNOTEL/CDEC/BCSWS) are Tier-1 and don't need temperature, so
they're skipped. Idempotent: upsert overwrites in place, so re-running is safe (and
resumes cheaply, since already-current rows just rewrite the same values).

Open-Meteo (54 stations back to 1980) is the slow, rate-limited part; failures are
caught per-station and reported at the end so one 429 doesn't abort the run -- just
re-run to fill the gaps.

Run from the project dir:  python scripts/backfill_temp.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MOUNTAINS  # noqa: E402
from ski import pipeline  # noqa: E402
from ski.db import read_observations  # noqa: E402

DEPTH_ONLY = {"acis", "eccc", "openmeteo"}

# Open-Meteo's free archive rate-limits hard on 54 back-to-back full-POR pulls, so
# pace requests and back off on 429. ACIS/ECCC are generous, so only Open-Meteo
# needs the inter-request delay.
INTER_REQUEST_S = {"openmeteo": 12.0}
MAX_RETRIES = 5
BACKOFF_S = [30, 60, 120, 240, 300]   # per successive 429


def _temp_rows(station: str) -> int:
    obs = read_observations(pipeline.DB_PATH, station)
    return int(obs["mean_temp_f"].notna().sum()) if "mean_temp_f" in obs else 0


def _ingest_with_backoff(key: str, src: str) -> int:
    """Full ingest, retrying a 429 with exponential backoff. Re-raises anything else
    (and a 429 that outlasts every retry)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return pipeline.ingest_mountain(key, full=True)
        except Exception as exc:  # noqa: BLE001
            is_429 = "429" in str(exc)
            if not is_429 or attempt == MAX_RETRIES:
                raise
            wait = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            print(f"        {src} {key}: 429, backing off {wait}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    return 0  # unreachable


def main() -> int:
    # Resumable: skip stations that already have real temp coverage (a prior run or a
    # SWE fallback), so a re-run only chases the gaps -- cheap and 429-friendly.
    todo = []
    for k, m in MOUNTAINS.items():
        if m.get("data_source") not in DEPTH_ONLY:
            continue
        if _temp_rows(pipeline.mountain_station(m)) >= 100:
            continue
        todo.append(k)

    print(f"Backfilling {len(todo)} station(s) still missing temperature "
          f"into {pipeline.DB_PATH}\n")
    failed = []
    for i, key in enumerate(todo, 1):
        m = MOUNTAINS[key]
        src = m.get("data_source")
        station = pipeline.mountain_station(m)
        t0 = time.time()
        try:
            n = _ingest_with_backoff(key, src)
            dt = time.time() - t0
            print(f"[{i:3}/{len(todo)}] {src:9} {key:22} rows={n:6} "
                  f"temp_rows={_temp_rows(station):6}  {dt:5.1f}s")
        except Exception as exc:  # noqa: BLE001
            failed.append((key, src, repr(exc)))
            print(f"[{i:3}/{len(todo)}] {src:9} {key:22} FAILED: {exc}")
        time.sleep(INTER_REQUEST_S.get(src, 0.0))

    print(f"\nDone. {len(todo) - len(failed)}/{len(todo)} succeeded.")
    if failed:
        print("Still failed (re-run to retry just these):")
        for key, src, err in failed:
            print(f"  {src:9} {key:22} {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
