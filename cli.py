"""Command-line entry point for the ski-conditions tracker.

    python cli.py ingest --mountain alta
    python cli.py report --mountain alta
    python cli.py report --mountain alta --as-of 2025-02-01   # backtest a date
    python cli.py report --mountain alta --no-nws             # grade only, no network

Phase 1 scope: ingest live SNOTEL history, then print the season-to-date
percentile + letter grade for Alta, plus the NWS forecast and any active alerts.
Run `ingest` once (it stores the full period of record); re-run periodically to
pick up new days.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from config import DB_PATH, DEFAULT_PROFILE, MOUNTAINS, SCORE_PROFILES
from ski import analysis, pipeline
from ski.card import scorecard
from ski.db import read_observations
from ski.grading import detect_storm_events
from ski.score import overall_score
from ski.watercalendar import water_year


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def cmd_ingest(args: argparse.Namespace) -> int:
    m = pipeline.get_mountain(args.mountain)
    src = m.get("data_source", "snotel").upper()
    sname = m.get("snotel_name") or m.get("station_name", "")
    print(f"Ingesting {src} history for {m['name']} "
          f"(station {pipeline.mountain_station(m)}, {sname})...")
    try:
        n = pipeline.ingest_mountain(args.mountain, db_path=args.db)
    except Exception as exc:  # noqa: BLE001 -- surface the real failure to the user
        print(f"  ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"  stored {n} daily observations -> {args.db}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    m = pipeline.get_mountain(args.mountain)
    as_of = _parse_date(args.as_of) if args.as_of else date.today()

    print("=" * 68)
    src = m.get("data_source", "snotel").upper()
    sname = m.get("snotel_name") or m.get("station_name", "")
    print(f"  {m['name']}   ({src} {pipeline.mountain_station(m)} '{sname}')")
    if not m.get("verified", False):
        print("  NOTE: station/grid pairing not yet verified -- confirm before trusting.")
    print("=" * 68)

    # --- Season grade (the core Phase-1 output) ---
    grade = pipeline.grade_mountain(args.mountain, db_path=args.db, as_of=as_of)
    print("\nSEASON GRADE")
    print("  " + grade.summary())
    if grade.n_years == 0:
        print("  (no historical years available -- have you run `ingest`?)")
    else:
        if grade.current_coverage is not None and grade.current_coverage < 0.5:
            print(f"  (current season coverage {grade.current_coverage:.0%} -- "
                  "early-season value, will firm up)")
        lo = min(v for _, v in grade.historical)
        hi = max(v for _, v in grade.historical)
        print(f"  historical range at this day-of-season: {lo:.1f}-{hi:.1f} in "
              f"across {grade.n_years} yrs")

    # --- Rolling 30-day "hot month" grade ---
    month = pipeline.grade_mountain_month(args.mountain, db_path=args.db, as_of=as_of)
    if month.n_years > 0:
        print("\nMONTH GRADE (rolling)")
        print("  " + month.summary())

    # --- Base grade (current snowpack vs same date in history) ---
    base = pipeline.grade_mountain_base(args.mountain, db_path=args.db, as_of=as_of)
    if base.n_years > 0:
        print("\nBASE GRADE")
        print("  " + base.summary())

    # --- Storms this season (adaptive alert floor for how the season is going) ---
    obs = read_observations(args.db, pipeline.mountain_station(m))
    if not obs.empty:
        season_wy = water_year(as_of)
        print("\nSTORMS THIS SEASON (WY{})".format(season_wy))
        events = pipeline.this_season_storms(args.mountain, db_path=args.db, as_of=as_of, top_n=3)
        for e in events:
            pct = "n/a" if e.percentile is None else f"{e.percentile:.0f}th pct of storms"
            flag = "  <-- ALERT" if e.alert else ""
            print(f"  {e.window_hours}hr  {e.end_date}  {e.total_inches:4.0f}\"  "
                  f"grade {e.grade:<3} ({pct}){flag}")
        if not events:
            print("  no notable storms recorded yet")

    # --- NWS forecast + alerts (what's coming) ---
    if not args.no_nws and not pipeline.has_nws(m):
        print("\n(no NWS forecast: this mountain is outside the US -- history/base only)")
    if not args.no_nws and pipeline.has_nws(m):
        print("\nINCOMING SNOW (NWS forecast, graded vs storm history)")
        try:
            for sg in pipeline.forecast_incoming_storms(args.mountain, db_path=args.db):
                pct = "n/a" if sg.percentile is None else f"{sg.percentile:.0f}th pct of storms"
                flag = "  <-- ALERT" if sg.alert else ""
                print(f"  next {sg.window_hours}hr: {sg.total_inches:4.1f}\"  "
                      f"grade {sg.grade:<3} ({pct}){flag}")
        except Exception as exc:  # noqa: BLE001
            print(f"  forecast-snow fetch failed: {exc}", file=sys.stderr)

        print("\nFORECAST (NWS)")
        try:
            forecast, alerts = pipeline.fetch_nws(args.mountain)
            for p in forecast[:6]:
                temp = f"{p.temperature}{p.temperature_unit}" if p.temperature is not None else "--"
                print(f"  {p.name:<22} {temp:>5}  {p.short_forecast}")
            print("\nACTIVE ALERTS (NWS)")
            if not alerts:
                print("  none")
            for a in alerts:
                print(f"  [{a.severity}] {a.event}: {a.headline}")
        except Exception as exc:  # noqa: BLE001
            print(f"  NWS fetch failed: {exc}", file=sys.stderr)

    print()
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Backtest the curve against real history: per-year grades + calibration + storms."""
    m = pipeline.get_mountain(args.mountain)
    obs = read_observations(args.db, pipeline.mountain_station(m))
    if obs.empty:
        print("no data -- run `ingest` first", file=sys.stderr)
        return 1

    src = m.get("data_source", "snotel").upper()
    wy_start = pipeline.mountain_wy_start(m)
    print(f"CURVE BACKTEST -- {m['name']} ({src} {pipeline.mountain_station(m)})\n")
    graded = analysis.season_grades_by_year(
        obs, metric=pipeline.mountain_metric(m),
        season_start_dowy=pipeline.mountain_season_start(m),
        wy_start_month=wy_start)
    print(f"Season grade per year (leave-one-out, {graded.shape[0]} full seasons):")
    for r in graded.sort_values("percentile", ascending=False).itertuples(index=False):
        print(f"  WY{r.water_year}  {r.value:6.1f}  {r.percentile:5.1f}th  {r.grade}")

    dist = analysis.grade_distribution(graded)
    order = [g for _, g in analysis.GRADE_THRESHOLDS]
    print("\nGrade distribution: " + "  ".join(f"{g}:{dist.get(g, 0)}" for g in order))
    f_count = dist.get("F", 0)
    print(f"  F = {f_count} yrs ({100 * f_count / max(1, len(graded)):.0f}% of seasons)")

    print("\nCurve calibration (real value at each grade cutoff):")
    for min_p, grade, thresh in analysis.curve_calibration(graded):
        print(f"  >= {min_p:>3}th pct -> {grade:<3}  ~ {thresh:6.1f}")

    last = max(analysis.completed_water_years(obs, wy_start))
    print(f"\nBiggest storms of WY{last}:")
    for wh in (24, 72):
        evs = detect_storm_events(obs, wh, water_year_filter=last, top_n=3,
                                  wy_start_month=wy_start)
        for e in evs:
            flag = "  <-- ALERT" if e.alert else ""
            print(f"  {wh}hr  {e.end_date}  {e.total_inches:4.0f}\"  "
                  f"grade {e.grade:<3} ({e.percentile:.0f}th pct of storms){flag}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Print the overall mountain score (the card's brain) under each profile."""
    m = pipeline.get_mountain(args.mountain)
    as_of = _parse_date(args.as_of) if args.as_of else date.today()
    card = pipeline.mountain_scorecard(
        args.mountain, db_path=args.db, as_of=as_of, use_network=not args.no_nws)
    sub = card["subscores"]

    def fmt(v):
        return "  n/a" if v is None else f"{v:5.1f}"

    print("=" * 60)
    print(f"  {m['name']}  --  mountain score  ({as_of})")
    print("=" * 60)
    print("\nSUB-SCORES (0-100):")
    print(f"  season (whole winter)  {fmt(sub['season'])}   grade {card['season'].grade}")
    print(f"  in-season (last 30d)   {fmt(sub['in_season'])}   grade {card['month'].grade}")
    fc = "  (nothing incoming -- excluded from blend)" if sub["forecast"] is None else ""
    print(f"  forecast (incoming)    {fmt(sub['forecast'])}{fc}")
    wq = card["weather_quality"]
    wtxt = f"weather {wq:.0f}" if wq is not None else "weather n/a"
    print(f"  conditions (base+wx)   {fmt(sub['conditions'])}   base {card['base'].grade}, {wtxt}")

    sp = card["season_progress"]
    profiles = (["dynamic"] if sp is not None else []) + list(SCORE_PROFILES)
    if sp is not None:
        print(f"\nseason progress: {sp:.0%}  (0%=season start, 100%=season end)")
    cover = card["cover_factor"]
    if cover < 1.0:
        print(f"cover gate: x{cover:.2f}  (effective depth "
              f"{card['effective_depth']:.0f}\" caps the overall)")
    print("OVERALL by profile:")
    for prof in profiles:
        o = overall_score(sub, prof, season_progress=sp, cover=cover)
        star = "  <= selected" if prof == args.profile else ""
        val = "n/a" if o.value is None else f"{o.value:5.1f}"
        note = ""
        if prof == "dynamic" and o.weights_used:
            top = max(o.weights_used, key=o.weights_used.get)
            note = f"  (leaning {top})"
        print(f"  {prof:<8} {val}  grade {o.grade}{star}{note}")
    return 0


def cmd_forecast_report(args: argparse.Namespace) -> int:
    """Print the forecast-accuracy backtest: predicted vs actual per horizon."""
    df = pipeline.forecast_accuracy(args.mountain, db_path=args.db)
    m = pipeline.get_mountain(args.mountain)
    print(f"FORECAST ACCURACY -- {m['name']}\n")
    if df.empty:
        print("  no elapsed, logged predictions yet -- forecast_log fills in as the\n"
              "  live scorer runs and horizons age past their window (see\n"
              "  ski/forecast_log.py, pipeline.forecast_accuracy)")
        return 0
    print(f"  {'as of':<12} {'hrs':>4} {'predicted':>10} {'actual':>8} {'error':>8}  pct")
    for r in df.sort_values(["as_of", "horizon_hours"]).itertuples(index=False):
        pct = "n/a" if r.predicted_percentile is None else f"{r.predicted_percentile:.0f}th"
        err = "n/a" if r.error_inches is None else f"{r.error_inches:+.1f}\""
        print(f"  {r.as_of:<12} {r.horizon_hours:>4} {r.predicted_inches:>9.1f}\" "
              f"{r.actual_inches:>7.1f}\" {err:>8}  {pct}")
    errs = df["error_inches"].dropna()
    if not errs.empty:
        print(f"\n  mean error: {errs.mean():+.2f}\"   mean |error|: {errs.abs().mean():.2f}\"  "
              f"(n={len(errs)})")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    """Emit the mountain scorecard as JSON (the frontend data contract)."""
    as_of = _parse_date(args.as_of) if args.as_of else date.today()
    data = scorecard(args.mountain, db_path=args.db, as_of=as_of,
                     use_network=not args.no_nws)
    print(json.dumps(data, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GladeGrade (Phase 1).")
    parser.add_argument("--db", default=DB_PATH, help=f"SQLite path (default: {DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mountain", default="alta", choices=sorted(MOUNTAINS),
                        help="mountain key (default: alta)")

    p_ing = sub.add_parser("ingest", parents=[common], help="fetch + store SNOTEL history")
    p_ing.set_defaults(func=cmd_ingest)

    p_rep = sub.add_parser("report", parents=[common], help="print season grade + storms + forecast")
    p_rep.add_argument("--as-of", help="grade as of this date YYYY-MM-DD (default: today)")
    p_rep.add_argument("--no-nws", action="store_true", help="skip NWS network calls")
    p_rep.set_defaults(func=cmd_report)

    p_an = sub.add_parser("analyze", parents=[common],
                          help="backtest the grade curve against real history")
    p_an.set_defaults(func=cmd_analyze)

    p_sc = sub.add_parser("score", parents=[common],
                          help="overall mountain score under each weighting profile")
    p_sc.add_argument("--as-of", help="score as of this date YYYY-MM-DD (default: today)")
    p_sc.add_argument("--profile", default=DEFAULT_PROFILE,
                      choices=["dynamic", *sorted(SCORE_PROFILES)],
                      help=f"highlight this profile (default: {DEFAULT_PROFILE})")
    p_sc.add_argument("--no-nws", action="store_true", help="skip NWS (forecast/weather) calls")
    p_sc.set_defaults(func=cmd_score)

    p_fc = sub.add_parser("forecast-report", parents=[common],
                          help="backtest logged forecasts vs what actually fell")
    p_fc.set_defaults(func=cmd_forecast_report)

    p_card = sub.add_parser("card", parents=[common],
                            help="emit the JSON scorecard (frontend data contract)")
    p_card.add_argument("--as-of", help="score as of this date YYYY-MM-DD (default: today)")
    p_card.add_argument("--no-nws", action="store_true", help="skip NWS (forecast/weather) calls")
    p_card.set_defaults(func=cmd_card)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
