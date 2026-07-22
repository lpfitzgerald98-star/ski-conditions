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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Allow `python scripts/build_snapshot.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (DEFAULT_PROFILE, GRADE_THRESHOLDS, MOUNTAINS,  # noqa: E402
                    TRIP_LEAD_DECAY, TRIP_WINDOW_DAYS)
from ski import commentary, pipeline, trip, trip_commentary  # noqa: E402
from ski.card import scorecard  # noqa: E402
from ski.db import read_observations  # noqa: E402
from ski.regions import region_for, region_tree  # noqa: E402
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
    ski = card.get("skiability") or {}
    season = card["grades"]["season"] or {}
    base = card["grades"]["base"] or {}
    fc = card["forecast"] or {}
    ci = card.get("comparable_inputs") or {}
    row.update(
        # HEADLINE = absolute skiability (pins, leaderboard sort, region rank all
        # reflect the honest "how good is the skiing right now"). The self-relative
        # `overall` is kept alongside as historical context, not the pin grade.
        score=ski.get("score"),
        grade=ski.get("grade", "N/A"),
        overall_score=overall.get("score"),
        overall_grade=overall.get("grade", "N/A"),
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
        # Flattened for ski.comparable.score_population (global/regional score),
        # same fields service.score_mountain sets for the live API path -- this
        # was the one row-builder that never got them (bug: global_score/
        # regional_score were always null on the deployed static site).
        abs_base_in=ci.get("base_in"),
        abs_fresh_in=ci.get("fresh_in"),
        abs_season_in=ci.get("season_in"),
        abs_forecast_in=ci.get("forecast_in"),
        abs_quality=ci.get("quality"),
        abs_vertical_ft=ci.get("vertical_drop_ft"),
        abs_acres=ci.get("skiable_acres"),
        abs_pct_advanced_expert=ci.get("pct_advanced_expert"),
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


def ingest_all(keys: list[str], pause: float = 2.0, full: bool = False) -> None:
    """Refresh every station's raw history before scoring.

    Incremental by default (see pipeline.ingest_mountain): once the DB is warm,
    each station fetches only its recent tail, so the whole roster ingests in a
    minute or two instead of re-pulling decades every run. `full=True` forces a
    full period-of-record pull for every station (first build, or a rebuild).

    Resilient by design: the Action runs against flaky upstreams (NRCS DNS,
    Open-Meteo 429s), and one dead source must degrade to a gray pin, never fail
    the whole build. Each mountain gets a couple of tries; the pause spaces out
    the rate-limited networks (Open-Meteo). The Action's runner IP is shared
    across many concurrent GitHub-hosted jobs, so it draws 429s harder than a
    local run does -- pacing here must be more patient than 0.5s ever was.
    """
    for i, key in enumerate(keys, 1):
        for attempt in (1, 2, 3):
            try:
                n = pipeline.ingest_mountain(key, full=full)
                print(f"[ingest {i:>2}/{len(keys)}] {key}: {n} rows")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    print(f"[ingest {i:>2}/{len(keys)}] {key}: FAILED ({exc})")
                else:
                    time.sleep(attempt * 5)
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
            # One AI sentence explaining the grade -- cached per (mountain, day)
            # in SQLite, so rebuilding the snapshot never re-pays the API; skipped
            # (null) off-season and when no Anthropic credentials are configured.
            card["commentary"] = commentary.get_or_generate(key, as_of, card)
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
        json.dumps({"colors": GRADE_COLORS, "na_color": NA_COLOR,
                    "thresholds": GRADE_THRESHOLDS}), encoding="utf-8")

    regions = sorted({mountain_summary(k)["region"] for k in keys})
    meta = {
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "default_profile": DEFAULT_PROFILE if DEFAULT_PROFILE in profiles
        else (profiles[0] if profiles else DEFAULT_PROFILE),
        "profiles": profiles,
        "regions": regions,               # flat leaf list, kept for compat
        "region_tree": region_tree(),     # the hierarchy the picker renders
        "roster_size": len(MOUNTAINS),
        "ok": len(ok),
        "failed": failed,
        "network": use_network,
        # Trip Predictor bounds/params the frontend needs to open the future date
        # picker and run the lead-time blend (see web/js/main.js, ski.trip).
        "trip": {"half_life_days": TRIP_LEAD_DECAY["half_life_days"],
                 "window_days": TRIP_WINDOW_DAYS,
                 "max_lead_days": TRIP_LEAD_DECAY["max_lead_days"]},
    }
    (WEB_DATA / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def build_history(keys: list[str], start: date, end: date,
                  profile: str = DEFAULT_PROFILE, forward_window_days: int = 3) -> dict:
    """Per-date retrospective roster files for browsing PAST scores.

    For each date in [start, end] we score the whole roster `as_of` that day with
    `retro=True`: history-to-date grades plus the snow that ACTUALLY fell in the
    forward window (the real "was it a good weekend"). Written one file per date so
    the frontend lazy-loads only the day you're viewing -- page load is unaffected
    no matter how much history accumulates.

    Immutable + incremental: a settled date's score never changes, so existing
    files are skipped. Only dates whose forward window has fully elapsed are built
    (up to today - forward_window_days), so the retrospective snow is complete.
    """
    hist_dir = WEB_DATA / "hist"
    hist_dir.mkdir(parents=True, exist_ok=True)
    cutoff = date.today() - timedelta(days=forward_window_days)
    end = min(end, cutoff)

    built = 0
    d = start
    while d <= end:
        f = hist_dir / f"{d.isoformat()}.json"
        if not f.exists():
            rows = []
            for key in keys:
                try:
                    card = scorecard(key, as_of=d, use_network=False, retro=True,
                                     default_profile=profile)
                    rows.append(_row_from_card(key, card, profile))
                except Exception as exc:  # noqa: BLE001
                    rows.append(_null_row(key, str(exc)))
            rank_within_regions(rows)
            f.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
            built += 1
            if built % 10 == 0:
                print(f"[history] built {built} new dates (…{d.isoformat()})")
        d += timedelta(days=1)

    # Manifest: the contiguous list of available dates for the date picker's bounds.
    dates = sorted(p.stem for p in hist_dir.glob("*.json") if p.stem != "index")
    (hist_dir / "index.json").write_text(json.dumps({
        "dates": dates,
        "min": dates[0] if dates else None,
        "max": dates[-1] if dates else None,
        "profile": profile,
        "forward_window_days": forward_window_days,
    }), encoding="utf-8")
    return {"built": built, "total": len(dates)}


def _build_climatology(keys: list[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Each requested mountain's climatology (ski.trip.climatology, a few
    group-bys over its whole station record), cached per station id so
    resorts sharing a station (e.g. Alta/Snowbird) share the computation.

    Returns (clim_by_station, meta_by_key) -- meta_by_key carries what
    ski.trip.roster_baseline_rows and ski.trip_commentary both need per
    mountain (station id, water-year start month, region) without re-reading
    config.MOUNTAINS at every call site. Shared by build_trip_baseline (the
    RANKED score) and build_trip_patterns (the PROSE) -- one pass over the DB
    feeds both, since they read the exact same underlying trajectory."""
    clim_by_station: dict[str, dict] = {}
    meta_by_key: dict[str, dict] = {}
    for key in keys:
        m = MOUNTAINS[key]
        station = pipeline.mountain_station(m)
        if station not in clim_by_station:
            try:
                obs = read_observations(pipeline.DB_PATH, station)
                # Regional literature priors + per-network trust shrink the noisy
                # measured density/preservation (see ski.trip / config priors).
                src = m.get("data_source", pipeline.DEFAULT_SOURCE)
                d_prior, d_trust = trip.density_priors(region_for(m), src)
                p_prior, p_trust = trip.preservation_priors(region_for(m), src)
                clim_by_station[station] = trip.climatology(
                    obs, pipeline.mountain_wy_start(m),
                    pipeline.mountain_season_start(m), pipeline.mountain_metric(m),
                    density_prior=d_prior, density_trust=d_trust,
                    preservation_prior=p_prior, preservation_trust=p_trust,
                    # NOTE: the climatology cache is per STATION; resorts sharing
                    # one (Alta/Snowbird, Keystone/A-Basin...) get the factor of
                    # whichever mountain reaches the station first. Deterministic
                    # (MOUNTAINS order), and sharing resorts are adjacent with
                    # near-identical published normals, so the drift is a few %.
                    siting_factor=pipeline.siting_factor(
                        key, obs, pipeline.mountain_wy_start(m),
                        pipeline.mountain_metric(m)))
            except Exception as exc:  # noqa: BLE001 -- one station, not the build
                print(f"[trip] {key}: climatology failed ({exc})")
                clim_by_station[station] = {}
        meta_by_key[key] = {"station": station,
                            "wy_start": pipeline.mountain_wy_start(m),
                            "region": region_for(m)}
    return clim_by_station, meta_by_key


def build_trip_baseline(keys: list[str], clim_by_station: dict[str, dict],
                        meta_by_key: dict[str, dict], ref_year: int = 2023) -> dict:
    """Precompute the Trip Predictor's HISTORICAL baseline for every calendar date.

    History is immutable, so a mountain's typical conditions for "March 14" never
    change -- this is a pure build-time artifact. For each calendar day of a
    reference non-leap year we resolve that day to each mountain's own
    day-of-water-year (hemisphere-aware) and rank the roster with the trip
    weights (ski.trip.score_baseline).

    Output web/data/trip/baseline.json, keyed by calendar MM-DD:
        {"generated_at", "half_life_days", "window_days",
         "dates": {"03-14": {key: [baseline_score|null, n_years], ...}, ...}}

    The frontend loads this once (lazily, on the first future date pick) and blends
    each mountain's baseline with TODAY'S global score using the lead-time decay --
    all the ranking stays here in Python; the client only does the 1-line blend."""
    trip_dir = WEB_DATA / "trip"
    trip_dir.mkdir(parents=True, exist_ok=True)

    dates: dict[str, dict] = {}
    d = date(ref_year, 1, 1)
    end = date(ref_year, 12, 31)
    while d <= end:
        rows = trip.roster_baseline_rows(d, keys, meta_by_key, clim_by_station)
        dates[d.strftime("%m-%d")] = {
            r["key"]: [r["baseline_score"], r["n_years"]] for r in rows}
        d += timedelta(days=1)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "half_life_days": TRIP_LEAD_DECAY["half_life_days"],
        "window_days": TRIP_WINDOW_DAYS,
        "dates": dates,
    }
    (trip_dir / "baseline.json").write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return {"dates": len(dates), "mountains": len(keys)}


def build_trip_patterns(keys: list[str], clim_by_station: dict[str, dict],
                        meta_by_key: dict[str, dict], ref_year: int = 2023) -> dict:
    """Precompute the Trip Predictor's seasonal-pattern PROSE (Part 1 of the
    trip commentary -- see ski.trip_commentary) for every mountain x calendar
    date. Doesn't depend on "today" at all (only climatology + season_window,
    both stable data), so unlike baseline.json this never needs same-day
    freshness -- it's rebuilt whenever the snapshot rebuilds, same as
    everything else, but nothing would go stale if it weren't.

    Written ONE FILE PER MOUNTAIN (web/data/trip/patterns/<key>.json, MM-DD ->
    text), not folded into baseline.json: baseline.json drives the WHOLE
    leaderboard on every date pick and stays deliberately tiny (~1KB/mountain
    for a [score, n_years] pair); prose for all 366 days of all 113 mountains
    would multiply that by roughly 200x. Splitting per mountain mirrors the
    two sharding precedents already in this codebase -- per-mountain live
    cards (web/data/cards/<key>.json) and per-date history (web/data/hist/
    <date>.json) -- and the frontend fetches a mountain's pattern file lazily,
    only when that mountain's trip card actually opens (see web/js/api.js
    loadTripPattern, web/js/card.js renderTripCard)."""
    patterns_dir = WEB_DATA / "trip" / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)

    for key in keys:
        mk = meta_by_key[key]
        clim = clim_by_station.get(mk["station"], {})
        name = MOUNTAINS[key]["name"]
        season_window = MOUNTAINS[key].get("season_window")
        out: dict[str, str] = {}
        d = date(ref_year, 1, 1)
        end = date(ref_year, 12, 31)
        while d <= end:
            out[d.strftime("%m-%d")] = trip_commentary.seasonal_pattern_text(
                key, name, mk["wy_start"], season_window, clim, d)
            d += timedelta(days=1)
        (patterns_dir / f"{key}.json").write_text(
            json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return {"mountains": len(keys)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the static Pages snapshot.")
    ap.add_argument("--no-ingest", action="store_true",
                    help="score off the current DB without refreshing stations")
    ap.add_argument("--full-ingest", action="store_true",
                    help="force a full period-of-record pull for every station "
                         "(default is incremental: only each station's recent tail)")
    ap.add_argument("--no-network", action="store_true",
                    help="skip live forecasts/weather (fast; for local testing)")
    ap.add_argument("--as-of", default=None, help="score as of YYYY-MM-DD (default today)")
    ap.add_argument("--history", action="store_true",
                    help="also build the retrospective per-date history files")
    ap.add_argument("--hist-start", default=None, help="history range start YYYY-MM-DD")
    ap.add_argument("--hist-end", default=None, help="history range end YYYY-MM-DD (default today)")
    ap.add_argument("--history-only", action="store_true",
                    help="build ONLY history (skip today's snapshot)")
    ap.add_argument("--no-trip", action="store_true",
                    help="skip the Trip Predictor historical-baseline file")
    args = ap.parse_args()

    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())
    keys = sorted(MOUNTAINS)

    if not args.no_ingest:
        mode = "full" if args.full_ingest else "incremental"
        print(f"== Ingesting {len(keys)} stations ({mode}) ==")
        ingest_all(keys, full=args.full_ingest)

    if not args.history_only:
        print(f"== Scoring {len(keys)} mountains (network={not args.no_network}) ==")
        meta = build(keys, as_of, use_network=not args.no_network)
        print(f"\nDone: {meta['ok']}/{len(keys)} scored, "
              f"{len(meta['failed'])} failed -> {WEB_DATA}")
        if meta["failed"]:
            print("  failed:", ", ".join(meta["failed"]))
    else:
        meta = {"ok": len(keys), "failed": []}

    if not args.no_trip and not args.history_only:
        print(f"== Building Trip Predictor climatology ({len(keys)} mountains) ==")
        try:
            clim_by_station, meta_by_key = _build_climatology(keys)
            t = build_trip_baseline(keys, clim_by_station, meta_by_key)
            print(f"Trip baseline: {t['dates']} dates x {t['mountains']} mountains -> {WEB_DATA / 'trip'}")
            p = build_trip_patterns(keys, clim_by_station, meta_by_key)
            print(f"Trip patterns: {p['mountains']} mountains -> {WEB_DATA / 'trip' / 'patterns'}")
        except Exception as exc:  # noqa: BLE001 -- a baseline failure shouldn't sink the snapshot
            print(f"Trip baseline/patterns FAILED: {exc}")

    if args.history or args.history_only:
        # Default range: the current + previous water years back to Nov 1, which
        # covers a full NH winter. Incremental, so the big first pass happens once.
        start = (datetime.strptime(args.hist_start, "%Y-%m-%d").date()
                 if args.hist_start else date(date.today().year - 1, 11, 1))
        end = (datetime.strptime(args.hist_end, "%Y-%m-%d").date()
               if args.hist_end else date.today())
        print(f"== Building history {start} -> {end} ==")
        h = build_history(keys, start, end)
        print(f"History: +{h['built']} new dates, {h['total']} total")
    # A snapshot with a handful of dead stations is still worth publishing; only a
    # near-total failure (bad deploy, no deps) should fail the Action.
    return 1 if meta["ok"] < len(keys) * 0.5 else 0


if __name__ == "__main__":
    raise SystemExit(main())
