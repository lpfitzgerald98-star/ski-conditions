"""Global / Regional Comparable Score -- absolute snow, ranked across OTHER
MOUNTAINS, not against each mountain's own history.

`score.overall_score` (surfaced as `overall` on the card) answers "is this a
good year/day FOR THIS MOUNTAIN": two of its four sub-scores (season, base)
are percentiles against the mountain's OWN history, so a resort having its
best season ever can outrank a resort having an ordinary one -- exactly
backwards for "where should I go ski right now". This module answers that
question instead.

Every input here is an ABSOLUTE, cross-mountain-comparable quantity (inches
of snow -- see pipeline.mountain_scorecard's `comparable_inputs` and
config.GLOBAL_SCORE_WEIGHTS), and the only thing that varies is which
POPULATION it's percentile-ranked against: the whole roster (`global_score`)
or one region's peers (`regional_score`). One function, `score_population`,
takes the population as its argument -- called once with everyone for the
global score, once per region for the regional one -- so the two math paths
can never drift apart.

Same in-season gate the rest of the codebase uses for cross-mountain ranking
(service.rank_within_regions, the existing region_score): a percentile can't
tell that an entire population is bare, so only `in_season is True` mountains
are ranked, or counted in anyone else's denominator.

Known limitation, not solved here (see config.GLOBAL_SCORE_WEIGHTS): absolute
inches aren't perfectly comparable across climates (snow density/quality) or
across station sitings (valley vs. summit). Percentile ranking can't fix
either; both are flagged for a future pass, not this one.
"""

from __future__ import annotations

from config import GLOBAL_SCORE_WEIGHTS
from ski.grading import percentile_rank

# Which absolute input feeds which named weight in config.GLOBAL_SCORE_WEIGHTS.
# Row field names ("abs_base_in" etc.) are what service.py flattens each
# mountain's `comparable_inputs` into -- see service.mountain_summary /
# score_mountain.
COMPONENTS = {
    "base": "abs_base_in",
    "fresh": "abs_fresh_in",
    "season": "abs_season_in",
    "forecast": "abs_forecast_in",
    # Phase 4: SnowQuality (0-100) -- NOT inches, but an absolute cross-mountain
    # quantity, percentile-ranked across the population like the others. This is
    # what stops the leaderboard being pure quantity: cold, dry, calm snow now
    # outranks a wind-hammered/crusty pile of the same depth. Missing (no recent
    # storm / off-network) drops out of the blend, same as any other component.
    "quality": "abs_quality",
}


def _percentile_vs_others(values: dict[str, float]) -> dict[str, float | None]:
    """Each key's percentile rank against the OTHER keys in `values` (never
    itself) -- fewer than 1 peer -> None, same convention as
    service.rank_against: a rank against an empty cohort is not a 0th
    percentile, it's no answer."""
    out: dict[str, float | None] = {}
    for key, v in values.items():
        others = [x for k, x in values.items() if k != key]
        out[key] = percentile_rank(v, others) if others else None
    return out


def _component_percentiles(rows: list[dict]) -> dict[str, dict[str, float | None]]:
    """For each named component, every eligible row's percentile rank against
    the other rows THAT HAVE A VALUE for that component -- a mountain missing
    one input (e.g. no forecast source reached this far) is simply excluded
    from that component's pool, not zeroed or imputed."""
    out = {}
    for component, field in COMPONENTS.items():
        values = {r["key"]: r[field] for r in rows
                 if r.get(field) is not None}
        out[component] = _percentile_vs_others(values)
    return out


def _blend(component_pcts: dict[str, float | None],
          weights: dict[str, float]) -> float | None:
    """Weighted average over whichever components this mountain actually has
    a percentile for, renormalized -- same "blend over what's available"
    convention as score.conditions_score / score.overall_score."""
    parts = [(weights.get(c, 0.0), p) for c, p in component_pcts.items()
            if p is not None and weights.get(c, 0.0) > 0]
    if not parts:
        return None
    # Same float-noise clamp as score.overall_score: weights summing to
    # "1.0" in config can land a hair off in binary float (e.g. 0.9999999999999999),
    # nudging an exact 100 numerator to 100.00000000000001 on division.
    val = sum(w * p for w, p in parts) / sum(w for w, _ in parts)
    return max(0.0, min(100.0, val))


def score_population(
    rows: list[dict], weights: dict[str, float] = GLOBAL_SCORE_WEIGHTS,
) -> dict[str, float | None]:
    """The comparable score for EXACTLY this population of mountains.

    Call once with the whole roster for the global score, once per region's
    subset for the regional score -- this function doesn't know or care which;
    the population is just whatever `rows` it's handed. Only `in_season is
    True` rows are eligible (ranked, and counted in each other's pools);
    everyone else maps to None.

    Returns {row['key']: score_0_100_or_None}.
    """
    eligible = [r for r in rows if r.get("in_season") is True]
    comp_pcts = _component_percentiles(eligible)
    out: dict[str, float | None] = {r["key"]: None for r in rows}
    for r in eligible:
        key = r["key"]
        pcts = {c: comp_pcts[c].get(key) for c in COMPONENTS}
        out[key] = _blend(pcts, weights)
    return out


def attach_global_score(
    rows: list[dict], weights: dict[str, float] = GLOBAL_SCORE_WEIGHTS,
    field: str = "global_score",
) -> list[dict]:
    """Attach `field` (default `global_score`) to every row IN PLACE, ranked
    against the WHOLE `rows` population -- the leaderboard's default sort key."""
    scores = score_population(rows, weights)
    for r in rows:
        v = scores.get(r["key"])
        r[field] = None if v is None else round(v, 1)
    return rows


def attach_regional_score(
    rows: list[dict], weights: dict[str, float] = GLOBAL_SCORE_WEIGHTS,
    field: str = "regional_score",
) -> list[dict]:
    """Attach `field` (default `regional_score`) to every row IN PLACE, each
    ranked only against the other rows sharing its `region` -- same shared
    `score_population`, one call per region so climate differences between
    regions (Northeast vs. Wasatch) can't wash a region's own good week out
    against a global pool it was never trying to compete with."""
    by_region: dict[str, list[dict]] = {}
    for r in rows:
        by_region.setdefault(r.get("region"), []).append(r)
    for pool in by_region.values():
        scores = score_population(pool, weights)
        for r in pool:
            v = scores.get(r["key"])
            r[field] = None if v is None else round(v, 1)
    return rows
