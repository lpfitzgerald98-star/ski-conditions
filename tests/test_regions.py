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
    # South America is now a LEAF (Chile+Argentina merged, 2026-07: too few
    # mountains for a country-level split to be worth a picker layer).
    assert ancestors("South America") == ["Southern Hemisphere"]
    assert ancestors("Northern Hemisphere") == []


def test_descendant_leaves_of_parent():
    # South America has no children of its own now -- its descendant set is
    # itself, same as any other leaf.
    assert descendant_leaves("South America") == {"South America"}
    assert "Utah" in descendant_leaves("Northern Hemisphere")
    assert "South America" not in descendant_leaves("Northern Hemisphere")
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
    # South America no longer splits by country (2026-07 consolidation).
    assert region_of("Cerro Catedral, AR") == "South America"
    assert region_of("Zermatt, CH") == "Southern Europe"
    assert region_of("Bansko, BG") == "Southern Europe"
    assert region_of("Hemsedal, NO") == "Northern Europe"
    assert region_of("Cairngorm, GB") == "Northern Europe"


def test_western_north_america_folds_the_1_mountain_regions():
    """Alaska and Southwest each had exactly one mountain -- too few for a
    within-region rank to ever mean anything (rank_against needs a real peer) --
    so they fold into a geographically adjacent, larger neighbor."""
    assert region_of("Alyeska, AK") == "Pacific Northwest"
    assert region_of("Taos Ski Valley, NM") == "Colorado"


def test_europe_sits_under_northern_hemisphere():
    assert ancestors("Southern Europe") == ["Europe", "Northern Hemisphere"]
    assert ancestors("Northern Europe") == ["Europe", "Northern Hemisphere"]
    assert descendant_leaves("Europe") == {"Northern Europe", "Southern Europe"}


def test_region_override_beats_the_country_parse():
    """An explicit per-mountain `region` still wins over the country-code parse
    (the mechanism the old Dolomites/Pyrenees split used) -- verified generically
    since no mountain in the real roster still needs one post-consolidation."""
    assert region_for({"name": "Chamonix, FR"}) == "Southern Europe"
    assert region_for({"name": "Chamonix, FR", "region": "Northern Europe"}) == "Northern Europe"

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
