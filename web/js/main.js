// Boot + wiring. Loads the grade scale and the first roster, builds the map,
// sidebar and card, and connects the controls. In static mode the roster comes
// from the daily snapshot; in live mode the SSE stream drives the same handlers.

import { LIVE } from "./config.js";
import { setScale, badgeSVG, LEGEND_GRADES, naColor } from "./grades.js";
import { loadGrades, loadMeta, loadScores } from "./api.js";
import {
  state, on, setScores, upsertRow, setRegion, setSelected, setInView, visibleScores,
} from "./state.js";
import { announce } from "./a11y.js";
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
function populateRegions() {
  const sel = $("region");
  const regions = [...new Set(state.scores.map(m => m.region))].sort();
  const cur = state.region;
  sel.innerHTML = '<option value="All">All regions (global)</option>' +
    regions.map(r => {
      const n = state.scores.filter(m => m.region === r).length;
      return `<option value="${r}">${r} (${n})</option>`;
    }).join("");
  sel.value = regions.includes(cur) || cur === "All" ? cur : "All";
  state.region = sel.value;
}

// -- tagline ---------------------------------------------------------------
function updateTagline() {
  const vis = visibleScores();
  const scored = vis.filter(m => m.score != null).length;
  const where = state.region === "All" ? `${vis.length} mountains` : `${state.region} · ${vis.length} resorts`;
  let feed;
  if (LIVE) {
    const live = vis.filter(m => m.status === "live").length;
    feed = state.complete ? "live" : `going live… ${live}/${vis.length}`;
  } else {
    feed = "daily snapshot";
  }
  $("tagline").textContent = `${where} · ${scored} with data · as of ${state.meta?.as_of || state.asof || ""} · ${feed}`;
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
  $("fitall").addEventListener("click", () => {
    setRegion("All");
    $("region").value = "All";
    renderMarkers();
    renderList();
    updateTagline();
    fitAll();
  });
  $("asof").addEventListener("change", () => { if (LIVE) loadProfile(state.profile); });

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
  initMap(select);
  if (LIVE) wireStream();

  const [grades, meta] = await Promise.all([loadGrades(), loadMeta()]);
  setScale(grades.colors, grades.na_color);
  state.meta = meta;
  buildLegend();

  const defaultProfile = initProfiles(meta);

  // The as-of control is snapshot-fixed in static mode (there's only one snapshot);
  // it's live and re-queries in backend mode. The forecast toggle is live-only.
  const asof = $("asof");
  asof.value = meta.as_of || new Date().toISOString().slice(0, 10);
  if (!LIVE) { asof.disabled = true; asof.title = "Fixed to the daily snapshot"; }
  $("network-field").hidden = !LIVE;

  wireControls();
  await loadProfile(defaultProfile);

  // Close the card when clicking empty map space is handled by its own ✕/Esc;
  // nothing else to wire.
}

boot().catch(err => {
  document.getElementById("tagline").textContent = "Failed to load data.";
  console.error(err);
});
