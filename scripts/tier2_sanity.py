"""Live end-to-end sanity check for the Tier-2 temperature-density proxy.

Ingests ONE station per depth-only source (ACIS / ECCC / Open-Meteo) into a
throwaway temp DB, then confirms mean_temp_f actually populated AND that
trip.climatology now emits a non-null `quality` for a mid-winter dowy. Proves the
whole chain (fetch -> parse -> store -> read -> Tier-2 density) before committing
to the full period-of-record backfill.

Run from the project dir:  python scripts/tier2_sanity.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MOUNTAINS  # noqa: E402
from ski import pipeline, trip  # noqa: E402
from ski.db import read_observations  # noqa: E402


def _first_key(source: str) -> str | None:
    for k, m in MOUNTAINS.items():
        if m.get("data_source") == source:
            return k
    return None


def main() -> int:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    ok = True
    try:
        for source in ("acis", "eccc", "openmeteo"):
            key = _first_key(source)
            if key is None:
                print(f"[{source}] no mountain configured -- skipped")
                continue
            m = MOUNTAINS[key]
            station = pipeline.mountain_station(m)
            try:
                n = pipeline.ingest_mountain(key, db_path=path, full=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[{source}] {key}: INGEST FAILED ({exc})")
                ok = False
                continue
            obs = read_observations(path, station)
            temp_rows = int(obs["mean_temp_f"].notna().sum()) if "mean_temp_f" in obs else 0
            clim = trip.climatology(
                obs, pipeline.mountain_wy_start(m),
                pipeline.mountain_season_start(m), pipeline.mountain_metric(m))
            # A representative deep-winter dowy (~Jan 15 in the mountain's water year).
            dowy = trip.target_dowy(date(2025, 1, 15), pipeline.mountain_wy_start(m))
            q = clim.get(dowy, {}).get("quality")
            status = "OK" if (temp_rows > 0 and q is not None) else "NO TIER-2"
            if status != "OK":
                ok = False
            print(f"[{source}] {key} ({station}): rows={n} temp_rows={temp_rows} "
                  f"quality@Jan15={q}  -> {status}")
    finally:
        os.unlink(path)
    print("\nSANITY", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
