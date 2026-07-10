"""Build the STATIC snapshot the GitHub-Pages frontend reads.

The site is hybrid: with no backend it runs off flat JSON produced here; point it
at a live API and it upgrades to SSE instead (see web/js/config.js). This script
is what the daily GitHub Action runs to refresh the flat-JSON half.

It reuses the real scoring code -- `card.scorecard`, `service.rank_within_regions`,
the `GRADE_COLORS` table -- so the static files carry exactly the shapes the live
`/grades`, `/scores` and `/score/{key}` endpoints would. Nothing is re-derived
here; if the grading changes, this output changes with it.

Output (under web/data/, git-ignored -- regenerated every run):
    grades.json              {colors, na_color}          == GET /grades
    meta.json                {as_of, generated_at, profiles, default_profile,
                              regions, roster_size, ok, failed}
    scores.<profile>.json    [roster row, ...] ranked    == GET /scores per profile
    cards/<key>.json         full scorecard              == GET /score/{key}

Run (from the project dir):
    python scripts/build_snapshot.py                 # ingest fresh, then score live
    python scripts/build_snapshot.py --no-ingest     # score off the current DB
    python scripts/build_snapshot.py --no-network    # skip forecasts (fast, for tests)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

# Allow `python scripts/build_snapshot.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_PROFILE, MOUNTAINS  # noqa: E402
from ski import pipeline  # noqa: E402
from ski.card import scorecard  # noqa: E402
from ski.service import (  # noqa: E402
    GRADE_COLORS,
    NA_COLOR,
    mountain_summary,
    rank_within_regions,
)

WEB_DATA = Path(__file__).resolve().parent.parent / "web" / "data"

# The order the profile selector shows them in; only those actually present in the
# scored cards are emitted. `dynamic` exists only for mountains with a season
# window, so it may be absent for a few.
PROFILE_ORDER = ["dynamic", "weekend", "month", "season"]


def _row_from_card(key: str, card: dict, profile: str) -> dict:
    """One roster row for `key` under `profile`, from an already-scored card.

    Mirrors service.score_mountain's row exactly, but reads the overall for a
    chosen profile out of the card we already computed -- so all four profile
    rosters come from ONE scoring pass instead of four.
    """
    row = mountain_summary(key)
    overall = card["overall"].get(profile) or {}
    season = card["grades"]["season"] or {}
    base = card["grades"]["base"] or {}
    fc = card["forecast"] or {}
    row.update(
        score=overall.get("score"),
        grade=overall.get("grade", "N/A"),
        in_season=card.get("in_season"),
        cover_depth=card.get("cover_depth"),
        season_grade=season.get("grade"),
        base_grade=base.get("grade"),
        base_depth=card["conditions"]["base_depth"],
        fresh_7d=card["conditions"]["fresh_7d"],
        season_progress=card["season_progress"],
        incoming_inches=fc.get("inches"),
        alert=bool(fc.get("alert")),
        status="live",   # a snapshot row is settled, never "stale"
    )
    return row


def _null_row(key: str, error: str) -> dict:
    """A roster row for a mountain whose scoring failed -- a gray pin, not a gap."""
    row = mountain_summary(key)
    row.update(score=None, grade="N/A", in_season=None, cover_depth=None,
               season_grade=None, base_grade=None, base_depth=None, fresh_7d=None,
               season_progress=None, incoming_inches=None, alert=False,
               status="error", error=error)
    return row


def ingest_all(keys: list[str], pause: float = 0.5) -> None:
    """Refresh every station's raw history before scoring.

    Resilient by design: the Action runs against flaky upstreams (NRCS DNS,
    Open-Meteo 429s), and one dead source must degrade to a gray pin, never fail
    the whole build. Each mountain gets a couple of tries; the pause spaces out
    the rate-limited networks (Open-Meteo).
    """
    for i, key in enumerate(keys, 1):
        for attempt in (1, 2, 3):
            try:
                n = pipeline.ingest_mountain(key)
                print(f"[ingest {i:>2}/{len(keys)}] {key}: {n} rows")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    print(f"[ingest {i:>2}/{len(keys)}] {key}: FAILED ({exc})")
                else:
                    time.sleep(attempt * 1.5)
        time.sleep(pause)


def build(keys: list[str], as_of: date, use_network: bool) -> dict:
    """Score every mountain once, then fan the result into the static files."""
    cards_dir = WEB_DATA / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    cards: dict[str, dict] = {}
    ok, failed = [], []
    for i, key in enumerate(keys, 1):
        try:
            card = scorecard(key, as_of=as_of, use_network=use_network,
                             default_profile=DEFAULT_PROFILE)
            card["roster_size"] = len(MOUNTAINS)
            cards[key] = card
            (cards_dir / f"{key}.json").write_text(
                json.dumps(card, separators=(",", ":")), encoding="utf-8")
            ok.append(key)
            print(f"[score {i:>2}/{len(keys)}] {key}: "
                  f"{(card['overall'].get(DEFAULT_PROFILE) or {}).get('grade', '—')}")
        except Exception as exc:  # noqa: BLE001 -- one mountain, not the build
            failed.append(key)
            print(f"[score {i:>2}/{len(keys)}] {key}: FAILED ({exc})")

    profiles = [p for p in PROFILE_ORDER
                if any(p in cards[k]["overall"] for k in ok)]

    # One ranked roster file per profile, assembled from the single scoring pass.
    for prof in profiles:
        rows = []
        for key in keys:
            card = cards.get(key)
            rows.append(_row_from_card(key, card, prof) if card
                        else _null_row(key, "scoring failed"))
        rank_within_regions(rows)
        (WEB_DATA / f"scores.{prof}.json").write_text(
            json.dumps(rows, separators=(",", ":")), encoding="utf-8")

    (WEB_DATA / "grades.json").write_text(
        json.dumps({"colors": GRADE_COLORS, "na_color": NA_COLOR}), encoding="utf-8")

    regions = sorted({mountain_summary(k)["region"] for k in keys})
    meta = {
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "default_profile": DEFAULT_PROFILE if DEFAULT_PROFILE in profiles
        else (profiles[0] if profiles else DEFAULT_PROFILE),
        "profiles": profiles,
        "regions": regions,
        "roster_size": len(MOUNTAINS),
        "ok": len(ok),
        "failed": failed,
        "network": use_network,
    }
    (WEB_DATA / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the static Pages snapshot.")
    ap.add_argument("--no-ingest", action="store_true",
                    help="score off the current DB without refreshing stations")
    ap.add_argument("--no-network", action="store_true",
                    help="skip live forecasts/weather (fast; for local testing)")
    ap.add_argument("--as-of", default=None, help="score as of YYYY-MM-DD (default today)")
    args = ap.parse_args()

    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())
    keys = sorted(MOUNTAINS)

    if not args.no_ingest:
        print(f"== Ingesting {len(keys)} stations ==")
        ingest_all(keys)

    print(f"== Scoring {len(keys)} mountains (network={not args.no_network}) ==")
    meta = build(keys, as_of, use_network=not args.no_network)

    print(f"\nDone: {meta['ok']}/{len(keys)} scored, "
          f"{len(meta['failed'])} failed -> {WEB_DATA}")
    if meta["failed"]:
        print("  failed:", ", ".join(meta["failed"]))
    # A snapshot with a handful of dead stations is still worth publishing; only a
    # near-total failure (bad deploy, no deps) should fail the Action.
    return 1 if meta["ok"] < len(keys) * 0.5 else 0


if __name__ == "__main__":
    raise SystemExit(main())
