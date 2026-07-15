// The map: MapLibre GL with keyless OpenFreeMap vector tiles, and one focusable
// HTML marker per mountain.
//
// Why HTML markers and not a GeoJSON symbol layer: symbol layers render to the
// WebGL canvas, so they can't take keyboard focus or expose ARIA, and they can't
// do the per-pin recolor fade. DOM markers keep all three -- and at ~110 pins the
// perf cost is nil. Each marker is a <button> wrapping the shared SVG badge, so
// grade reads as color + shape + letter and Tab walks the pins in order.

import {
  MAP_STYLE, MAP_START, MAP_MIN_ZOOM, MAP_MAX_ZOOM,
  CLUSTER_PX, CLUSTER_MAX_ZOOM, MARKER_SIZE,
} from "./config.js";
import { badgeSVG, shapeFor, colorFor, naColor } from "./grades.js";
import { state, visibleScores, displayValue, setSelected, emit } from "./state.js";
import { leafSetOf } from "./regions.js";
import { prefersReducedMotion } from "./a11y.js";

// Every camera move THIS code drives carries this tag in its event data, so the
// zoom-out auto-clear can tell our framing (fitRegion's padding routinely shows
// out-of-region pins -- that must not self-clear the filter) from the user's own
// gestures (which are exactly what should clear it).
const PROGRAMMATIC = { programmatic: true };

let map = null;
let markers = {};      // key -> { marker, el }
let tooltipEl = null;

export function initMap(onSelect, onViewAll) {
  map = new maplibregl.Map({
    container: "map",
    style: MAP_STYLE,
    center: MAP_START.center,
    zoom: MAP_START.zoom,
    minZoom: MAP_MIN_ZOOM,
    maxZoom: MAP_MAX_ZOOM,
    attributionControl: { compact: true },
    dragRotate: false,          // a 2D data map -- rotating/tilting only disorients
    pitchWithRotate: false,
    renderWorldCopies: true,
  });

  // Kill every gesture that tilts or spins the map: the #1 "I can't control this
  // map" complaint is accidentally rotating it and not knowing how to get back.
  map.touchZoomRotate.disableRotation();
  map.touchPitch.disable();
  map.keyboard.enable();
  map.scrollZoom.setWheelZoomRate(1 / 260);   // gentler, less jumpy wheel zoom

  // Zoom buttons (no compass -- there's no rotation to reset) + a one-tap "frame
  // everything" control so you're never lost after zooming into one mountain.
  map.addControl(new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }), "top-right");
  // "View all mountains": the standard global view. Prefer the full reset callback
  // (clears region/selection/card, then frames everything); fall back to a bare fit.
  map.addControl(new FitAllControl(onViewAll || (() => fitAll())), "top-right");

  // Declutter the world view: badges shrink when zoomed out (so ~110 pins don’t
  // pile up) and grow back as you zoom in. Driven by a CSS var on #map.
  const applyMarkerScale = () => {
    const z = map.getZoom();
    const s = Math.max(0.62, Math.min(1, (z - MAP_MIN_ZOOM) / (6 - MAP_MIN_ZOOM) * 0.7 + 0.62));
    map.getContainer().style.setProperty("--marker-scale", s.toFixed(3));
  };
  map.on("zoom", applyMarkerScale);
  map.on("load", applyMarkerScale);

  // Re-cluster after every pan/zoom settles. moveend (not move) fires once per
  // gesture, so markers pan smoothly during a drag and only regroup when it stops.
  map.on("moveend", scheduleRecluster);

  // Zooming/panning OUT past the active region's extent auto-clears the filter,
  // so the pins that just came into view are actually selectable.
  map.on("moveend", maybeAutoClearRegion);

  tooltipEl = document.getElementById("map-tooltip");
  map._onSelect = onSelect;
  return map;
}

// A small on-map button that frames all pins -- MapLibre custom control.
class FitAllControl {
  constructor(onClick) { this._onClick = onClick; }
  onAdd() {
    const c = document.createElement("div");
    c.className = "maplibregl-ctrl maplibregl-ctrl-group";
    const b = document.createElement("button");
    b.type = "button";
    b.title = "View all mountains";
    b.setAttribute("aria-label", "View all mountains (reset to global view)");
    b.innerHTML = '<span aria-hidden="true" style="font-size:15px;line-height:29px">⤢</span>';
    b.addEventListener("click", this._onClick);
    c.appendChild(b);
    this._c = c;
    return c;
  }
  onRemove() { this._c.remove(); }
}

// One marker's classification: stale (cached, awaiting live) / off (off-season) /
// unknown (no cover evidence) / live. Off and unknown never show a colored letter.
function markerState(row, d) {
  if (d.status !== "live") return "stale";
  if (row.in_season === false) return "off";
  if (row.in_season == null) return "unknown";
  return "live";
}
function markerFace(row, d, st) {
  if (st === "off") return { label: "—", grade: "—" };
  if (st === "unknown") return { label: "?", grade: "?" };
  if (d.grade === "—" || d.value == null) return { label: "·", grade: "—" };
  return { label: d.grade, grade: d.grade };
}

function ariaFor(row, d, st, face) {
  const where = st === "off" ? "off-season"
    : st === "unknown" ? "no recent data"
    : st === "stale" ? `${face.grade}, updating`
    : `grade ${face.grade}`;
  const rank = d.rel && row.region_score != null ? `, ${row.region_score} percentile in region` : "";
  return `${row.name}: ${where}${rank}${row.alert ? ", storm alert" : ""}`;
}

function makeMarkerEl(row, d) {
  const st = markerState(row, d);
  const face = markerFace(row, d, st);
  const el = document.createElement("button");
  el.type = "button";
  el.className = "marker";
  el.dataset.key = row.key;
  paint(el, row, d, st, face);

  el.addEventListener("click", () => map._onSelect(row.key));
  el.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); map._onSelect(row.key); }
  });
  const show = () => showTooltip(row, d);
  el.addEventListener("mouseenter", show);
  el.addEventListener("focus", show);
  el.addEventListener("mouseleave", hideTooltip);
  el.addEventListener("blur", hideTooltip);
  return el;
}

// Paint (or repaint) a marker element. When the shape is unchanged we mutate the
// existing fill + label so CSS can fade it (the stale->live effect); when the tier
// changes we rebuild, which snaps -- acceptable and uncommon.
function paint(el, row, d, st, face) {
  const shape = shapeFor(face.grade);
  const existing = el.querySelector(".badge-fill");
  if (existing && el.dataset.shape === shape) {
    const fill = face.grade === "—" || face.grade === "?" ? naColor() : colorFor(face.grade);
    existing.setAttribute("fill", fill);
    const text = el.querySelector("text");
    if (text) text.textContent = face.label;
  } else {
    el.innerHTML = badgeSVG(face.label, face.grade, { size: MARKER_SIZE, alert: row.alert });
    el.dataset.shape = shape;
  }
  el.classList.toggle("is-stale", st === "stale");
  el.classList.toggle("is-alert", !!row.alert);
  el.setAttribute("aria-label", ariaFor(row, d, st, face));
  el.dataset.state = st;
}

let reclusterQueued = false;

// Group the visible pins by screen proximity at the current zoom: nearby pins
// become one count bubble (click/Enter to zoom in and expand), lone pins render
// as their normal grade badge. This is what makes a ~110-pin global map navigable
// instead of a pile of overlapping badges over the US West and NZ.
//
// Past CLUSTER_MAX_ZOOM, clustering is OFF -- every pin stands alone so you can
// always zoom in far enough to read every individual rating.
function clusterPoints(rows) {
  if (map.getZoom() >= CLUSTER_MAX_ZOOM) {
    return rows.map(row => ({ members: [row] }));
  }
  const groups = [];
  rows.forEach(row => {
    const p = map.project([row.longitude, row.latitude]);
    let best = null, bestDist = CLUSTER_PX;
    for (const g of groups) {
      const dist = Math.hypot(g.px - p.x, g.py - p.y);
      if (dist <= bestDist) { best = g; bestDist = dist; }
    }
    if (best) {
      best.members.push(row);
      best.sx += p.x; best.sy += p.y;
      best.px = best.sx / best.members.length;
      best.py = best.sy / best.members.length;
    } else {
      groups.push({ members: [row], sx: p.x, sy: p.y, px: p.x, py: p.y });
    }
  });
  groups.forEach(g => { g.center = map.unproject([g.px, g.py]); });
  return groups;
}

export function renderMarkers() {
  // map.project() is available as soon as the map is constructed (it uses the
  // transform, not the tiles), so we can cluster + place markers before the style
  // finishes loading -- no need to gate on it.
  if (!map) return;
  hideTooltip();
  Object.values(markers).forEach(({ marker }) => marker.remove());
  markers = {};

  clusterPoints(visibleScores().filter(m => m.latitude != null && m.longitude != null))
    .forEach((g, i) => {
      if (g.members.length === 1) {
        const row = g.members[0];
        const d = displayValue(row);
        const el = makeMarkerEl(row, d);
        const marker = new maplibregl.Marker({ element: el, anchor: "center" })
          .setLngLat([row.longitude, row.latitude]).addTo(map);
        markers[row.key] = { marker, el };
      } else {
        const el = makeClusterEl(g);
        const marker = new maplibregl.Marker({ element: el, anchor: "center" })
          .setLngLat(g.center).addTo(map);
        markers[`cluster-${i}`] = { marker, el };
      }
    });
  markSelected(state.selected);
}

function scheduleRecluster() {
  if (reclusterQueued || !map) return;
  reclusterQueued = true;
  requestAnimationFrame(() => {
    reclusterQueued = false;
    renderMarkers();
    emit("viewport", viewportKeys());   // let the leaderboard follow the map
  });
}

// Clear the region filter when the user zooms/pans OUT past the active region.
//
// Evaluated only on user-initiated moves; our own camera calls carry
// PROGRAMMATIC, and each one also re-baselines `regionFrameZoom` -- the zoom at
// which the app last framed the view. That baseline is the key: fitRegion shows
// the WHOLE region (often with out-of-region pins in the padding), so any test
// phrased purely against the region's bbox fires on the very first user gesture,
// even a zoom IN. "Out" therefore means out relative to where we framed you.
//
// A user move clears the filter when out-of-selection mountains are in view --
// tested against the active region's own member set, never region vs region, so
// overlapping/hierarchical regions can't confuse it -- AND either:
//
//   a. the camera has zoomed OUT below the framing baseline (past a small
//      dead-band, so a wheel twitch doesn't count), or
//   b. no member mountain is left in view at all (panned clean away).
//
// Zooming further IN can never fire (a); a pan that keeps members on screen
// never fires (b). The clear itself (state change, re-render, toast) lives in
// main.js, wired to the "region-autoclear" event -- this module only detects.
let regionFrameZoom = null;

function maybeAutoClearRegion(e) {
  if (!map) return;
  if (e.programmatic) { regionFrameZoom = map.getZoom(); return; }
  if (state.region === "All") return;

  const b = map.getBounds();
  const leaves = leafSetOf(state.region);
  let memberVisible = false, outsideVisible = false;
  state.scores.forEach(m => {
    if (m.latitude == null || m.longitude == null) return;
    if (!b.contains([m.longitude, m.latitude])) return;
    if (leaves.has(m.region)) memberVisible = true; else outsideVisible = true;
  });
  if (!outsideVisible) return;

  const zoomedOut = regionFrameZoom != null && map.getZoom() < regionFrameZoom - 0.25;
  if (zoomedOut || !memberVisible) emit("region-autoclear", state.region);
}

// The keys of the mountains currently inside the map viewport -- what the sidebar
// narrows to, so the leaderboard shows only what you're looking at.
function viewportKeys() {
  const b = map.getBounds();
  const keys = new Set();
  visibleScores().forEach(m => {
    if (m.latitude != null && m.longitude != null &&
        b.contains([m.longitude, m.latitude])) {
      keys.add(m.key);
    }
  });
  return keys;
}

// A cluster bubble: a focusable count badge that zooms to fit its members.
function makeClusterEl(g) {
  const n = g.members.length;
  const el = document.createElement("button");
  el.type = "button";
  el.className = "cluster-marker";
  const size = Math.round(30 + Math.min(n, 24) * 0.8);
  el.style.width = el.style.height = `${size}px`;
  // A slice ring hints how many are in-season (skiable) vs not, without color-coding.
  const skiable = g.members.filter(m => m.in_season === true).length;
  el.style.setProperty("--skiable", (n ? skiable / n : 0).toFixed(3));
  el.innerHTML = `<span class="cnt">${n}</span>`;
  const regions = [...new Set(g.members.map(m => m.region))];
  el.setAttribute("aria-label",
    `Cluster of ${n} mountains${regions.length === 1 ? ` in ${regions[0]}` : ""}, ${skiable} in season. Activate to zoom in.`);
  // Zoom past CLUSTER_MAX_ZOOM so even a tight bundle comes fully apart into
  // individual badges rather than re-forming a smaller bubble.
  const zoomIn = () => fitBounds(g.members, CLUSTER_MAX_ZOOM + 2);
  el.addEventListener("click", zoomIn);
  el.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); zoomIn(); }
  });
  el.addEventListener("mouseenter", () => showClusterTip(g));
  el.addEventListener("focus", () => showClusterTip(g));
  el.addEventListener("mouseleave", hideTooltip);
  el.addEventListener("blur", hideTooltip);
  return el;
}

// Recolor one marker in place as its live update lands -- the whole point of the
// stream. Never touches the other 78.
export function updateMarker(row) {
  const hit = markers[row.key];
  if (!hit) return;                       // filtered out of the current region view
  const d = displayValue(row);
  const st = markerState(row, d);
  paint(hit.el, row, d, st, markerFace(row, d, st));
}

export function markSelected(key) {
  Object.entries(markers).forEach(([k, { el }]) =>
    el.setAttribute("aria-pressed", String(k === key)));
}

// Center on a mountain, offsetting left so the pin clears the detail card.
export function flyToMountain(key) {
  const row = state.byKey[key];
  if (!row || row.latitude == null) return;
  const zoom = Math.max(map.getZoom(), 4.5);
  const opts = { center: [row.longitude, row.latitude], zoom, offset: [-150, 0] };
  if (prefersReducedMotion()) map.jumpTo(opts, PROGRAMMATIC);
  else map.easeTo({ ...opts, duration: 600 }, PROGRAMMATIC);
}

export function fitAll() {
  const pts = state.scores.filter(m => m.latitude != null);
  fitBounds(pts);
}
export function fitRegion() {
  fitBounds(visibleScores().filter(m => m.latitude != null));
}
function fitBounds(pts, maxZoom = 8) {
  if (!pts.length) return;
  const run = () => {
    const b = new maplibregl.LngLatBounds();
    pts.forEach(m => b.extend([m.longitude, m.latitude]));
    // Extra left padding so pins clear the detail card; less on small screens.
    const wide = window.innerWidth > 780;
    map.fitBounds(b, {
      padding: { top: 50, bottom: 50, left: 50, right: wide ? 90 : 50 },
      maxZoom, duration: prefersReducedMotion() ? 0 : 700,
    }, PROGRAMMATIC);
  };
  // Fitting before the style loads throws; defer to first load in that case.
  if (map.isStyleLoaded()) run(); else map.once("load", run);
}

// --- tooltip ---------------------------------------------------------------
function showTooltip(row, d) {
  if (!tooltipEl) return;
  const st = markerState(row, d);
  const note = st === "stale" ? "<br><small>cached — updating…</small>"
    : st === "off" ? `<br><small>off-season${row.cover_depth != null ? ` · ${row.cover_depth}" base` : ""}</small>`
    : st === "unknown" ? "<br><small>no recent station data</small>"
    : d.rel && row.region_score != null ? `<br><small>${row.region_score}th percentile in ${row.region}</small>` : "";
  const grade = st === "off" || st === "unknown" ? "" : `${d.grade}${d.rel ? " (in region)" : ""}`;
  tooltipEl.innerHTML = `<b>${row.name}</b>${grade ? "<br>" + grade : ""}${row.alert ? " · ⚠ storm" : ""}${note}`;
  const p = map.project([row.longitude, row.latitude]);
  tooltipEl.style.left = `${p.x}px`;
  tooltipEl.style.top = `${p.y - MARKER_SIZE * 0.7}px`;
  tooltipEl.hidden = false;
}
function showClusterTip(g) {
  if (!tooltipEl) return;
  const names = g.members.slice(0, 6).map(m => m.name);
  const more = g.members.length - names.length;
  tooltipEl.innerHTML = `<b>${g.members.length} mountains</b><br>` +
    `<small>${names.join(", ")}${more > 0 ? ` +${more} more` : ""}<br>click to zoom in</small>`;
  const p = map.project(g.center);
  tooltipEl.style.left = `${p.x}px`;
  tooltipEl.style.top = `${p.y - 26}px`;
  tooltipEl.hidden = false;
}

function hideTooltip() { if (tooltipEl) tooltipEl.hidden = true; }
