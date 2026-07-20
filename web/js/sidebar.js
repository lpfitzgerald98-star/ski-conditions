// The leaderboard: one flat list, best first, ranked by the current display value.
// Keyboard-navigable as a roving-tabindex listbox (Up/Down/Home/End move, Enter or
// Space selects), which is what makes the whole roster reachable without a mouse.

import { badgeSVG } from "./grades.js";
import { state, visibleScores, displayValue } from "./state.js";

let listEl = null;
let onSelect = () => {};

export function initSidebar(selectHandler) {
  onSelect = selectHandler;
  listEl = document.getElementById("leaderboard");
  listEl.addEventListener("click", e => {
    const li = e.target.closest(".item");
    if (li) onSelect(li.dataset.key);
  });
  listEl.addEventListener("keydown", onKeydown);
}

// Tier before score: an in-season mountain outranks an unknown one, which outranks
// a known-bare one. A percentile can't see that a whole region is off-season, so
// without this a dead-station July mountain sorts above ones we know are skiing.
function tier(m) { return m.in_season === true ? 0 : (m.in_season == null ? 1 : 2); }

function sortRows(rows) {
  const disp = {};
  rows.forEach(m => { disp[m.key] = displayValue(m); });
  return rows.slice().sort((a, b) => {
    if (tier(a) !== tier(b)) return tier(a) - tier(b);
    const va = disp[a.key].value, vb = disp[b.key].value;
    if (va == null && vb == null) return a.name.localeCompare(b.name);
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va !== vb) return vb - va;
    // The primary display value saturates (skiability's powder curve tops out
    // near 24"+, so several mountains can all land at exactly 100) -- break the
    // tie with global_score, a non-saturating cross-mountain measure, instead of
    // silently falling through to array order (which happened to be alphabetical
    // and made a mid-pack mountain look like an unambiguous #1). Falls back to
    // name so the order is always deterministic, never array-position luck.
    const ga = a.global_score, gb = b.global_score;
    if (ga != null && gb != null && ga !== gb) return gb - ga;
    return a.name.localeCompare(b.name);
  });
}

function subtitle(m) {
  if (m.in_season === false) return `off-season${m.cover_depth != null ? ` · ${m.cover_depth}" base` : ""}`;
  if (m.in_season == null) return "no recent station data";
  return (m.season_grade ? "season " + m.season_grade : "no data")
       + (m.base_grade && m.base_grade !== "N/A" ? " · base " + m.base_grade : "");
}

function itemHTML(m, rank) {
  const d = displayValue(m);
  const st = d.status !== "live" ? "stale"
    : m.in_season === false ? "off"
    : m.in_season == null ? "unknown" : "live";
  const face = st === "off" ? { l: "—", g: "—" }
    : st === "unknown" ? { l: "?", g: "?" }
    : d.grade === "—" || d.value == null ? { l: "·", g: "—" }
    : { l: d.grade, g: d.grade };
  const dim = st === "off" || st === "unknown" ? " is-dim" : "";
  const rankLabel = d.value == null ? "–" : rank;
  return `<li class="item${dim}" role="option" data-key="${m.key}" tabindex="-1"
      aria-selected="${state.selected === m.key}">
    <span class="rank" aria-hidden="true">${rankLabel}</span>
    <span class="badge">${badgeSVG(face.l, face.g, { size: 30 })}</span>
    <span class="nm">
      <b>${escapeHTML(m.name)}${m.alert ? '<span class="alert-chip" aria-label="storm alert">⚠</span>' : ""}</b>
      <small>${subtitle(m)}</small>
    </span>
    <span class="ctry" title="${m.country || ""}">${m.country_code || "—"}</span>
  </li>`;
}

// The leaderboard follows the map: it lists only mountains whose pin is inside the
// current viewport (state.inViewKeys, set on every map move). Null = show all
// (before the map has settled once).
function inView(rows) {
  return state.inViewKeys ? rows.filter(m => state.inViewKeys.has(m.key)) : rows;
}

export function renderList() {
  const inRegion = visibleScores();
  const rows = sortRows(inView(inRegion));
  const note = document.getElementById("rank-note");
  const shown = rows.length, total = inRegion.length;
  if (shown === total) {
    note.textContent = state.region === "All"
      ? `${total} mountains by overall score`
      : `within ${state.region}: best = A, worst = F`;
  } else {
    note.textContent = `${shown} in view of ${total}` +
      (state.region === "All" ? "" : ` in ${state.region}`);
  }

  listEl.setAttribute("role", "listbox");
  listEl.innerHTML = rows.length
    ? rows.map((m, i) => itemHTML(m, i + 1)).join("")
    : `<li class="placeholder">No mountains ${state.inViewKeys ? "in view — zoom out or drag the map" : "in this region"}.</li>`;

  // Roving tabindex: exactly one item is Tab-reachable; arrows move focus among them.
  const items = [...listEl.querySelectorAll(".item")];
  const active = items.find(li => li.dataset.key === state.selected) || items[0];
  if (active) active.tabIndex = 0;
  markSelected(state.selected);
}

export function markSelected(key) {
  if (!listEl) return;
  listEl.querySelectorAll(".item").forEach(li => {
    const sel = li.dataset.key === key;
    li.setAttribute("aria-selected", String(sel));
    li.setAttribute("aria-current", String(sel));
  });
}

// Scroll a selected row into view (e.g. when selection came from the map).
export function revealSelected(key) {
  const li = listEl?.querySelector(`.item[data-key="${CSS.escape(key)}"]`);
  if (li) li.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function onKeydown(e) {
  const items = [...listEl.querySelectorAll(".item")];
  if (!items.length) return;
  const cur = document.activeElement.closest?.(".item");
  let idx = items.indexOf(cur);
  if (e.key === "ArrowDown") idx = Math.min(items.length - 1, idx + 1);
  else if (e.key === "ArrowUp") idx = Math.max(0, idx - 1);
  else if (e.key === "Home") idx = 0;
  else if (e.key === "End") idx = items.length - 1;
  else if (e.key === "Enter" || e.key === " ") {
    if (cur) { e.preventDefault(); onSelect(cur.dataset.key); }
    return;
  } else return;
  e.preventDefault();
  items.forEach(li => (li.tabIndex = -1));
  const next = items[idx];
  next.tabIndex = 0;
  next.focus({ preventScroll: false });
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
