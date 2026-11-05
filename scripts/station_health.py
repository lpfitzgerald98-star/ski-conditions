"""Which mountains' stations have stopped reporting snow?

A station can keep sending rows (temperature, or empty placeholders) long after its
snow readings stop, so "last row" says nothing. This reports, per mountain:
  - days since the last snow reading of any kind (depth, SWE or new snow)
  - the share of in-season days over the past year that had a snow reading

Blue Mountain, Mont Tremblant and June Mountain were silent for 1-2 years before
anyone noticed; this is the check that would have caught them.

Run:  python scripts/station_health.py [--as-of YYYY-MM-DD] [--all]
Exit code 1 when any mountain is flagged, so CI can surface it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_STALE_DAYS, MOUNTAINS  # noqa: E402
from ski import pipeline  # noqa: E402
from ski.db import read_observations  # noqa: E402
from ski.watercalendar import in_season_window  # noqa: E402

SNOW_FIELDS = ["snow_depth_inches", "swe_inches", "new_snow_24hr"]
MIN_SEASON_COVERAGE = 0.5     # under half of last year's season days with a reading


def station_health(obs: pd.DataFrame, as_of: date, window: dict | None) -> dict:
    """Days since the last snow reading, and the share of the past year's in-season
    days (per `window`; every day when there is none) that had one."""
    if obs is None or obs.empty:
        return {"days_silent": None, "season_coverage": 0.0}
    have = obs[obs[SNOW_FIELDS].notna().any(axis=1) & (obs["date"] <= pd.Timestamp(as_of))]
    days_silent = int((pd.Timestamp(as_of) - have["date"].max()).days) if len(have) else None
    days = [as_of - timedelta(days=i) for i in range(1, 366)]
    if window:
        days = [d for d in days if in_season_window(d, tuple(window["start"]), tuple(window["end"]))]
    got = set(have["date"].dt.date)
    coverage = sum(d in got for d in days) / len(days) if days else 0.0
    return {"days_silent": days_silent, "season_coverage": coverage}


def flagged(h: dict, in_season_now: bool) -> str | None:
    if h["days_silent"] is None:
        return "never reported snow"
    if h["season_coverage"] < MIN_SEASON_COVERAGE:
        return f"only {h['season_coverage']:.0%} of last season's days"
    if in_season_now and h["days_silent"] >= DATA_STALE_DAYS:
        return f"silent {h['days_silent']} days in season"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report mountains whose stations stopped reporting snow.")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--all", action="store_true", help="list every mountain, not just flagged ones")
    args = ap.parse_args(argv)

    cache: dict[str, pd.DataFrame] = {}
    problems = 0
    for key, m in sorted(MOUNTAINS.items()):
        st = pipeline.mountain_station(m)
        if st not in cache:
            cache[st] = read_observations(pipeline.DB_PATH, st)
        window = m.get("season_window")
        h = station_health(cache[st], args.as_of, window)
        now = bool(window) and in_season_window(args.as_of, tuple(window["start"]), tuple(window["end"]))
        why = flagged(h, now)
        problems += why is not None
        if why or args.all:
            silent = "-" if h["days_silent"] is None else h["days_silent"]
            print(f"{key:22} {m.get('data_source') or 'snotel':9} {st:14} silent={silent!s:>5}d "
                  f"season_coverage={h['season_coverage']:4.0%}  {why or 'ok'}")
    print(f"\n{problems} of {len(MOUNTAINS)} mountains flagged.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
