// Boot + wiring. Loads the grade scale and the first roster, builds the map,
// sidebar and card, and connects the controls. In static mode the roster comes
// from the daily snapshot; in live mode the SSE stream drives the same handlers.

import { LIVE } from "./config.js";
import { setScale, badgeSVG, LEGEND_GRADES, naColor, letterFor } from "./grades.js";
import {
  loadGrades, loadMeta, loadScores, loadHistoryIndex, loadHistoryDate,
  loadTripBaseline, loadTripLive,
} from "./api.js";
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

// -- date picker: today vs past (history) vs future (trip) -----------------
const todayISO = () => state.meta?.as_of || new Date().toISOString().slice(0, 10);
const round1 = v => (v == null ? null : Math.round(v * 10) / 10);
function addDaysISO(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
const daysBetween = (a, b) =>
  Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);

// Open the date picker's bounds: back to the earliest history date (static, if
// built) and forward to the Trip Predictor horizon (whenever meta carries trip
// config -- static and live both do). With neither, it stays fixed to today.
function configureDatePicker() {
  const asof = $("asof");
  const today = todayISO();
  const trip = state.meta?.trip;
  const hasHist = !!(histIndex && histIndex.min);
  if (!trip && !hasHist && !LIVE) {
    asof.disabled = true;
    asof.title = "Fixed to the daily snapshot";
    return;
  }
  asof.disabled = false;
  asof.min = hasHist ? histIndex.min : today;
  asof.max = trip ? addDaysISO(today, trip.max_lead_days) : today;
  asof.title = (hasHist ? "Pick a past date for its historical score, or " : "Pick ")
    + (trip ? "a future date for a trip prediction" : "");
}

// Route a date change to the right mode: future -> trip, past -> history,
// today -> restore the live/snapshot view.
async function onAsOfChange() {
  const d = $("asof").value;
  const today = todayISO();
  if (!d) return;
  if (d > today) return enterTrip(d);
  if (d === today) return restoreToday();
  if (LIVE) { clearFutureState(); loadProfile(state.profile); return; }
  if (!histIndex) return restoreToday();
  const dd = d < histIndex.min ? histIndex.min : d;
  if (dd !== d) $("asof").value = dd;
  await enterHistory(dd);
}

function clearFutureState() { state.tripDate = null; state.tripInfo = null; }

async function restoreToday() {
  clearFutureState();
  state.histDate = null;
  $("profile-field").hidden = false;
  await loadProfile(state.profile);
}

async function enterHistory(d) {
  // History is built for one profile (dynamic), so the selector adds nothing here
  // -- hide it (the "As of" field widens to fill). Restored on return to today.
  clearFutureState();
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

// -- Trip Predictor (future dates) -----------------------------------------
// Blend today's live/global score with the historical baseline for the target
// date, weighting today down as the trip recedes (score.decay_weight on the
// lead-time axis). Static mode blends in the browser off the prebuilt baseline;
// live mode asks the /trip endpoint, which returns the same shape already blended.
function tripGrade(v) { return v == null ? "—" : (letterFor(v) || "—"); }

// Storm alerts (row.alert -- the map pin's pulsing ring, the sidebar's ⚠ chip, the
// tooltip text) are computed live over a 24/72h forecast window (see
// pipeline.forecast_incoming_storms). A trip date beyond that window wouldn't
// include whatever storm triggered it, so carrying today's `alert` straight
// through would flag a storm that's long past (or hasn't happened yet) by the
// time the trip actually occurs. 3 days matches that 72h ceiling.
const TRIP_ALERT_HORIZON_DAYS = 3;

// Overlay a trip score onto the fields the map/list/card already read, and carry
// the breakdown for the card. in_season drives the leaderboard's tier sort, so a
// date with no rankable prediction (off-season / no history) sinks and dims.
function asTripRow(base, { trip_score, historical_baseline, current_score,
                          current_weight, n_years, low_confidence, in_season },
                   leadDays) {
  const g = tripGrade(trip_score);
  return {
    ...base,
    score: trip_score, grade: g,
    region_score: trip_score, region_grade: g,
    global_score: trip_score, regional_score: trip_score,
    in_season: in_season ?? (trip_score != null),
    status: "live",
    alert: leadDays <= TRIP_ALERT_HORIZON_DAYS ? !!base.alert : false,
    trip_score, historical_baseline, current_score,
    current_weight, n_years, low_confidence,
  };
}

function blendTrip(current, baseline, lead, halfLife) {
  const w = Math.pow(0.5, Math.max(0, lead) / halfLife);
  if (current != null && baseline != null) return [round1(w * current + (1 - w) * baseline), w];
  if (baseline != null) return [round1(baseline), w];
  if (current != null) return [lead <= halfLife ? round1(current) : null, w];
  return [null, w];
}

async function computeTrip(d) {
  const today = todayISO();
  const lead = daysBetween(today, d);
  if (LIVE) {
    const data = await loadTripLive(d, state.profile);
    return {
      rows: data.mountains.map(m => asTripRow(m, m, data.lead_days)),
      info: { lead_days: data.lead_days, current_weight: data.current_weight },
    };
  }
  const [baseline, todayRows] = await Promise.all([loadTripBaseline(), loadScores(state.profile)]);
  if (!baseline) throw new Error("no trip baseline");
  const hl = state.meta.trip.half_life_days;
  // MM-DD key; the baseline is built on a non-leap reference year, so fold Feb 29.
  let mmdd = d.slice(5);
  if (mmdd === "02-29") mmdd = "02-28";
  const day = baseline.dates[mmdd] || {};
  const rows = todayRows.map(r => {
    const [bScore, nYears] = day[r.key] || [null, 0];
    const [ts, w] = blendTrip(r.global_score ?? null, bScore, lead, hl);
    return asTripRow(r, {
      trip_score: ts, historical_baseline: bScore, current_score: r.global_score ?? null,
      current_weight: w, n_years: nYears, low_confidence: nYears < 10,
      in_season: ts != null,
    }, lead);
  });
  return { rows, info: { lead_days: lead, current_weight: Math.pow(0.5, Math.max(0, lead) / hl) } };
}

async function enterTrip(d) {
  try {
    const { rows, info } = await computeTrip(d);
    state.histDate = null;
    state.tripDate = d;
    state.tripInfo = info;
    $("profile-field").hidden = false;
    setScores(rows);
    populateRegions();
    renderMarkers();
    renderList();
    updateTagline();
    if (state.selected) openCard(state.selected);
    announce(`Trip prediction for ${d}, ${info.lead_days} days out.`);
  } catch (e) {
    $("tagline").textContent = `no trip prediction for ${d}`;
    console.error(e);
  }
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
  if (state.tripDate) {
    const w = Math.round((state.tripInfo?.current_weight ?? 0) * 100);
    feed = `trip · ${state.tripInfo?.lead_days}d out · ${w}% now / ${100 - w}% history`;
    asOf = state.tripDate;
  } else if (state.histDate) {
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
  $("network-field").hidden = !LIVE;

  wireControls();
  await loadProfile(defaultProfile);

  // Open the date picker after first paint (so it never delays the map): past
  // dates browse the prebuilt history (static only); future dates run a Trip
  // Prediction (both modes, whenever meta carries trip config).
  if (!LIVE) {
    const idx = await loadHistoryIndex();
    if (idx && idx.min) histIndex = idx;
    loadTripBaseline();   // warm the cache; harmless if the file is absent
  }
  configureDatePicker();

  // Close the card when clicking empty map space is handled by its own ✕/Esc;
  // nothing else to wire.
}

boot().catch(err => {
  document.getElementById("tagline").textContent = "Failed to load data.";
  console.error(err);
});
