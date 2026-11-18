"""Pick the best snow alerts from a fresh build and push them to GladeGrade phones.

Runs in the daily build after the site deploys. Compares the new scores with the
build that was live before it and finds four kinds of event:

  storm   - the site's own storm alert: a forecast storm that's big for that
            mountain (above its floor and top decile), 1-3 days out
  powder  - new snow in the last 24h at or above the mountain's 24h storm floor
  opens   - a mountain leaves "not open yet" and is skiable
  grade_a - a mountain's skiing grade reaches A- or better

Each phone chooses what it hears about in the app's alert settings (stored with
its subscription by the push Worker): mountains and regions it follows, which
kinds of event, and whether it also wants the best snow anywhere. Per phone, per
run, one notification at most:

  1. its best event at a mountain it follows (by mountain or region), else
  2. if "best anywhere" is on, the best event anywhere it hasn't had yet, so
     great snow still reaches people who don't follow that mountain.

Kinds it turned off never go out. At most MAX_PER_DAY a day per phone, and at
most BEST_PER_DAY_IF_FOLLOWING of those from "best anywhere" when it follows
something, so its own mountains always have room. Cooldowns are per phone: each
phone's recent alerts are kept with its subscription (history), so the same
event isn't sent to it twice. logs/push/sent.csv is the public summary of what
led a notification and to how many phones.

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
from ski.regions import descendant_leaves, region_for  # noqa: E402

SITE = "https://lpfitzgerald98-star.github.io/ski-conditions/"
LOG = ROOT / "logs" / "push" / "sent.csv"
A_GRADES = {"A-", "A", "A+"}
MAX_PER_DAY = 2
BEST_PER_DAY_IF_FOLLOWING = 1
ALSO_LISTED = 2          # runner-up events named in the one notification
KINDS = ("storm", "powder", "grade_a", "opens")
DEFAULT_PREFS = {"best": True, "kinds": list(KINDS), "mountains": [], "regions": []}
HISTORY_DAYS = 150       # the longest cooldown; older history is dropped
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
    region: str = ""
    tab: str = "recent"   # which Top picks tab the tap opens: recent, 24, 48 or 72


def _region(row: dict) -> str:
    if row.get("region"):
        return row["region"]
    m = MOUNTAINS.get(row["key"])
    return region_for(m) if m else ""


def _floor(key: str, hours: int) -> float:
    """The mountain's own storm floor for a window, nearest configured window."""
    m = MOUNTAINS.get(key, {})
    floors = {**STORM_THRESHOLDS["min_inches"], **(m.get("storm_floor_inches") or {})}
    near = min(floors, key=lambda h: abs(h - hours))
    return float(floors[near])


def _floor_24h(key: str) -> float:
    return _floor(key, 24)


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
            # Only what the card's own forecast counts as snow (temperature-checked,
            # ranked against this mountain's storms). No horizon that clears the
            # mountain's storm floor with a real percentile -> no storm alert.
            horizons = [h for h in (cards.get(key) or {}).get("forecast_horizons") or []
                        if (h.get("inches") or 0) >= _floor(key, h.get("horizon_hours", 24))
                        and h.get("percentile") is not None and h["percentile"] >= 90]
            best = max(horizons, key=lambda h: h["percentile"], default=None)
            if best:
                inches, hours, pct = best["inches"], best.get("horizon_hours", 24), best["percentile"]
                out.append(Event("storm", key, name, strength=1.0 + (pct - 90) / 10,
                                 tab=str(hours) if hours in (24, 48, 72) else "72",
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
    regions = {r["key"]: _region(r) for r in rows}
    for e in out:
        e.region = regions.get(e.key, "")
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


def rank(events: list[Event]) -> list[Event]:
    """Best first. Ties between kinds go to the one a skier acts on soonest."""
    return sorted(events, key=lambda e: (e.strength, KIND_RANK[e.kind]), reverse=True)


def _recent(e: Event, history: list[dict], today: date) -> bool:
    cutoff = today - timedelta(days=COOLDOWN_DAYS[e.kind] - 1)
    return any(h["kind"] == e.kind and h["key"] == e.key and date.fromisoformat(h["date"]) >= cutoff
               for h in history)


def pick(events: list[Event], log: list[dict], today: date) -> list[Event]:
    """Fresh events against one history, best first; empty once the day's quota is used."""
    if sum(1 for r in log if r["date"] == str(today)) >= MAX_PER_DAY:
        return []
    return rank([e for e in events if not _recent(e, log, today)])


def prefs_of(sub: dict) -> dict:
    """A phone's settings, with defaults for anything missing (older subscriptions
    have none, and get exactly the old behavior: the best snow anywhere)."""
    p = {**DEFAULT_PREFS, **(sub.get("prefs") or {})}
    p["kinds"] = [k for k in p["kinds"] if k in KINDS]
    return p


def follows(e: Event, prefs: dict) -> bool:
    if e.key in prefs["mountains"]:
        return True
    leaves = set()
    for r in prefs["regions"]:
        leaves |= descendant_leaves(r) or {r}
    return bool(e.region) and e.region in leaves


@dataclass
class Choice:
    lead: Event
    others: list[Event]
    why: str             # "follow" or "best"


def choose(ranked: list[Event], prefs: dict, history: list[dict], today: date) -> Choice | None:
    """The one notification for one phone this run, or None.

    `ranked` is every event this run, best first. The phone's own mountains come
    first; failing those, "best anywhere" sends the top event it hasn't had."""
    today_s = str(today)
    sent_today = [h for h in history if h["date"] == today_s]
    if len(sent_today) >= MAX_PER_DAY:
        return None
    usable = [e for e in ranked if e.kind in prefs["kinds"] and not _recent(e, history, today)]
    if not usable:
        return None
    mine = [e for e in usable if follows(e, prefs)]
    if mine:
        rest = mine[1:] + [e for e in usable if e not in mine]
        return Choice(mine[0], rest[:ALSO_LISTED], "follow")
    if not prefs["best"]:
        return None
    following = bool(prefs["mountains"] or prefs["regions"])
    if following and sum(1 for h in sent_today if h.get("why") == "best") >= BEST_PER_DAY_IF_FOLLOWING:
        return None
    return Choice(usable[0], usable[1:1 + ALSO_LISTED], "best")


def message(ranked: list[Event], note: str = "") -> dict:
    """One notification: the lead event, naming the next couple."""
    top = ranked[0]
    body = top.body
    others = [e.name.split(",")[0] for e in ranked[1:1 + ALSO_LISTED]]
    if others:
        body += f" Also worth a look: {', '.join(others)}."
    if note:
        body += f" {note}"
    return {"title": top.title, "body": body, "url": f"{SITE}?top={top.tab}&hl={top.key}", "tag": f"{top.kind}-{top.key}"}


def trim_history(history: list[dict], today: date) -> list[dict]:
    cutoff = str(today - timedelta(days=HISTORY_DAYS))
    return [h for h in history if h.get("date", "") >= cutoff]


def send(ranked: list[Event], api: str, token: str, vapid_key: str, today: date) -> list[dict]:
    """Send each phone its own notification; record it in that phone's history and
    drop phones whose subscription is gone. Returns sent.csv rows (one per lead event)."""
    import requests
    from pywebpush import WebPushException, webpush

    auth = {"Authorization": f"Bearer {token}"}
    subs = requests.get(f"{api}/subscriptions", headers=auth, timeout=30)
    subs.raise_for_status()
    subs = subs.json()
    print(f"{len(subs)} phone(s) subscribed")
    # A Worker not yet redeployed with settings support returns no history and
    # can't store it: fall back to the shared sent log, so cooldowns still hold.
    shared = [{**r, "why": "best"} for r in read_log()]
    leads: dict[tuple[str, str], int] = {}
    skipped = gone = failed = 0
    for s in subs:
        prefs = prefs_of(s)
        stored = "history" in s
        history = (s.get("history") or []) if stored else shared
        c = choose(ranked, prefs, history, today)
        if c is None:
            skipped += 1
            continue
        following = bool(prefs["mountains"] or prefs["regions"])
        payload = message([c.lead, *c.others],
                          "Best snow anywhere today." if c.why == "best" and following else "")
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, json.dumps(payload),
                    vapid_private_key=vapid_key,
                    # The push services' contact for this sender: an email or a
                    # bare https origin (py_vapid rejects paths).
                    vapid_claims={"sub": "https://lpfitzgerald98-star.github.io"},
                    timeout=30)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):     # unsubscribed or expired: forget it
                requests.delete(f"{api}/subscriptions/{s['id']}", headers=auth, timeout=30)
                gone += 1
            else:
                print(f"push to {s['id'][:8]} failed: {e}")
                failed += 1
            continue
        leads[(c.lead.kind, c.lead.key)] = leads.get((c.lead.kind, c.lead.key), 0) + 1
        if not stored:
            continue
        history = trim_history(history, today) + [
            {"date": str(today), "kind": c.lead.kind, "key": c.lead.key, "why": c.why}]
        r = requests.put(f"{api}/subscriptions/{s['id']}/history", headers=auth,
                         json={"history": history}, timeout=30)
        if not r.ok:
            print(f"couldn't save history for {s['id'][:8]}: {r.status_code}")
    print(f"sent to {sum(leads.values())}, nothing new for {skipped}, "
          f"expired and removed {gone}, failed {failed}")
    return [{"date": str(today), "kind": k, "key": key, "sent_to": n} for (k, key), n in leads.items()]


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

    ranked = rank(find_events(rows, previous, cards))
    if not ranked:
        print("no alert-worthy events")
        return 0
    preview = choose(ranked, dict(DEFAULT_PREFS), [], args.today)
    if preview:
        msg = message([preview.lead, *preview.others])
        print(f"best anywhere: {msg['title']} -- {msg['body']}")

    api, token, key = (os.environ.get(v) for v in ("PUSH_API", "PUSH_ADMIN_TOKEN", "VAPID_PRIVATE_KEY"))
    if args.dry_run or not (api and token and key):
        print("not sent (dry run, or push isn't configured yet)")
        return 0
    sent = send(ranked, api.rstrip("/"), token, key, args.today)
    if sent:
        append_log(sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
