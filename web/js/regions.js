// The region hierarchy, client side. The tree itself is owned by the backend
// (ski/regions.py, served in meta.region_tree); this module only joins it with
// the loaded rows -- membership, counts, bboxes -- so nothing here is a second
// source of truth about which region contains what.
//
// Node ids ARE display names (unique, and rows already carry the leaf name in
// `region`). A leaf present in the rows but missing from the tree (a new state
// code the backend hasn't placed yet) is treated as a root-level leaf, so the
// picker and filter keep working while it waits for a parent.

let byId = new Map();      // id -> {id, name, parent}
let children = new Map();  // id -> [child id, ...]

export function setRegionTree(nodes) {
  byId = new Map();
  children = new Map();
  (nodes || []).forEach(n => {
    byId.set(n.id, n);
    if (n.parent != null) {
      if (!children.has(n.parent)) children.set(n.parent, []);
      children.get(n.parent).push(n.id);
    }
  });
}

export function isLeaf(id) {
  return !children.has(id);   // orphans (not in the tree at all) are leaves too
}

// Every leaf region under `id` (itself, if it is a leaf). Mirrors
// regions.descendant_leaves in Python.
export function leafSetOf(id) {
  if (isLeaf(id)) return new Set([id]);
  const out = new Set();
  (children.get(id) || []).forEach(c => leafSetOf(c).forEach(l => out.add(l)));
  return out;
}

// Picker options: a depth-first walk of the tree, keeping only nodes that
// actually contain loaded mountains, followed by any orphan leaves the rows
// mention that the tree doesn't. Each entry: {id, depth, count}.
export function optionsList(rows) {
  const leafCounts = new Map();
  rows.forEach(r => leafCounts.set(r.region, (leafCounts.get(r.region) || 0) + 1));
  const countOf = id => {
    let n = 0;
    leafSetOf(id).forEach(l => { n += leafCounts.get(l) || 0; });
    return n;
  };

  const out = [];
  const walk = (id, depth) => {
    const count = countOf(id);
    if (!count) return;
    out.push({ id, depth, count });
    (children.get(id) || []).slice().sort().forEach(c => walk(c, depth + 1));
  };
  [...byId.values()].filter(n => n.parent == null).map(n => n.id).sort()
    .forEach(id => walk(id, 0));

  [...leafCounts.keys()].filter(l => !byId.has(l)).sort()
    .forEach(l => out.push({ id: l, depth: 0, count: leafCounts.get(l) }));
  return out;
}
