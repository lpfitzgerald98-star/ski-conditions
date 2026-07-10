// Central state + a tiny pub/sub bus. Modules read `state`, mutate it through the
// setters, and subscribe to change events instead of calling each other directly.

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
};

export function setScores(rows) {
  state.scores = rows;
  state.byKey = {};
  rows.forEach(r => { state.byKey[r.key] = r; });
  emit("scores", rows);
}

// Merge one updated row (a live SSE mountain_update) without dropping the rest.
export function upsertRow(row) {
  const i = state.scores.findIndex(r => r.key === row.key);
  if (i >= 0) state.scores[i] = row; else state.scores.push(row);
  state.byKey[row.key] = row;
  emit("row", row);
}

export function setRegion(region) { state.region = region; emit("region", region); }
export function setSelected(key)  { state.selected = key; emit("selected", key); }

// The rows currently on screen, honoring the region filter.
export function visibleScores() {
  return state.region === "All"
    ? state.scores
    : state.scores.filter(m => m.region === state.region);
}

// Which of the two numbers a row shows, given the region selection: absolute
// overall globally, within-region percentile when a region is picked.
export function displayValue(row) {
  const rel = state.region !== "All";
  const value = rel ? row.region_score : row.score;
  const grade = rel ? row.region_grade : row.grade;
  return {
    rel,
    value: value ?? null,
    grade: (grade && grade !== "N/A") ? grade : "—",
    status: row.status || "live",
  };
}
