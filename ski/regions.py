"""The one region taxonomy.

Lived in `api.py` until the SSE stream and the scoring service both needed it;
an HTTP module is the wrong place to import a domain concept from. Everything
that groups mountains -- the sidebar headers, the region filter, the
within-region percentile on the score card -- resolves through `region_of` here,
so there is exactly one answer to "what region is this mountain in".

The region is parsed from the trailing state/province code in the display name
("Alta, UT" -> "Utah") rather than stored per-mountain, because the name already
carries it and a second field would be a second thing to keep in sync.
"""

from __future__ import annotations

_STATE_REGION = {
    "UT": "Utah",
    # Southwest folded into Colorado (2026-07: a 1-mountain region can never get a
    # meaningful within-region rank -- rank_against needs at least one peer -- and
    # Taos sits culturally/geographically in the same southern-Rockies belt).
    "CO": "Colorado", "NM": "Colorado", "AZ": "Colorado",
    "CA": "Tahoe & Sierra", "NV": "Tahoe & Sierra",
    # Alaska folded into Pacific Northwest for the same reason (1-mountain region);
    # both are Pacific-coast climates.
    "WA": "Pacific Northwest", "OR": "Pacific Northwest", "AK": "Pacific Northwest",
    "WY": "Northern Rockies", "MT": "Northern Rockies", "ID": "Northern Rockies",
    "VT": "Northeast", "NH": "Northeast", "ME": "Northeast",
    "NY": "Northeast", "MA": "Northeast",
    "BC": "British Columbia",
    "AB": "Alberta",
    "QC": "Eastern Canada", "ON": "Eastern Canada",
    "AU": "Australia", "NZ": "New Zealand",
    # South America stays ONE leaf (2026-07: only 5 mountains total; a Chile/
    # Argentina split added a region-picker layer for no ranking benefit).
    "CL": "South America", "AR": "South America",
    # Europe: leaves are NORTH/SOUTH, not individual mountain ranges (2026-07:
    # the prior 7-way range split -- Alps/Dolomites/Pyrenees/Scandinavia/
    # Carpathians/Balkans/Scotland -- was too granular for a useful within-region
    # leaderboard). Nordic + British Isles resorts are the clearly northern
    # cluster; everything else (Alpine, Iberian, Carpathian, Balkan) groups as
    # Southern Europe. The old per-mountain `region` overrides for the Dolomites/
    # Pyrenees (needed because FR/IT default to the Alps) are gone -- both ranges
    # now land in the same Southern Europe bucket as the Alps, so the country-code
    # default is already correct.
    "FR": "Southern Europe", "CH": "Southern Europe", "AT": "Southern Europe",
    "IT": "Southern Europe", "DE": "Southern Europe", "SI": "Southern Europe",
    "ES": "Southern Europe", "AD": "Southern Europe",
    "RO": "Southern Europe", "SK": "Southern Europe", "PL": "Southern Europe",
    "BG": "Southern Europe",
    "NO": "Northern Europe", "SE": "Northern Europe", "FI": "Northern Europe",
    "GB": "Northern Europe",
}


# Country per state/province code, from the same parse as the region. The sidebar
# ranks all mountains together and labels each with its country, so this has to
# come from the one taxonomy rather than being re-derived in JavaScript.
_STATE_COUNTRY = {
    "UT": "USA", "CO": "USA", "CA": "USA", "NV": "USA", "WA": "USA", "OR": "USA",
    "WY": "USA", "MT": "USA", "ID": "USA", "NM": "USA", "AZ": "USA", "AK": "USA",
    "VT": "USA", "NH": "USA", "ME": "USA", "NY": "USA", "MA": "USA",
    "BC": "Canada", "AB": "Canada", "QC": "Canada", "ON": "Canada",
    "AU": "Australia", "NZ": "New Zealand", "CL": "Chile", "AR": "Argentina",
    "FR": "France", "CH": "Switzerland", "AT": "Austria", "IT": "Italy",
    "DE": "Germany", "SI": "Slovenia", "ES": "Spain", "AD": "Andorra",
    "NO": "Norway", "SE": "Sweden", "FI": "Finland", "RO": "Romania",
    "SK": "Slovakia", "PL": "Poland", "BG": "Bulgaria", "GB": "United Kingdom",
}

# ISO-ish short codes for the sidebar chip -- "USA" is already short, the rest
# need shrinking to fit beside a mountain name.
_COUNTRY_CODE = {
    "USA": "USA", "Canada": "CAN", "Australia": "AUS",
    "New Zealand": "NZL", "Chile": "CHL", "Argentina": "ARG",
    "France": "FRA", "Switzerland": "CHE", "Austria": "AUT", "Italy": "ITA",
    "Germany": "DEU", "Slovenia": "SVN", "Spain": "ESP", "Andorra": "AND",
    "Norway": "NOR", "Sweden": "SWE", "Finland": "FIN", "Romania": "ROU",
    "Slovakia": "SVK", "Poland": "POL", "Bulgaria": "BGR",
    "United Kingdom": "GBR",
}


# The region hierarchy. Leaves are the values of _STATE_REGION (what `region_of`
# returns); parents group them into progressively wider scopes. A mountain's full
# membership is its leaf plus every ancestor -- Park City is in Utah AND Western
# North America AND Northern Hemisphere -- derived here rather than stored per
# mountain, for the same keep-one-thing-in-sync reason as the leaf parse above.
# Europe slots in later as a new subtree under Northern Hemisphere.
_PARENT: dict[str, str | None] = {
    "Northern Hemisphere": None,
    "Southern Hemisphere": None,
    "Western North America": "Northern Hemisphere",
    "East Coast (incl. Canada)": "Northern Hemisphere",
    "Europe": "Northern Hemisphere",
    "South America": "Southern Hemisphere",   # now a LEAF (Chile+Argentina merged)
    "Oceania": "Southern Hemisphere",
    "Northern Europe": "Europe",
    "Southern Europe": "Europe",
    "Utah": "Western North America",
    "Colorado": "Western North America",      # now includes the old Southwest
    "Tahoe & Sierra": "Western North America",
    "Pacific Northwest": "Western North America",   # now includes the old Alaska
    "Northern Rockies": "Western North America",
    "British Columbia": "Western North America",
    "Alberta": "Western North America",
    "Northeast": "East Coast (incl. Canada)",
    "Eastern Canada": "East Coast (incl. Canada)",
    "Australia": "Oceania",
    "New Zealand": "Oceania",
}


def region_tree() -> list[dict]:
    """The hierarchy as serializable nodes ({id, name, parent}), for /meta.

    Node ids ARE the display names -- they're unique, and the roster rows already
    carry the leaf name in `region`, so the frontend can join without a second
    id-to-name map. A leaf `region_of` produces that isn't in the tree (a new,
    unmapped state code) simply won't appear here; the frontend treats such
    orphans as root-level leaves, so nothing breaks while it waits for a parent.
    """
    return [{"id": n, "name": n, "parent": p} for n, p in _PARENT.items()]


def ancestors(leaf: str) -> list[str]:
    """Every region containing `leaf`, nearest first, excluding the leaf itself."""
    out = []
    node = _PARENT.get(leaf)
    while node is not None:
        out.append(node)
        node = _PARENT.get(node)
    return out


def descendant_leaves(region: str) -> set[str]:
    """The leaf regions under `region` (itself included if it is a leaf)."""
    children = [n for n, p in _PARENT.items() if p == region]
    if not children:
        return {region}
    leaves: set[str] = set()
    for c in children:
        leaves |= descendant_leaves(c)
    return leaves


def _code(name: str) -> str:
    """The trailing state/province code in a display name ('Alta, UT' -> 'UT')."""
    return name.rsplit(",", 1)[-1].strip().upper() if "," in name else ""


def region_of(name: str) -> str:
    """Region for a mountain from the trailing state/province code in its name.

    Falls back to the raw code (then "Other") if unmapped, so a new state shows
    up as its own bucket instead of silently joining someone else's.
    """
    code = _code(name)
    return _STATE_REGION.get(code, code or "Other")


def region_for(mountain: dict) -> str:
    """The leaf region for one MOUNTAINS entry.

    Normally the country-code parse (`region_of`), but an entry may set an
    explicit `region` when its country straddles two ranges -- the French
    Pyrenees resorts and the Italian Dolomites can't be told apart from the
    code alone. The override must itself be a known leaf so it slots into the
    hierarchy like any other.
    """
    return mountain.get("region") or region_of(mountain["name"])


def country_of(name: str) -> str:
    """Country for a mountain, from the same trailing code as `region_of`."""
    return _STATE_COUNTRY.get(_code(name), "Other")


def country_code(country: str) -> str:
    """Three-letter chip label for a country name."""
    return _COUNTRY_CODE.get(country, (country[:3].upper() if country else "—"))
