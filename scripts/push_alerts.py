"""Pick the best snow alerts from a fresh build and push them to GladeGrade phones.

Runs in the daily build after the site deploys. Compares the new scores with the
build that was live before it and finds four kinds of event:

  storm   - the site's own storm alert: a forecast storm that's big for that
            mountain (above its floor and top decile), 1-3 days out
  powder  - new snow in the last 24h at or above the mountain's 24h storm floor
  opens   - a mountain leaves "not open yet" and is skiable
  grade_a - a mountain's skiing grade reaches A- or better

"Best anywhere": events are ranked and only the top one goes out per run (the
next few ride along in its text), at most MAX_PER_DAY a day. logs/push/sent.csv
remembers what went out so the same event isn't sent again within its cooldown.

Env: PUSH_API, PUSH_ADMIN_TOKEN, VAPID_PRIVATE_KEY. With any unset, it only prints
what it would send. --dry-run does the same on purpose.

Run:  python scripts/push_alerts.py --previous prev_scores.json [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import MOUNTAINS, STORM_THRESHOLDS  # noqa: E402

SITE = "https://lpfitzgerald98-star.github.io/ski-conditions/"
LOG = ROOT / "logs" / "push" / "sent.csv"
A_GRADES = {"A-", "A", "A+"}
MAX_PER_DAY = 2
ALSO_LISTED = 2          # runner-up events named in the one notification
COOLDOWN_DAYS = {"storm": 3, "powder": 1, "opens": 150, "grade_a": 5}
# Ties between kinds go to the one a skier acts on soonest.
KIND_RANK = {"powder": 3, "storm": 2, "grade_a": 1, "opens": 0}


@dataclass
class Event:
    kind: str
    key: str
    name: str
    strength: float      # how notable, comparable across kinds (1.0 = just qualifies)
    title: str
    body: str


def _floor_24h(key: str) -> float:
    m = MOUNTAINS.get(key, {})
    return float((m.get("storm_floor_inches") or {}).get(24, STORM_THRESHOLDS["min_inches"][24]))


def _skiable(row: dict) -> bool:
    return (row.get("in_season") is True and not row.get("season_over_since")
            and not row.get("preseason_grade"))


def find_events(rows: list[dict], previous: list[dict] | None, cards: dict[str, dict]) -> list[Event]:
    """Every alert-worthy event in this build. Transitions (opens, grade_a) need the
    previous build; without one only storm and powder can fire."""
    prev = {r["key"]: r for r in previous} if previous else None
    out = []
    for r in rows:
        key, name = r["key"], r.get("name", r["key"])
        if r.get("season_over_since"):
            continue
        if r.get("alert"):
            horizons = (cards.get(key) or {}).get("forecast_horizons") or []
            best = max(horizons, key=lambda h: h.get("inches") or 0, default=None)
            inches = (best or {}).get("inches") or r.get("incoming_inches") or 0
            hours = (best or {}).get("horizon_hours", 24)
            pct = (best or {}).get("percentile") or 90
            out.append(Event("storm", key, name, strength=1.0 + (pct - 90) / 10,
                             title=f"Big storm coming: {name}",
                             body=f'{inches:.0f}" forecast in the next {hours} hours, top {max(1, round(100 - pct))}% for {name.split(",")[0]}.'))
        fresh = r.get("fresh_24h")
        if _skiable(r) and fresh is not None and fresh >= _floor_24h(key):
            out.append(Event("powder", key, name, strength=fresh / _floor_24h(key),
                             title=f"Powder day: {name}",
                             body=f'{fresh:.0f}" of new snow in the last 24 hours. Grade {r.get("grade", "?")}.'))
        if prev is None or key not in prev:
            continue
        p = prev[key]
        if _skiable(r) and p.get("preseason_grade") and not r.get("preseason_grade"):
            out.append(Event("opens", key, name, strength=1.0,
                             title=f"Season's open: {name}",
                             body=f'{name.split(",")[0]} is open for the season. Base {r.get("base_depth") or 0:.0f}", grade {r.get("grade", "?")}.'))
        if _skiable(r) and r.get("grade") in A_GRADES and p.get("grade") not in A_GRADES:
            out.append(Event("grade_a", key, name, strength=1.0 + (r.get("score") or 0) / 100,
                             title=f"{name} is skiing at an {r['grade']}",
                             body=f'Base {r.get("base_depth") or 0:.0f}", {r.get("fresh_7d") or 0:.0f}" new this week.'))
    return out


def read_log(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_log(rows: list[dict], path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "kind", "key", "sent_to"])
        if new:
            w.writeheader()
        w.writerows(rows)


def pick(events: list[Event], log: list[dict], today: date) -> list[Event]:
    """Fresh events, best first. Empty when today's quota is used up."""
    if sum(1 for r in log if r["date"] == str(today)) >= MAX_PER_DAY:
        return []

    def recent(e: Event) -> bool:
        cutoff = today - timedelta(days=COOLDOWN_DAYS[e.kind] - 1)
        return any(r["kind"] == e.kind and r["key"] == e.key and date.fromisoformat(r["date"]) >= cutoff
                   for r in log)

    fresh = [e for e in events if not recent(e)]
    return sorted(fresh, key=lambda e: (e.strength, KIND_RANK[e.kind]), reverse=True)


def message(ranked: list[Event]) -> dict:
    """One notification: the top event, naming the next couple."""
    top = ranked[0]
    body = top.body
    others = [e.name.split(",")[0] for e in ranked[1:1 + ALSO_LISTED]]
    if others:
        body += f" Also worth a look: {', '.join(others)}."
    return {"title": top.title, "body": body, "url": f"{SITE}?m={top.key}", "tag": f"{top.kind}-{top.key}"}


def send(payload: dict, api: str, token: str, vapid_key: str) -> int:
    """Push `payload` to every stored subscription; drop the ones that are gone."""
    import requests
    from pywebpush import WebPushException, webpush

    auth = {"Authorization": f"Bearer {token}"}
    subs = requests.get(f"{api}/subscriptions", headers=auth, timeout=30).json()
    sent = 0
    for s in subs:
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, json.dumps(payload),
                    vapid_private_key=vapid_key,
                    # The push services' contact for this sender: an email or a
                    # bare https origin (py_vapid rejects paths).
                    vapid_claims={"sub": "https://lpfitzgerald98-star.github.io"},
                    timeout=30)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):     # unsubscribed or expired: forget it
                requests.delete(f"{api}/subscriptions/{s['id']}", headers=auth, timeout=30)
            else:
                print(f"push to {s['id']} failed: {e}")
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send the best snow alerts from this build.")
    ap.add_argument("--scores", type=Path, default=ROOT / "web/data/scores.dynamic.json")
    ap.add_argument("--cards", type=Path, default=ROOT / "web/data/cards")
    ap.add_argument("--previous", type=Path, help="the scores file that was live before this build")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = json.loads(args.scores.read_text())
    previous = None
    if args.previous and args.previous.exists():
        try:
            previous = json.loads(args.previous.read_text())
        except json.JSONDecodeError:
            print("previous scores unreadable; storm and powder alerts only")
    cards = {}
    for r in rows:
        if r.get("alert"):
            path = args.cards / f"{r['key']}.json"
            if path.exists():
                cards[r["key"]] = json.loads(path.read_text())

    ranked = pick(find_events(rows, previous, cards), read_log(), args.today)
    if not ranked:
        print("no new alerts")
        return 0
    payload = message(ranked)
    print(f"alert: {payload['title']} -- {payload['body']}")

    api, token, key = (os.environ.get(v) for v in ("PUSH_API", "PUSH_ADMIN_TOKEN", "VAPID_PRIVATE_KEY"))
    if args.dry_run or not (api and token and key):
        print("not sent (dry run, or push isn't configured yet)")
        return 0
    n = send(payload, api.rstrip("/"), token, key)
    append_log([{"date": str(args.today), "kind": ranked[0].kind, "key": ranked[0].key, "sent_to": n}])
    print(f"sent to {n} device(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
