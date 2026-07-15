// Central state + a tiny pub/sub bus. Modules read `state`, mutate it through the
// setters, and subscribe to change events instead of calling each other directly.

import { isLeaf, leafSetOf } from "./regions.js";
import { letterFor } from "./grades.js";

const listeners = new Map();   // event -> Set<fn>

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}
export function emit(event, payload) {
  (listeners.get(event) || []).forEach(fn => fn(payload));
}

export const state = {
  scores: [],        // roster rows for the current profile
  byKey: {},         // key -> row
  meta: null,        // snapshot meta (as_of, profiles, regions, ...)
  profile: null,
  region: "All",
  selected: null,    // selected mountain key
  complete: false,   // stream finished (always true in static mode)
  inViewKeys: null,  // Set of keys currently in the map viewport (null = all)
  histDate: null,    // ISO date being viewed retrospectively (null = today/live)
};

export function setInView(keys) { state.inViewKeys = keys; emit("inview", keys); }

export function setScores(rows) {
  state.scores = rows;
  state.byKey = {};
  rows.forEach(r => { state.byKey[r.key] = r; });
  recomputeSelectionRanks();
  emit("scores", rows);
}

// Merge one updated row (a live SSE mountain_update) without dropping the rest.
export function upsertRow(row) {
  const i = state.scores.findIndex(r => r.key === row.key);
  if (i >= 0) state.scores[i] = row; else state.scores.push(row);
  state.byKey[row.key] = row;
  recomputeSelectionRanks();
  emit("row", row);
}

export function setRegion(region) {
  state.region = region;
  recomputeSelectionRanks();
  emit("region", region);
}
export function setSelected(key)  { state.selected = key; emit("selected", key); }

// The rows currently on screen, honoring the region filter. Regions are a
// hierarchy: a selection matches every row whose LEAF region falls under it
// (picking "Western North America" includes Utah, Colorado, ...).
export function visibleScores() {
  if (state.region === "All") return state.scores;
  const leaves = leafSetOf(state.region);
  return state.scores.filter(m => leaves.has(m.region));
}

// Within-selection percentiles for NON-LEAF region selections, key -> {value,
// grade}. Leaf cohorts come precomputed from the backend (region_score /
// region_grade); a parent's cohort spans several leaves, and precomputing a rank
// per tree node on every row would bloat the payload for no gain -- so this one
// case is ranked here, with the same strictly-below/total formula and the same
// gates as service.rank_within_regions: only in_season === true rows get a rank,
// but every scored row stays in the denominator; no peers -> no rank.
let selRanks = new Map();

function recomputeSelectionRanks() {
  selRanks = new Map();
  if (state.region === "All" || isLeaf(state.region)) return;
  const rows = visibleScores();
  const scored = rows.filter(r => r.score != null).map(r => r.score);
  rows.forEach(r => {
    if (r.score == null || r.in_season !== true) return;
    const others = scored.slice();
    others.splice(others.indexOf(r.score), 1);   // never in its own denominator
    if (!others.length) return;
    const pct = Math.round(others.filter(s => s < r.score).length / others.length * 100);
    selRanks.set(r.key, { value: pct, grade: letterFor(pct) });
  });
}

// Which number a row shows, given the region selection: absolute overall
// globally, backend within-region percentile for a leaf region, client-ranked
// within-selection percentile for a parent region.
export function displayValue(row) {
  const rel = state.region !== "All";
  let value, grade;
  if (!rel) {
    value = row.score; grade = row.grade;
  } else if (isLeaf(state.region)) {
    value = row.region_score; grade = row.region_grade;
  } else {
    const r = selRanks.get(row.key);
    value = r ? r.value : null; grade = r ? r.grade : null;
  }
  return {
    rel,
    value: value ?? null,
    grade: (grade && grade !== "N/A") ? grade : "—",
    status: row.status || "live",
  };
}
