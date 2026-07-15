"""Tests for the region hierarchy (ski/regions.py).

No network, no pytest. Run standalone:  python tests/test_regions.py

The invariant that matters: every leaf `region_of` can produce is reachable from
a root, so a mountain's full membership (leaf + ancestors) is always derivable
and the frontend's parent-region filtering can never orphan a pin.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ski.regions import (  # noqa: E402
    _STATE_REGION,
    ancestors,
    descendant_leaves,
    region_for,
    region_of,
    region_tree,
)


def test_every_leaf_is_rooted():
    """Every region a state code maps to must climb to a hemisphere root."""
    tree_ids = {n["id"] for n in region_tree()}
    roots = {n["id"] for n in region_tree() if n["parent"] is None}
    for leaf in set(_STATE_REGION.values()):
        assert leaf in tree_ids, f"{leaf} missing from the tree"
        chain = ancestors(leaf)
        assert chain and chain[-1] in roots, f"{leaf} does not reach a root: {chain}"


def test_ancestors_nearest_first():
    assert ancestors("Utah") == ["Western North America", "Northern Hemisphere"]
    assert ancestors("Argentina") == ["South America", "Southern Hemisphere"]
    assert ancestors("Northern Hemisphere") == []


def test_descendant_leaves_of_parent():
    assert descendant_leaves("South America") == {"Chile", "Argentina"}
    assert "Utah" in descendant_leaves("Northern Hemisphere")
    assert "Chile" not in descendant_leaves("Northern Hemisphere")
    # a leaf's descendant set is itself
    assert descendant_leaves("Utah") == {"Utah"}


def test_unknown_region_is_its_own_leaf():
    """An unmapped code (new state) degrades to a root-level orphan leaf: no
    ancestors, descendant set = itself. Nothing breaks while it awaits a parent."""
    assert ancestors("XX") == []
    assert descendant_leaves("XX") == {"XX"}


def test_region_of_still_parses_names():
    """The hierarchy sits ON TOP of the leaf parse; the parse itself is unchanged."""
    assert region_of("Alta, UT") == "Utah"
    assert region_of("Cerro Catedral, AR") == "Argentina"
    assert region_of("Zermatt, CH") == "Alps"
    assert region_of("Bansko, BG") == "Balkans"


def test_europe_sits_under_northern_hemisphere():
    assert ancestors("Alps") == ["Europe", "Northern Hemisphere"]
    assert descendant_leaves("Europe") == {
        "Alps", "Dolomites", "Pyrenees", "Scandinavia",
        "Carpathians", "Balkans", "Scotland",
    }


def test_region_override_beats_the_country_parse():
    """A split country's minority resorts pin their range explicitly; every
    override in the real roster must itself be a leaf the tree knows."""
    assert region_for({"name": "Chamonix, FR"}) == "Alps"
    assert region_for({"name": "Saint-Lary-Soulan, FR", "region": "Pyrenees"}) == "Pyrenees"
    assert region_for({"name": "Cortina d'Ampezzo, IT", "region": "Dolomites"}) == "Dolomites"

    from config import MOUNTAINS
    tree_ids = {n["id"] for n in region_tree()}
    for key, m in MOUNTAINS.items():
        if "region" in m:
            assert m["region"] in tree_ids, f"{key} overrides to unknown region {m['region']}"


def test_tree_nodes_are_wellformed():
    ids = [n["id"] for n in region_tree()]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    id_set = set(ids)
    for n in region_tree():
        assert n["parent"] is None or n["parent"] in id_set, \
            f"{n['id']} has dangling parent {n['parent']}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
