// Top picks: one drop-down that ranks every mountain by snow, so a snow alert (or
// anyone opening the app) lands on a shortlist instead of a single mountain.
//
// Four tabs: the last 3 days of snow, and the forecast for the next 24, 48 and 72
// hours. Tapping a mountain closes the panel, flies the map to it and opens its
// card. A notification opens it with ?top=<tab>&hl=<key> (scripts/push_alerts.py);
// hl marks the mountain the alert was about.
//
// Always ranks TODAY's rows: a past (history) or future (trip) date swaps
// state.scores for rows without these fields, so the panel keeps its own copy.

import { state, on, visibleScores } from "./state.js";
import { badgeSVG, colorFor } from "./grades.js";
import { announce } from "./a11y.js";

const $ = id => document.getElementById(id);
const TOP_N = 10;

export const TABS = [
  { id: "recent", label: "Last 3 days", head: "Snow in the last 3 days" },
  { id: "24", label: "Next 24h", head: "Forecast, next 24 hours" },
  { id: "48", label: "48h", head: "Forecast, next 48 hours" },
  { id: "72", label: "72h", head: "Forecast, next 72 hours" },
];

let todayRows = [];
let tab = "recent";
let highlight = null;
let onPick = () => {};

const inches = v => (v == null ? "—" : `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10}"`);

// The number a tab ranks by. An older snapshot (before these fields existed)
// falls back to what it does have, so the panel never comes up empty for that.
function valueFor(m, id) {
  if (id === "recent") return m.fresh_72h ?? m.fresh_24h ?? null;
  const v = m[`forecast_${id}h`];
  if (v !== undefined) return v;
  return id === "72" ? (m.incoming_inches ?? null) : null;
}

function statusNote(m) {
  if (m.preseason_grade) return `opens ~${m.preseason_opens}`;
  if (m.in_season === false) return "off-season";
  if (m.in_season == null) return "no recent data";
  return "";
}

function detailFor(m, id) {
  if (id === "recent") {
    const parts = [["24h", m.fresh_24h], ["48h", m.fresh_48h]].filter(([, v]) => v != null);
    return parts.map(([k, v]) => `${k} ${inches(v)}`).join(" · ");
  }
  const got = TABS.filter(t => t.id !== "recent" && t.id !== id)
    .map(t => [t.id, m[`forecast_${t.id}h`]]).filter(([, v]) => v != null);
  return got.map(([h, v]) => `${h}h ${inches(v)}`).join(" · ");
}

// Today's mountains in the current region, best first. Season-over mountains are
// left out (they won't be skiing whatever falls), and so is anything with no snow.
// The top ten, plus the alert's own mountain at its real rank if it missed the cut.
export function ranked(rows, id, keep = null) {
  const all = rows
    .filter(m => !m.season_over_since)
    .map(m => ({ m, v: valueFor(m, id) }))
    .filter(x => x.v != null && x.v >= 0.5)
    .sort((a, b) => b.v - a.v || a.m.name.localeCompare(b.m.name))
    .map((x, i) => ({ ...x, rank: i + 1 }));
  const top = all.slice(0, TOP_N);
  const extra = keep && all.slice(TOP_N).find(x => x.m.key === keep);
  return extra ? [...top, extra] : top;
}

function regionRows() {
  if (state.region === "All") return todayRows;
  const keys = new Set(visibleScores().map(m => m.key));
  return todayRows.filter(m => keys.has(m.key));
}

function render() {
  const panel = $("picks-panel");
  if (!panel || panel.hidden) return;
  panel.querySelectorAll("[role=tab]").forEach(b => {
    const sel = b.dataset.tab === tab;
    b.setAttribute("aria-selected", String(sel));
    b.tabIndex = sel ? 0 : -1;
  });
  const t = TABS.find(x => x.id === tab);
  const where = state.region === "All" ? "everywhere" : `in ${state.region}`;
  $("picks-sub").textContent = `${t.head} · ${where}`;
  const list = ranked(regionRows(), tab, highlight);
  const ol = $("picks-list");
  ol.innerHTML = list.length ? list.map(({ m, v, rank }) => {
    const note = statusNote(m);
    const detail = detailFor(m, tab);
    const g = m.preseason_grade || (m.grade && m.grade !== "N/A" ? m.grade : "—");
    return `<li><button type="button" class="pick${m.key === highlight ? " is-hl" : ""}" data-key="${m.key}">
      <span class="pick-rank" aria-hidden="true">${rank}</span>
      <span class="pick-badge${m.preseason_grade ? " is-preseason" : ""}" aria-hidden="true"${m.preseason_grade
        ? ` style="--grade-color:${colorFor(g)}"` : ""}>${badgeSVG(g, g, { size: 26 })}</span>
      <span class="pick-nm"><b>${esc(m.name)}</b>
        <small>${[note, detail].filter(Boolean).map(esc).join(" · ") || "&nbsp;"}</small></span>
      <span class="pick-val">${inches(v)}</span>
    </button></li>`;
  }).join("")
    : `<li class="picks-empty">${tab === "recent"
        ? "No new snow in the last 3 days"
        : "No snow in the forecast"} ${where}.</li>`;
}

export function openPicks(id = tab, hl = null) {
  if (TABS.some(t => t.id === id)) tab = id;
  if (hl) highlight = hl;
  const panel = $("picks-panel");
  panel.hidden = false;
  $("picks-toggle").setAttribute("aria-expanded", "true");
  render();
  const mark = panel.querySelector(".pick.is-hl");
  (mark || panel.querySelector("[role=tab][aria-selected=true]")).focus({ preventScroll: true });
  if (mark) mark.scrollIntoView({ block: "nearest" });
  announce(`Top picks: ${TABS.find(t => t.id === tab).head}.`);
}

export function closePicks({ refocus = true } = {}) {
  const panel = $("picks-panel");
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $("picks-toggle").setAttribute("aria-expanded", "false");
  if (refocus) $("picks-toggle").focus({ preventScroll: true });
}

export function initTopPicks(pickHandler) {
  onPick = pickHandler;
  const panel = $("picks-panel");
  panel.querySelector(".picks-tabs").innerHTML = TABS.map(t =>
    `<button type="button" role="tab" data-tab="${t.id}" aria-controls="picks-list">${t.label}</button>`).join("");

  $("picks-toggle").addEventListener("click", () =>
    panel.hidden ? openPicks() : closePicks());
  $("picks-close").addEventListener("click", () => closePicks());

  panel.addEventListener("click", e => {
    const t = e.target.closest("[role=tab]");
    if (t) { tab = t.dataset.tab; render(); return; }
    const p = e.target.closest(".pick");
    if (p) { closePicks({ refocus: false }); onPick(p.dataset.key); }
  });
  // Left/Right moves between tabs, like any tab strip.
  panel.querySelector(".picks-tabs").addEventListener("keydown", e => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const i = TABS.findIndex(t => t.id === tab);
    tab = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length].id;
    render();
    panel.querySelector(`[role=tab][data-tab="${tab}"]`).focus();
    e.preventDefault();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closePicks(); });
  document.addEventListener("pointerdown", e => {
    if (!panel.hidden && !panel.contains(e.target) && !$("picks-toggle").contains(e.target)) {
      closePicks({ refocus: false });
    }
  });

  on("scores", rows => {
    if (!state.histDate && !state.tripDate) todayRows = rows;
    render();
  });
  on("row", row => {
    if (state.histDate || state.tripDate) return;
    const i = todayRows.findIndex(r => r.key === row.key);
    if (i >= 0) todayRows[i] = row;
  });
  on("region", render);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
