"""Thin HTTP layer over the scoring engine -- the API a map frontend calls.

Deliberately minimal: it does no scoring of its own. `ski.service` decides what
number and letter a mountain gets; this module moves those over HTTP. All the
logic lives in `ski/`.

Run it:
    pip install fastapi "uvicorn[standard]"
    uvicorn api:app --reload
    # then: http://127.0.0.1:8000/
    #       http://127.0.0.1:8000/live/stream
    #       http://127.0.0.1:8000/docs   (auto OpenAPI)

Endpoints:
    GET /mountains          roster: key, name, lat/lon, verified, region
    GET /grades             the letter -> color scale (frontend paints from this)
    GET /meta               profiles + the region hierarchy (live meta.json twin)
    GET /scores             one summary row per mountain, ranked within region
    GET /score/{mountain}   full scorecard JSON (?as_of=YYYY-MM-DD&network=false)
    GET /forecast-accuracy/{mountain}
                            logged forecast predictions vs what later verified
                            (see ski.forecast_log / pipeline.forecast_accuracy)
    GET /live/stream        SSE: cached snapshot now, live per-mountain updates as
                            they land, then stream_complete
    GET /health             liveness
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import DB_PATH, DEFAULT_PROFILE, GRADE_THRESHOLDS, MOUNTAINS
from ski import cache, pipeline
from ski.regions import region_tree
from ski.service import (
    GRADE_COLORS,
    NA_COLOR,
    mountain_summary,
    rank_within_regions,
    score_card,
    score_mountain,
    score_roster,
)

WEB_DIR = Path(__file__).parent / "web"

# How long any one mountain gets to fetch live data before the stream gives up on
# it and moves on. The whole roster should be live in ~20s; a single slow source
# must not hold that open.
PER_MOUNTAIN_TIMEOUT_S = 25.0
# Concurrent live fetches. Counter-intuitively this should be SMALL.
#
# Scoring a mountain is CPU-bound (pandas over its whole station history), not
# IO-bound: the HTTP calls are ~0.3s of a ~1s job, and Open-Meteo happily serves
# the whole roster in 4s at 12-way. Because that CPU work holds the GIL, threads
# cannot overlap it -- they only add convoy contention. Scoring the roster with
# network disabled takes 7s serially and 42s across 12 threads.
#
# So the fan-out exists only to hide the ~0.3s of network wait per mountain, and
# a couple of workers buys all of that. Measured, full 79-mountain roster, live:
#      1 worker  -> 39s      6 workers -> 75s
#      2 workers -> 22s     12 workers -> 82s
#      3 workers -> 20s     24 workers -> 86s + errors
# Don't raise this without re-measuring; more threads have made it slower every
# single time. Real headroom is in the CPU path (see grading._prepare) or a
# process pool, not here.
STREAM_CONCURRENCY = 3

# ONE pool for the whole process, not one per request. Each open browser tab holds
# its own /live/stream, and a per-request pool meant N tabs spawned N*12 pandas
# threads: they starved each other on the GIL and left /score/{mountain} hanging
# indefinitely behind them. The per-stream semaphore still caps how many fetches
# any single client has in flight; this caps how many the server ever runs at once.
_LIVE_POOL = ThreadPoolExecutor(max_workers=STREAM_CONCURRENCY,
                                thread_name_prefix="ski-live")

app = FastAPI(
    title="GladeGrade API",
    version="0.2.0",
    summary="Snow/weather scorecards for tracked mountains.",
)

# A public map frontend will call this from the browser; allow any origin (the
# API is read-only and unauthenticated). Tighten if it ever gets write routes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _parse_as_of(as_of: str | None) -> date:
    if not as_of:
        return date.today()
    try:
        return datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="as_of must be YYYY-MM-DD") from None


@app.get("/", include_in_schema=False)
def index():
    """Serve the interactive map frontend."""
    idx = WEB_DIR / "index.html"
    if not idx.exists():
        raise HTTPException(status_code=404, detail="web/index.html not found")
    return FileResponse(idx)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mountains": len(MOUNTAINS)}


@app.get("/grades")
def grades() -> dict:
    """The letter -> color scale. The frontend paints from this rather than
    keeping its own copy of the grading thresholds (which is how the map and the
    score card drifted onto different curves).

    `thresholds` (the percentile -> letter curve) is here for the ONE lettering
    the frontend does itself: ranking rows within a non-leaf region selection,
    a cohort the backend can't precompute without shipping a rank per tree node
    on every row. Same curve, served not copied, so it can't drift."""
    return {"colors": GRADE_COLORS, "na_color": NA_COLOR,
            "thresholds": GRADE_THRESHOLDS}


@app.get("/meta")
def meta() -> dict:
    """Roster-level metadata: profiles + the region hierarchy. The live-mode
    counterpart of the static build's meta.json (build_snapshot.py), so the
    frontend region picker works identically in both modes."""
    return {
        "as_of": None,
        "profiles": ["dynamic", "weekend", "month", "season"],
        "default_profile": DEFAULT_PROFILE,
        "region_tree": region_tree(),
        "roster_size": len(MOUNTAINS),
    }


@app.get("/mountains")
def list_mountains() -> dict:
    """The roster a map renders as pins."""
    return {"mountains": [mountain_summary(k) for k in sorted(MOUNTAINS)]}


_SORT_KEYS = ("global_score", "regional_score", "score")


@app.get("/scores")
def all_scores(
    profile: str = Query(DEFAULT_PROFILE, description="scoring profile to rank by"),
    as_of: str | None = Query(None, description="score as of this date, YYYY-MM-DD"),
    network: bool = Query(False, description="fetch live forecasts (slow; off by default)"),
    sort: str = Query("global_score", description=f"sort key, one of {_SORT_KEYS}"),
) -> dict:
    """Overall score + within-region rank + key grades for EVERY mountain.

    Kept for the CLI, the no-JS case, and `?network=true` batch refreshes. The map
    itself now boots off /live/stream instead, which paints instantly and fills in.

    `sort` defaults to `global_score` (ski.comparable's cross-mountain absolute
    score -- "where should I go ski right now") rather than `score` (the
    absolute `overall`, partly self-relative -- "how good is this mountain('s
    year)"). Pass `sort=regional_score` for a region-scoped leaderboard (each
    mountain's rank against only its own region's peers) or `sort=score` for
    the legacy absolute-overall order. Rows with a null sort value sort last,
    not first -- an unranked mountain shouldn't top the board by looking like
    a very negative score.
    """
    if sort not in _SORT_KEYS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {_SORT_KEYS}")
    parsed = _parse_as_of(as_of)
    keys = sorted(MOUNTAINS)
    if network:
        # Every mountain has a forecast provider (NWS in the US, Open-Meteo
        # elsewhere), so the live path touches the whole roster -- fan the
        # per-mountain work out across threads (it's all IO wait).
        with ThreadPoolExecutor(max_workers=STREAM_CONCURRENCY) as pool:
            rows = list(pool.map(lambda k: score_mountain(k, parsed, profile, network), keys))
        rows = rank_within_regions(rows)
    else:
        rows = score_roster(keys, parsed, profile, network)
    rows = sorted(rows, key=lambda r: (r.get(sort) is None, -(r.get(sort) or 0)))
    return {"profile": profile, "as_of": parsed.isoformat(), "network": network,
            "sort": sort, "mountains": rows}


@app.get("/score/{mountain}")
def score_one(
    mountain: str,
    as_of: str | None = Query(None, description="grade as of this date, YYYY-MM-DD"),
    network: bool = Query(True, description="fetch live NWS forecast/weather"),
) -> dict:
    """Full scorecard for one mountain (the map's popup / detail payload).

    Carries both numbers the card shows: the absolute `overall` and the
    `region` block ranking it against its neighbors (peers read from the render
    cache -- see service.score_card).
    """
    if mountain not in MOUNTAINS:
        raise HTTPException(status_code=404, detail=f"unknown mountain '{mountain}'")
    parsed = _parse_as_of(as_of)
    return score_card(mountain, parsed, use_network=network,
                      cached_peers=cache.get_all(DB_PATH, parsed))


@app.get("/forecast-accuracy/{mountain}")
def forecast_accuracy(mountain: str) -> dict:
    """Logged forecast predictions vs what the station later recorded, for every
    horizon whose forecast window has fully elapsed (see ski.forecast_log,
    pipeline.forecast_accuracy). Read-only -- this never triggers a fetch or a
    log write, only reads what mountain_scorecard has already logged live.

    Empty `rows` until forecasts have been logged AND their horizons have
    elapsed: a freshly-deployed mountain needs a few days, and a Northern
    Hemisphere mountain needs an in-season winter (Nov onward) before this has
    anything to say. A currently in-season Southern Hemisphere mountain can
    show results now.
    """
    if mountain not in MOUNTAINS:
        raise HTTPException(status_code=404, detail=f"unknown mountain '{mountain}'")
    df = pipeline.forecast_accuracy(mountain, db_path=DB_PATH)
    return {"mountain": mountain, "rows": df.to_dict(orient="records")}


# ---------------------------------------------------------------------------
# Live stream
# ---------------------------------------------------------------------------
def _sse(event: str, payload: dict) -> str:
    """One SSE frame. The trailing blank line is what dispatches it -- without it
    the browser buffers the event forever waiting for more data."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_rows(as_of: date, profile: str) -> list[dict]:
    """What to paint at t=0: the last cached summary per mountain, falling back to
    a bare roster row for mountains never cached (a gray pin beats a missing one).

    Marked `stale` so the frontend dims them until the live update lands.
    """
    cached = cache.get_all(DB_PATH, as_of)
    rows = []
    for key in sorted(MOUNTAINS):
        hit = cached.get(key)
        if hit:
            # Static roster fields (name, lat/lon, region, country) always come
            # from config, never from the cached payload -- a row written before
            # a field existed, or before a mountain was renamed, must not resurrect
            # stale metadata. Only the SCORES come out of the cache.
            row = {**dict(hit), **mountain_summary(key)}
            row.update({k: hit.get(k) for k in ("score", "grade", "in_season",
                                                "cover_depth", "season_grade",
                                                "base_grade", "base_depth", "fresh_7d",
                                                "season_progress", "incoming_inches",
                                                "alert", "fetched_at", "cached_as_of")})
            row["status"] = "stale"
        else:
            row = mountain_summary(key)
            row.update(score=None, grade="N/A", status="loading")
        rows.append(row)
    return rank_within_regions(rows)


def _score_and_cache(key: str, as_of: date, profile: str) -> dict:
    """Score one mountain live, then write it to the render cache. Runs in a
    worker thread -- both halves are blocking, and the SQLite write in particular
    must not happen on the event loop (it can wait on another stream's lock).

    Caching only successes: a timed-out or errored fetch must never overwrite a
    good row with a blank one.
    """
    row = score_mountain(key, as_of, profile, use_network=True)
    row["status"] = "error" if row.get("error") else "live"
    if row["status"] == "live":
        # Ranks are cohort-dependent, so they're recomputed on read, never stored.
        cache.put(DB_PATH, key, as_of,
                  {k: v for k, v in row.items()
                   if k not in ("status", "region_score", "region_grade")})
    return row


async def _live_updates(keys: list[str], as_of: date, profile: str):
    """Yield (key, fresh_row) as each mountain's live fetch lands, in whatever
    order they land.

    `_score_and_cache` is blocking (requests + pandas + sqlite), so each one runs
    in a thread. A per-mountain timeout keeps one hung source from stalling the
    stream, and a semaphore caps how many upstream APIs we hit at once.
    """
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(STREAM_CONCURRENCY)

    async def one(key: str) -> tuple[str, dict]:
        async with sem:
            try:
                row = await asyncio.wait_for(
                    loop.run_in_executor(_LIVE_POOL, _score_and_cache, key, as_of, profile),
                    timeout=PER_MOUNTAIN_TIMEOUT_S,
                )
                return key, row
            except asyncio.TimeoutError:
                row = mountain_summary(key)
                row.update(score=None, grade="N/A", status="error",
                           error=f"timed out after {PER_MOUNTAIN_TIMEOUT_S:.0f}s")
                return key, row
            except Exception as exc:  # noqa: BLE001 -- one mountain, not the stream
                row = mountain_summary(key)
                row.update(score=None, grade="N/A", status="error", error=str(exc))
                return key, row

    tasks = [asyncio.create_task(one(k)) for k in keys]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        # The pool is shared and outlives this request, so cancel THIS stream's
        # pending work rather than shutting the pool down. Threads already running
        # will finish and cache their result -- harmless, and it warms the cache.
        for t in tasks:
            t.cancel()


@app.get("/live/stream")
async def live_stream(
    request: Request,
    profile: str = Query(DEFAULT_PROFILE, description="scoring profile to rank by"),
    as_of: str | None = Query(None, description="score as of this date, YYYY-MM-DD"),
):
    """Server-Sent Events: instant cached snapshot, then live data as it arrives.

    Frame sequence:
        event: snapshot         all mountains, from cache, status stale|loading
        event: mountain_update  one mountain, status live|error   (xN, as they land)
        event: stream_complete  settled within-region ranks + counts

    Why the ranks get re-sent at the end: a within-region percentile depends on the
    whole cohort, so every row's rank shifts slightly as fresher neighbors land.
    Each `mountain_update` carries the rank computed against the best cohort known
    at that instant (fresh where landed, cached elsewhere) -- good enough to paint
    -- and `stream_complete` carries the settled values.
    """
    parsed = _parse_as_of(as_of)
    keys = sorted(MOUNTAINS)

    async def gen():
        rows = _snapshot_rows(parsed, profile)
        merged = {r["key"]: r for r in rows}
        yield _sse("snapshot", {"as_of": parsed.isoformat(), "profile": profile,
                                "mountains": rows, "timestamp": _now()})

        live = errored = 0
        async for key, fresh in _live_updates(keys, parsed, profile):
            if await request.is_disconnected():
                return                      # client navigated away; stop fetching

            if fresh["status"] == "live":
                live += 1
            else:
                errored += 1
                # Keep the stale row's numbers on an error; the client dims it via
                # status rather than blanking a pin we had a decent value for.
                if merged[key].get("score") is not None and fresh.get("score") is None:
                    fresh = {**merged[key], **{k: v for k, v in fresh.items()
                                               if k in ("status", "error")}}

            merged[key] = fresh
            rank_within_regions(list(merged.values()))   # re-rank against the mixed cohort
            yield _sse("mountain_update", {**merged[key], "mountain_id": key,
                                           "timestamp": _now()})

        settled = rank_within_regions(list(merged.values()))
        yield _sse("stream_complete", {
            "as_of": parsed.isoformat(), "profile": profile,
            "live": live, "errors": errored, "total": len(keys),
            "mountains": settled, "timestamp": _now(),
        })

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this, an nginx-fronted host (Render, most PaaS proxies) will
            # buffer the whole response and the client sees nothing until the end
            # -- which looks exactly like a hung stream. This is the #1 way SSE
            # silently breaks in deploy that works fine on localhost.
            "X-Accel-Buffering": "no",
        },
    )


# The frontend is now split into css/js/data files, not one inline index.html.
# Serve them so `uvicorn api:app` still works end-to-end and so the live-backend
# hosting mode can serve the whole site. Mounted LAST: every API route declared
# above is matched first; only unmatched paths (static assets) fall through here.
# In static/GitHub-Pages mode this module isn't used at all -- Pages serves web/.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
