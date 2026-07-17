// Boot + wiring. Loads the grade scale and the first roster, builds the map,
// sidebar and card, and connects the controls. In static mode the roster comes
// from the daily snapshot; in live mode the SSE stream drives the same handlers.

import { LIVE } from "./config.js";
import { setScale, badgeSVG, LEGEND_GRADES, naColor } from "./grades.js";
import { loadGrades, loadMeta, loadScores, loadHistoryIndex, loadHistoryDate } from "./api.js";
import {
  state, on, setScores, upsertRow, setRegion, setSelected, setInView, visibleScores,
} from "./state.js";
import { announce } from "./a11y.js";
import { setRegionTree, optionsList } from "./regions.js";
import * as sse from "./sse.js";
import {
  initMap, renderMarkers, updateMarker, markSelected as mapMarkSelected, flyToMountain, fitAll, fitRegion,
} from "./map.js";
import {
  initSidebar, renderList, markSelected as listMarkSelected, revealSelected,
} from "./sidebar.js";
import { initCard, openCard, close as closeCard } from "./card.js";

const $ = id => document.getElementById(id);
let renderListQueued = false;
let framedOnce = false;
let histIndex = null;   // retrospective-history manifest (static mode)

// Frame the whole roster the first time data lands, so the map opens on all the
// pins instead of an arbitrary start view. Later profile/region changes manage
// their own framing (or leave the camera where the user put it).
function frameOnce() {
  if (framedOnce || !state.scores.length) return;
  framedOnce = true;
  fitAll();
}

// -- selection -------------------------------------------------------------
function select(key) {
  setSelected(key);
  mapMarkSelected(key);
  listMarkSelected(key);
  revealSelected(key);
  flyToMountain(key);
  openCard(key, { network: LIVE && $("network").checked });
}

// Return to the standard global view: clear the region filter and any selection,
// close the detail card, and frame every mountain. The "back to all" reset behind
// both the sidebar button and the on-map ⤢ control.
function viewAll() {
  closeCard();
  setSelected(null);
  setRegion("All");
  $("region").value = "All";
  renderMarkers();
  renderList();
  updateTagline();
  fitAll();
}

// -- retrospective history (static mode) -----------------------------------
// Wire the date picker to the prebuilt per-date files. Picking a past date shows
// the roster scored as of that day, forward-window snow included; picking today
// (or the max) returns to the live/snapshot view.
function setupHistory(idx) {
  histIndex = idx;
  const asof = $("asof");
  asof.disabled = false;
  asof.min = idx.min;
  asof.max = state.meta?.as_of || idx.max;
  asof.title = "Pick a past date to see its score (history back to " + idx.min + ")";
}

async function onAsOfChange() {
  if (LIVE) { loadProfile(state.profile); return; }
  if (!histIndex) return;
  const d = $("asof").value;
  const today = state.meta?.as_of;
  if (!d || (today && d >= today)) return exitHistory();
  if (d < histIndex.min) { $("asof").value = histIndex.min; }
  await enterHistory($("asof").value);
}

async function enterHistory(d) {
  // History is built for one profile (dynamic), so the selector adds nothing here
  // -- hide it (the "As of" field widens to fill). Restored in exitHistory.
  $("profile").value = histIndex.profile;
  $("profile-field").hidden = true;
  state.profile = histIndex.profile;
  try {
    const rows = await loadHistoryDate(d);
    state.histDate = d;
    setScores(rows);
    populateRegions();
    renderMarkers();
    renderList();
    updateTagline();
    if (state.selected) openCard(state.selected);
    announce(`Showing scores as of ${d}.`);
  } catch {
    $("tagline").textContent = `no history for ${d}`;
  }
}

async function exitHistory() {
  if (state.histDate == null) return;
  state.histDate = null;
  $("profile-field").hidden = false;
  await loadProfile(state.profile);
}

// -- roster load (static) or stream (live) ---------------------------------
async function loadProfile(profile) {
  state.profile = profile;
  if (LIVE) {
    sse.start({ profile, asof: $("asof").value });
    return;                    // snapshot/update events populate the roster
  }
  const rows = await loadScores(profile);
  setScores(rows);
  populateRegions();
  renderMarkers();
  renderList();
  updateTagline();
  frameOnce();
}

// -- regions ---------------------------------------------------------------
// The picker walks the hierarchy depth-first, indenting children under their
// parents; any level is selectable, and picking a parent includes everything
// under it. With no tree in meta (old snapshot), optionsList degrades to the
// flat leaf list this control always showed.
function populateRegions() {
  const sel = $("region");
  const opts = optionsList(state.scores);
  const cur = state.region;
  sel.innerHTML = '<option value="All">All regions (global)</option>' +
    opts.map(o =>
      `<option value="${o.id}">${"  ".repeat(o.depth)}${o.id} (${o.count})</option>`
    ).join("");
  sel.value = opts.some(o => o.id === cur) || cur === "All" ? cur : "All";
  state.region = sel.value;
}

// -- toast -------------------------------------------------------------------
// One transient notice at a time (currently only the filter auto-clear).
let toastTimer = null;
function showToast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

// -- tagline ---------------------------------------------------------------
function updateTagline() {
  const vis = visibleScores();
  const scored = vis.filter(m => m.score != null).length;
  const where = state.region === "All" ? `${vis.length} mountains` : `${state.region} · ${vis.length} resorts`;
  let feed, asOf = state.meta?.as_of || "";
  if (state.histDate) {
    feed = "historical · snow-based";
    asOf = state.histDate;
  } else if (LIVE) {
    const live = vis.filter(m => m.status === "live").length;
    feed = state.complete ? "live" : `going live… ${live}/${vis.length}`;
  } else {
    feed = "daily snapshot";
  }
  $("tagline").textContent = `${where} · ${scored} with data · as of ${asOf} · ${feed}`;
}

// Batch list re-renders during a burst of stream updates (one per animation frame).
function queueList() {
  if (renderListQueued) return;
  renderListQueued = true;
  requestAnimationFrame(() => { renderListQueued = false; renderList(); updateTagline(); });
}

// -- legend ----------------------------------------------------------------
function buildLegend() {
  const el = $("legend");
  el.innerHTML = LEGEND_GRADES.map(g =>
    `<span class="lg">${badgeSVG(g, g, { size: 18 })}${g}</span>`).join("") +
    `<span class="lg">${badgeSVG("—", "—", { size: 18 })}off-season / no data</span>`;
}

// -- theme -----------------------------------------------------------------
function initTheme() {
  const saved = localStorage.getItem("ski-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  syncThemeButton();
  $("theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const now = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", now);
    localStorage.setItem("ski-theme", now);
    syncThemeButton();
  });
}
function syncThemeButton() {
  const dark = document.documentElement.getAttribute("data-theme") !== "light";
  const btn = $("theme-toggle");
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}

// -- SSE wiring (live mode only) -------------------------------------------
function wireStream() {
  on("sse:snapshot", data => {
    state.complete = false;
    state.meta = { ...state.meta, as_of: data.as_of };
    setScores(data.mountains);
    populateRegions();
    renderMarkers();
    renderList();
    updateTagline();
    frameOnce();
    announce(`Snapshot loaded: ${data.mountains.length} mountains. Live data updating.`);
  });
  on("sse:update", row => { upsertRow(row); updateMarker(row); queueList(); });
  on("sse:complete", data => {
    state.complete = true;
    setScores(data.mountains);
    renderMarkers();
    renderList();
    updateTagline();
    announce(`Live update complete: ${data.live} of ${data.total} mountains current.`);
    if (state.selected) openCard(state.selected, { network: $("network").checked });
  });
  on("sse:reconnecting", ({ attempt, delay }) => {
    $("tagline").textContent = `reconnecting… (attempt ${attempt} in ${Math.round(delay / 1000)}s)`;
    announce("Live connection dropped; reconnecting.");
  });
  on("sse:failed", () => {
    $("tagline").textContent = "live connection lost — showing last data";
    announce("Live connection lost. Showing the last received data.");
  });
}

// -- controls --------------------------------------------------------------
function wireControls() {
  $("profile").addEventListener("change", e => loadProfile(e.target.value));
  $("region").addEventListener("change", e => {
    setRegion(e.target.value);
    renderMarkers();
    renderList();
    updateTagline();
    if (state.region !== "All") fitRegion();
    announce(`Showing ${state.region === "All" ? "all regions" : state.region}.`);
  });
  $("fitall").addEventListener("click", viewAll);
  $("asof").addEventListener("change", onAsOfChange);

  // The map detected a zoom/pan out past the active region (map.js
  // maybeAutoClearRegion): drop the filter so the newly visible pins are
  // selectable, and say so -- a filter that vanishes silently reads as a bug.
  on("region-autoclear", region => {
    setRegion("All");
    $("region").value = "All";
    renderMarkers();
    renderList();
    updateTagline();
    showToast(`Region filter cleared — zoomed out past ${region}`);
    announce(`Region filter cleared: zoomed out past ${region}. Showing all mountains.`);
  });

  // The map detected a zoom-out past the open card's framing zoom (map.js
  // maybeDismissCard): close the scorecard so the user is back to surveying pins.
  on("card-dismiss", () => { if (state.selected) closeCard(); });

  // Re-render markers when the map finishes moving isn't needed (MapLibre keeps
  // HTML markers pinned), but do keep the selected card in sync on selection.
  on("selected", key => { if (key) { mapMarkSelected(key); listMarkSelected(key); } });

  // The leaderboard follows the map: on every settle, narrow the list to the pins
  // now in view. Guarded so a pan that doesn't change the in-view set doesn't
  // needlessly re-render (which would reset the list's scroll).
  let lastViewSig = null;
  on("viewport", keys => {
    const sig = [...keys].sort().join(",");
    if (sig === lastViewSig) return;
    lastViewSig = sig;
    setInView(keys);
    renderList();
    updateTagline();
  });
}

function initProfiles(meta) {
  const sel = $("profile");
  const labels = { dynamic: "Dynamic (auto)", weekend: "This weekend", month: "This month", season: "Rest of season" };
  sel.innerHTML = meta.profiles.map(p =>
    `<option value="${p}">${labels[p] || p}</option>`).join("");
  const def = meta.default_profile && meta.profiles.includes(meta.default_profile)
    ? meta.default_profile : meta.profiles[0];
  sel.value = def;
  return def;
}

// -- boot ------------------------------------------------------------------
async function boot() {
  initTheme();
  initCard();
  initSidebar(select);
  initMap(select, viewAll);
  if (LIVE) wireStream();

  const [grades, meta] = await Promise.all([loadGrades(), loadMeta()]);
  setScale(grades.colors, grades.na_color, grades.thresholds);
  state.meta = meta;
  setRegionTree(meta.region_tree);
  buildLegend();

  const defaultProfile = initProfiles(meta);

  // The as-of control defaults to the snapshot date. In static mode it's enabled
  // only if retrospective history was built (setupHistory below); in live mode it
  // re-queries the backend. The forecast toggle is live-only.
  const asof = $("asof");
  asof.value = meta.as_of || new Date().toISOString().slice(0, 10);
  if (!LIVE) { asof.disabled = true; asof.title = "Fixed to the daily snapshot"; }
  $("network-field").hidden = !LIVE;

  wireControls();
  await loadProfile(defaultProfile);

  // Retrospective history: if the prebuilt per-date files exist, open up the date
  // picker so you can browse past scores. Loaded after first paint so it never
  // delays the map.
  if (!LIVE) {
    const idx = await loadHistoryIndex();
    if (idx && idx.min) setupHistory(idx);
  }

  // Close the card when clicking empty map space is handled by its own ✕/Esc;
  // nothing else to wire.
}

boot().catch(err => {
  document.getElementById("tagline").textContent = "Failed to load data.";
  console.error(err);
});
