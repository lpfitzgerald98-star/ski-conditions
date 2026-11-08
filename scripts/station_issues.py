"""Turn station_health.py's findings into GitHub issues, so problems reach the owner.

Two issues, each kept to at most one open at a time:

  "Weather stations need attention" -- mountains whose station stopped reporting.
      Opened when the first one appears (GitHub notifies the owner). While open, its
      text is refreshed every run without a notification; a comment (which does
      notify) is added only when the set of mountains changes. Closed with a comment
      once every station reports again.
  "A watched weather station is reporting again" -- a station a mountain was moved
      off came back. Opened once; the owner closes it after deciding.

Run in the daily build after station_health.py --json:
  python scripts/station_issues.py health.json
Needs the GitHub CLI (`gh`) with a token that can write issues.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

STATIONS_TITLE = "Weather stations need attention"
BACK_TITLE = "A watched weather station is reporting again"
KEYS_MARKER = re.compile(r"<!-- keys: (.*?) -->")

Gh = Callable[[list[str]], str]


def run_gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def find_open(gh: Gh, title: str) -> dict | None:
    """The open issue with exactly this title, as {"number", "body"}, or None."""
    out = gh(["issue", "list", "--state", "open", "--search", f'"{title}" in:title',
              "--json", "number,title,body"])
    for issue in json.loads(out or "[]"):
        if issue["title"] == title:
            return issue
    return None


def stations_body(report: dict) -> str:
    rows = "\n".join(f"| {f['name']} | {f['station']} ({f['source']}) | {f['reason']} |"
                     for f in report["flagged"])
    keys = ",".join(sorted(f["key"] for f in report["flagged"]))
    return (f"These mountains' weather stations have stopped sending snow readings "
            f"(checked {report['as_of']}):\n\n"
            "| Mountain | Station | Problem |\n|---|---|---|\n"
            f"{rows}\n\n"
            "Until a station reports again or the mountain moves to another one, its "
            "numbers on the site are missing or stale. The `station-scout` agent can find "
            "a replacement. This issue updates with each build and closes itself once "
            f"every station reports again.\n\n<!-- keys: {keys} -->")


def back_body(report: dict) -> str:
    rows = "\n".join(f"- **{b['name']}**: old station {b['old_station']} ({b['old_id']}) sent snow "
                     f"readings again, latest {b['latest']}. It currently uses {b['current_station']}."
                     for b in report["back"])
    return (f"{rows}\n\nDecide whether to move the mountain back (and drop its "
            "`watch_station`) or keep the replacement. Close this issue once decided.")


def sync(report: dict, gh: Gh = run_gh) -> list[str]:
    """Bring both issues in line with the report. Returns what it did, for the log."""
    done = []
    issue = find_open(gh, STATIONS_TITLE)
    keys = sorted(f["key"] for f in report["flagged"])
    if keys:
        body = stations_body(report)
        if issue is None:
            gh(["issue", "create", "--title", STATIONS_TITLE, "--body", body])
            done.append(f"opened '{STATIONS_TITLE}' for {', '.join(keys)}")
        else:
            m = KEYS_MARKER.search(issue.get("body") or "")
            before = sorted(filter(None, m.group(1).split(","))) if m else []
            gh(["issue", "edit", str(issue["number"]), "--body", body])
            if keys != before:
                added, cleared = sorted(set(keys) - set(before)), sorted(set(before) - set(keys))
                note = "; ".join(filter(None, [
                    f"newly silent: {', '.join(added)}" if added else "",
                    f"reporting again: {', '.join(cleared)}" if cleared else ""]))
                gh(["issue", "comment", str(issue["number"]), "--body", f"Changed: {note}."])
                done.append(f"updated #{issue['number']} ({note})")
            else:
                done.append(f"refreshed #{issue['number']} (no change)")
    elif issue is not None:
        gh(["issue", "close", str(issue["number"]), "--comment",
            f"Every station is reporting again as of {report['as_of']}."])
        done.append(f"closed #{issue['number']}")

    if report["back"] and find_open(gh, BACK_TITLE) is None:
        gh(["issue", "create", "--title", BACK_TITLE, "--body", back_body(report)])
        done.append(f"opened '{BACK_TITLE}'")
    return done or ["nothing to do"]


if __name__ == "__main__":
    for line in sync(json.loads(Path(sys.argv[1]).read_text())):
        print(line)
