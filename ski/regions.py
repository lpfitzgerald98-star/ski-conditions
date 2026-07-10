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
    "CO": "Colorado",
    "CA": "Tahoe & Sierra", "NV": "Tahoe & Sierra",
    "WA": "Pacific Northwest", "OR": "Pacific Northwest",
    "WY": "Northern Rockies", "MT": "Northern Rockies", "ID": "Northern Rockies",
    "NM": "Southwest", "AZ": "Southwest",
    "AK": "Alaska",
    "VT": "Northeast", "NH": "Northeast", "ME": "Northeast",
    "NY": "Northeast", "MA": "Northeast",
    "BC": "British Columbia",
    "AB": "Alberta",
    "QC": "Eastern Canada", "ON": "Eastern Canada",
    "AU": "Australia", "NZ": "New Zealand",
    "CL": "Chile", "AR": "Argentina",
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
}

# ISO-ish short codes for the sidebar chip -- "USA" is already short, the rest
# need shrinking to fit beside a mountain name.
_COUNTRY_CODE = {
    "USA": "USA", "Canada": "CAN", "Australia": "AUS",
    "New Zealand": "NZL", "Chile": "CHL", "Argentina": "ARG",
}


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


def country_of(name: str) -> str:
    """Country for a mountain, from the same trailing code as `region_of`."""
    return _STATE_COUNTRY.get(_code(name), "Other")


def country_code(country: str) -> str:
    """Three-letter chip label for a country name."""
    return _COUNTRY_CODE.get(country, (country[:3].upper() if country else "—"))
