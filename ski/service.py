"""The single source of truth for "what number and letter does this mountain get".

Before this module, the answer depended on who was asking. The score card called
`/score/{key}` and rendered the absolute overall. The sidebar called `/scores`,
then -- when a region was selected -- computed a within-region percentile *in
JavaScript*, graded it against a third hardcoded threshold table that matched
neither backend curve, and painted that. Same mountain, two letters, both
labeled "grade".

They were never really the same quantity. One asks "how good is this mountain",
the other asks "how good is it compared to its neighbors". Both are worth
knowing. So instead of picking a winner, this module computes both, here, once:

    overall  -- the absolute 0-100 value from score.overall_score(), lettered on
                OVERALL_GRADE_THRESHOLDS (calibrated on the real distribution of
                power-mean-times-cover-gate values). Two of its four sub-scores
                (season, base) are percentiles against the mountain's OWN
                history, so this answers "how good is this mountain('s year)",
                not "where should I go ski right now".
    region   -- that same overall value's percentile rank against the other
                mountains in its region, lettered on GRADE_THRESHOLDS (the
                percentile curve every other percentile in this codebase uses).

Two curves on purpose: each number is graded on the curve it was calibrated for.
An absolute 50 is a B+; a 50th-percentile rank is a B-. Grading a rank on the
absolute curve would quietly inflate every mid-pack mountain.

`rank_within_regions` also attaches `global_score` / `regional_score` (see
ski.comparable), the CROSS-MOUNTAIN-COMPARABLE answer to "where should I go ski
right now": built from four ABSOLUTE inputs (current base, trailing fresh,
season-to-date, incoming forecast -- all in inches) percentile-ranked against
this same rows population, not against history. `global_score` is the
leaderboard's default sort key; `regional_score` is the same math run once per
region, so a mountain can have a great regional week without needing to top
the global board (climates differ). Both live alongside `overall`/`region`,
not in place of them -- the self-relative percentiles stay visible on the card
as context (`grades.season.percentile`, `grades.base.percentile`).

The region rank is a property of the COHORT, not of the mountain -- you cannot
compute it from one mountain's data. `rank_within_regions` therefore takes the
whole roster at once. During the live stream that roster is a mix of fresh and
cached rows (see api.stream_live); ranking against the full mixed cohort is the
point, because ranking against only the handful that have landed so far would
have Alta at the 0th percentile of a one-mountain Utah.

Nothing here touches the network or the cache. `score_mountain` is a pure
(slow, CPU+IO-bound) function of the DB and the date, which is what lets the
stream fan it out across threads.
"""

from __future__ import annotations

from datetime import date

from config import DEFAULT_PROFILE, GRADE_THRESHOLDS, MOUNTAINS
from ski import commentary, comparable
from ski.card import scorecard
from ski.grading import letter_grade, percentile_rank
from ski.regions import country_code, country_of, region_for

# Grade -> color, the one place a letter becomes a pixel. The frontend looks
# letters up in here (served via /grades) instead of re-bucketing raw scores
# against its own copy of the thresholds, which is how the curves drifted apart
# in the first place. Ordered best -> worst; the legend renders it in order.
GRADE_COLORS: dict[str, str] = {
    "A+": "#0d7a3e",
    "A":  "#1a9850",
    "A-": "#4fb265",
    "B+": "#66bd63",
    "B":  "#a6d96a",
    "B-": "#d9ef8b",
    "C+": "#fee08b",
    "C":  "#fdae61",
    "C-": "#f68e4f",
    "D":  "#f46d43",
    "F":  "#d73027",
}
NA_COLOR = "#5a636e"


def color_for(grade: str | None) -> str:
    """The color for a letter grade. Unknown / "N/A" / None -> the neutral gray."""
    return GRADE_COLORS.get(grade or "", NA_COLOR)


def mountain_summary(key: str) -> dict:
    """Static roster fields for one mountain -- no scoring, no IO."""
    m = MOUNTAINS[key]
    return {
        "key": key,
        "name": m["name"],
        "latitude": m.get("latitude"),
        "longitude": m.get("longitude"),
        "verified": m.get("verified", False),
        "source": m.get("data_source", "snotel"),
        "region": region_for(m),
        "country": country_of(m["name"]),
        "country_code": country_code(country_of(m["name"])),
    }


def score_mountain(
    key: str,
    as_of: date,
    profile: str = DEFAULT_PROFILE,
    use_network: bool = False,
    db_path: str | None = None,
) -> dict:
    """One mountain's summary row: roster fields + absolute overall + key grades.

    `region_score` / `region_grade` are absent here by construction -- a rank
    needs the cohort. Callers get them by passing a list of these through
    `rank_within_regions`. A failing source yields a row with `score=None` and an
    `error`, never an exception: one dead station must not sink the batch (or,
    now, the stream).
    """
    row = mountain_summary(key)
    kwargs = {} if db_path is None else {"db_path": db_path}
    try:
        card = scorecard(key, as_of=as_of, use_network=use_network,
                         default_profile=profile, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- see docstring
        row.update(score=None, grade="N/A", error=str(exc))
        return row

    overall = card["overall"].get(profile) or {}
    ski = card.get("skiability") or {}
    season = card["grades"]["season"] or {}
    base = card["grades"]["base"] or {}
    fc = card["forecast"] or {}
    ci = card.get("comparable_inputs") or {}
    row.update(
        # HEADLINE = absolute skiability (see build_snapshot._row_from_card);
        # the self-relative `overall` rides alongside as historical context.
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
        # Flattened for ski.comparable.score_population (global/regional score).
        abs_base_in=ci.get("base_in"),
        abs_fresh_in=ci.get("fresh_in"),
        abs_season_in=ci.get("season_in"),
        abs_forecast_in=ci.get("forecast_in"),
        abs_quality=ci.get("quality"),
        # Mountain character (config.TERRAIN_STATS) -- static, not conditions.
        abs_vertical_ft=ci.get("vertical_drop_ft"),
        abs_acres=ci.get("skiable_acres"),
        abs_pct_advanced_expert=ci.get("pct_advanced_expert"),
    )
    return row


def rank_against(score: float | None, peer_scores: list[float]) -> tuple[float | None, str | None]:
    """(percentile, letter) for `score` against `peer_scores` -- the OTHERS, not
    including itself. Fewer than 1 peer -> (None, None): a rank against an empty
    cohort is not a 0th percentile, it's no answer.

    The one place a within-region rank becomes a number, so `rank_within_regions`
    (the roster path) and `score_card` (the one-mountain path) cannot disagree.
    """
    if score is None or len(peer_scores) < 1:
        return None, None
    pct = percentile_rank(score, peer_scores)
    if pct is None:
        return None, None
    return round(pct, 0), letter_grade(pct, GRADE_THRESHOLDS)


def rank_within_regions(rows: list[dict]) -> list[dict]:
    """Attach `region_score` (percentile) + `region_grade` to each row, in place.

    Ranks each mountain's absolute `score` against the other scored mountains in
    its own region, using the same `percentile_rank` (strictly-below / total) that
    grades a season against its history -- so "72nd percentile" means the same
    thing everywhere in this codebase.

    A mountain with no score gets nulls. A mountain alone in its region (or the
    only one with data) ranks at the 0th percentile against an empty cohort, which
    is meaningless, so it gets nulls too rather than a confident-looking F.

    A RANK REQUIRES POSITIVE EVIDENCE OF COVER: only `in_season is True` is ranked.
    This is the whole reason `is_in_season` exists. A percentile cannot see that the
    entire region is bare, so in July Palisades ranked 100th in Tahoe -- an A+ on a
    mountain with no snow. Arithmetically right, completely misleading.

    Unknown (None) is suppressed too, not just off-season. Without a cover reading
    the cover gate never engages, and a season-to-date percentile still counts the
    whole winter: Stratton's ACIS station stopped reporting on May 31, and in July
    it read 60/100 and 100th in the Northeast. Suppressing unknown costs almost
    nothing in midwinter (one mountain, already at the 0th percentile) and removes
    that entire class of lie.

    Off-season mountains stay in their peers' denominators on purpose. An in-season
    mountain surrounded by dead ones genuinely IS the best option in its region, and
    should read that way.
    """
    by_region: dict[str, list[float]] = {}
    for r in rows:
        if r.get("score") is not None:
            by_region.setdefault(r["region"], []).append(r["score"])

    for r in rows:
        peers = by_region.get(r["region"], [])
        if r.get("score") is None or r.get("in_season") is not True:
            r["region_score"] = r["region_grade"] = None
            continue
        # Rank against the OTHERS: drop one instance of this mountain's own score
        # so a mountain never counts itself in its own denominator.
        others = list(peers)
        others.remove(r["score"])
        r["region_score"], r["region_grade"] = rank_against(r["score"], others)

    # The comparable score (see ski.comparable): absolute snow, ranked against
    # this SAME rows population -- global against everyone, regional per region.
    comparable.attach_global_score(rows)
    comparable.attach_regional_score(rows)
    return rows


def score_card(key: str, as_of: date, use_network: bool = True,
               cached_peers: dict[str, dict] | None = None) -> dict:
    """The full scorecard for one mountain, plus its within-region rank.

    A rank needs the cohort, and scoring the other 78 mountains to render one
    card would take ~10s. So the peers come from the render cache (`cached_peers`,
    normally `cache.get_all(...)`), which the live stream keeps fresh.

    Critically, the rank uses this mountain's OWN CACHED score, not the freshly
    computed one, whenever a cached row exists. A rank is only meaningful within a
    single snapshot: the card is often fetched offline (`network=false`) while the
    cache was built live, and ranking an offline 17.8 against live peers put Alta
    at the 100th percentile of Utah with a C overall -- while its pin, ranked
    inside the live snapshot, said something else entirely. That disagreement
    between pin and card is the exact bug this module exists to prevent.

    So: `overall` is always fresh; `region` is always a coherent rank inside the
    cached snapshot, and therefore always matches the pin. Without a cache (first
    ever run) it falls back to the fresh score, and with no peers the block is
    present but null -- the frontend renders "—" but never has to guess the shape.
    """
    card = scorecard(key, as_of=as_of, use_network=use_network)
    region = region_for(MOUNTAINS[key])
    overall = card["overall"].get(card["default_profile"]) or {}

    peers = [
        v["score"] for k, v in (cached_peers or {}).items()
        if k != key and v.get("region") == region and v.get("score") is not None
    ]
    own = (cached_peers or {}).get(key, {}).get("score")
    pct, grade = rank_against(own if own is not None else overall.get("score"), peers)
    # Same gate the roster path applies (rank_within_regions), for the same reason:
    # a rank needs positive evidence of cover, so off-season AND unknown are both
    # suppressed. Only `is True` ranks.
    if card.get("in_season") is not True:
        pct = grade = None
    card["region"] = {
        "name": region,
        "score": pct,
        "grade": grade,
        "cohort_size": len(peers) + 1,
    }
    card["roster_size"] = len(MOUNTAINS)
    # AI phrasing of the grade -- cached per (mountain, day) in SQLite, so only
    # the first card render of the day pays an API call; off-season cards and
    # keyless environments get null (see ski/commentary.py for the gates).
    card["commentary"] = commentary.get_or_generate(key, as_of, card)
    return card


def score_roster(
    keys: list[str],
    as_of: date,
    profile: str = DEFAULT_PROFILE,
    use_network: bool = False,
    db_path: str | None = None,
) -> list[dict]:
    """Score every mountain in `keys` and rank them within their regions.

    Serial on purpose -- the concurrent paths (the /scores thread pool, the SSE
    fan-out) call `score_mountain` directly and hand the results here to rank.
    """
    rows = [score_mountain(k, as_of, profile, use_network, db_path) for k in keys]
    return rank_within_regions(rows)
