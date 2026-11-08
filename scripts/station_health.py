"""Which mountains' stations have stopped reporting snow?

A station can keep sending rows (temperature, or empty placeholders) long after its
snow readings stop, so "last row" says nothing. This reports, per mountain:
  - days since the last snow reading of any kind (depth, SWE or new snow)
  - the share of in-season days over the past year that had a snow reading

Blue Mountain, Mont Tremblant and June Mountain were silent for 1-2 years before
anyone noticed; this is the check that would have caught them.

A mountain moved off a dead station keeps the old one under `watch_station` in
config. `--watch` asks each watched station directly (network) whether it has
sent snow readings again, so the move can be reconsidered.

Run:  python scripts/station_health.py [--as-of YYYY-MM-DD] [--all] [--watch] [--json PATH]
Exit code 1 when any mountain is flagged, so CI can surface it. With --watch,
exit code 3 when a watched station is back (it takes precedence). --json writes
{"as_of", "flagged": [...], "back": [...]} for scripts/station_issues.py.
"""

from __future__ import annotations

import argparse
import json
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


def station_back(obs: pd.DataFrame, silent_since: date) -> date | None:
    """The latest snow reading after `silent_since`, or None while still silent."""
    if obs is None or obs.empty:
        return None
    have = obs[obs[SNOW_FIELDS].notna().any(axis=1) & (obs["date"] > pd.Timestamp(silent_since))]
    return have["date"].max().date() if len(have) else None


def check_watched(as_of: date) -> list[dict]:
    """Fetch each watched station's recent data and list the ones sending snow again."""
    back = []
    for key, m in sorted(MOUNTAINS.items()):
        w = m.get("watch_station")
        if not w:
            continue
        silent_since = date.fromisoformat(w["silent_since"])
        try:
            obs = pipeline.SOURCES[w["data_source"]]["fetch"](w["id"], since=silent_since)
            obs["date"] = pd.to_datetime(obs["date"])
            last = station_back(obs, silent_since)
        except Exception as e:  # noqa: BLE001 -- a fetch failure is "unknown", not "back"
            print(f"{key}: couldn't check old station {w['name']} ({w['id']}): {e}")
            continue
        if last:
            back.append({"key": key, "name": m.get("name", key), "old_station": w["name"],
                         "old_id": f"{w['data_source']} {w['id']}", "latest": str(last),
                         "current_station": m.get("station_name")})
            print(f"{key}: old station {w['name']} ({w['data_source']} {w['id']}) is reporting "
                  f"snow again, latest {last}. Consider moving back from {m.get('station_name')}.")
        else:
            print(f"{key}: old station {w['name']} still silent since {silent_since}.")
    return back


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report mountains whose stations stopped reporting snow.")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--all", action="store_true", help="list every mountain, not just flagged ones")
    ap.add_argument("--watch", action="store_true", help="also check stations kept under watch_station")
    ap.add_argument("--json", type=Path, help="also write the findings to this file")
    args = ap.parse_args(argv)

    cache: dict[str, pd.DataFrame] = {}
    found = []
    for key, m in sorted(MOUNTAINS.items()):
        st = pipeline.mountain_station(m)
        if st not in cache:
            cache[st] = read_observations(pipeline.DB_PATH, st)
        window = m.get("season_window")
        h = station_health(cache[st], args.as_of, window)
        now = bool(window) and in_season_window(args.as_of, tuple(window["start"]), tuple(window["end"]))
        why = flagged(h, now)
        if why:
            found.append({"key": key, "name": m.get("name", key), "station": m.get("station_name", st),
                          "source": f"{m.get('data_source') or 'snotel'} {st}", "reason": why})
        if why or args.all:
            silent = "-" if h["days_silent"] is None else h["days_silent"]
            print(f"{key:22} {m.get('data_source') or 'snotel':9} {st:14} silent={silent!s:>5}d "
                  f"season_coverage={h['season_coverage']:4.0%}  {why or 'ok'}")
    print(f"\n{len(found)} of {len(MOUNTAINS)} mountains flagged.")
    back = []
    if args.watch:
        print()
        back = check_watched(args.as_of)
    if args.json:
        args.json.write_text(json.dumps({"as_of": str(args.as_of), "flagged": found, "back": back}, indent=2))
    if back:
        return 3
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
