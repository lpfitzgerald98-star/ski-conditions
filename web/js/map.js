// The map: MapLibre GL with keyless OpenFreeMap vector tiles, and one focusable
// HTML marker per mountain.
//
// Why HTML markers and not a GeoJSON symbol layer: symbol layers render to the
// WebGL canvas, so they can't take keyboard focus or expose ARIA, and they can't
// do the per-pin recolor fade. DOM markers keep all three -- and at 79 pins the
// perf cost is nil. Each marker is a <button> wrapping the shared SVG badge, so
// grade reads as color + shape + letter and Tab walks the pins in order.

import { MAP_STYLE, MAP_START, MARKER_SIZE } from "./config.js";
import { badgeSVG, shapeFor, colorFor, naColor } from "./grades.js";
import { state, visibleScores, displayValue, setSelected } from "./state.js";
import { prefersReducedMotion } from "./a11y.js";

let map = null;
let markers = {};      // key -> { marker, el }
let tooltipEl = null;

export function initMap(onSelect) {
  map = new maplibregl.Map({
    container: "map",
    style: MAP_STYLE,
    center: MAP_START.center,
    zoom: MAP_START.zoom,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.keyboard.enable();
  tooltipEl = document.getElementById("map-tooltip");
  map._onSelect = onSelect;
  return map;
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

export function renderMarkers() {
  Object.values(markers).forEach(({ marker }) => marker.remove());
  markers = {};
  visibleScores().forEach(row => {
    if (row.latitude == null || row.longitude == null) return;
    const d = displayValue(row);
    const st = markerState(row, d);
    const el = makeMarkerEl(row, d);
    const marker = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat([row.longitude, row.latitude])
      .addTo(map);
    markers[row.key] = { marker, el };
  });
  markSelected(state.selected);
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
  if (prefersReducedMotion()) map.jumpTo(opts); else map.easeTo({ ...opts, duration: 600 });
}

export function fitAll() {
  const pts = state.scores.filter(m => m.latitude != null);
  fitBounds(pts);
}
export function fitRegion() {
  fitBounds(visibleScores().filter(m => m.latitude != null));
}
function fitBounds(pts) {
  if (!pts.length) return;
  const b = new maplibregl.LngLatBounds();
  pts.forEach(m => b.extend([m.longitude, m.latitude]));
  map.fitBounds(b, { padding: 60, maxZoom: 8, duration: prefersReducedMotion() ? 0 : 700 });
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
function hideTooltip() { if (tooltipEl) tooltipEl.hidden = true; }
