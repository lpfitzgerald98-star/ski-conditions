// The detail card: a mountain's full scorecard, shown in a dialog over the map.
// Two scores side by side (absolute overall + within-region percentile) on their
// own curves, then grades, sub-score bars, incoming storms, weather and warnings.

import { loadCard } from "./api.js";
import { colorFor, textOn, badgeSVG, naColor } from "./grades.js";
import { state } from "./state.js";
import { announce, focusSilently } from "./a11y.js";

let el = null;
let lastFocus = null;      // element to restore focus to on close
let reqToken = 0;          // guards against a slow load overwriting a newer one

export function initCard() {
  el = document.getElementById("detail");
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !el.hidden) close();
  });
}

export async function openCard(key, { network = false } = {}) {
  lastFocus = document.activeElement;
  el.hidden = false;
  const row = state.byKey[key];
  // Historical dates have no per-date card file (that'd be 79 x N files); render a
  // compact card straight from the roster row, which already carries the grades.
  if (state.histDate) { el.dataset.key = key; renderHistCard(row, key); return; }
  if (el.dataset.key !== key) {
    el.innerHTML = `<div class="placeholder">Loading ${row ? escapeHTML(row.name) : key}…</div>`;
  }
  el.dataset.key = key;
  const token = ++reqToken;
  try {
    const card = await loadCard(key, { network });
    if (token !== reqToken || state.selected !== key) return;   // superseded
    render(card, key);
  } catch {
    if (token === reqToken) el.innerHTML = '<div class="placeholder">Failed to load.</div>';
  }
}

export function close() {
  if (!el || el.hidden) return;
  el.hidden = true;
  el.dataset.key = "";
  state.selected = null;
  document.querySelectorAll('.item[aria-selected="true"]')
    .forEach(li => li.setAttribute("aria-selected", "false"));
  if (lastFocus) focusSilently(lastFocus);
}

// The region block. Live cards carry `card.region`; static cards don't (region
// rank is cohort-derived), so we build it from the current roster row -- which
// also guarantees the card always agrees with the pin, per profile.
function regionBlock(card, key) {
  if (card.region) return card.region;
  const row = state.byKey[key];
  if (!row) return { name: "region", score: null, grade: null, cohort_size: 1 };
  const cohort = state.scores.filter(m => m.region === row.region && m.score != null).length;
  return { name: row.region, score: row.region_score, grade: row.region_grade,
           cohort_size: cohort || 1 };
}

function scoreTile(label, sublabel, grade, value, valueSuffix) {
  const c = grade ? colorFor(grade) : naColor();
  return `<div class="side">
    <div class="lbl">${label}</div>
    <div class="score-row">
      <span class="badge">${badgeSVG(grade || "—", grade || "—", { size: 34 })}</span>
      <span class="num">${value != null ? value : "—"}<span>${value != null ? valueSuffix : ""}</span></span>
    </div>
    <div class="of">${sublabel}</div>
  </div>`;
}

function duo(card, key) {
  const prof = state.profile || card.default_profile;
  const o = card.overall[prof] || card.overall[card.default_profile] || {};
  const r = regionBlock(card, key);
  const why = card.in_season === false
      ? `off-season${card.cover_depth != null ? ` — ${card.cover_depth}" base` : ""}`
    : card.in_season == null ? "no recent station data"
    : r.cohort_size <= 1 ? `only tracked resort in ${r.name}` : "not enough data";
  const overallTile = scoreTile("Overall", `across all ${card.roster_size || "tracked"} mountains`,
    o.grade, o.score != null ? Math.round(o.score) : null, " / 100");
  const regionTile = r.score == null
    ? `<div class="side"><div class="lbl">Within ${r.name}</div>
         <div class="score-row"><span class="badge">${badgeSVG("—", "—", { size: 34 })}</span>
         <span class="num">—</span></div><div class="of">${why}</div></div>`
    : scoreTile(`Within ${r.name}`, `vs ${r.cohort_size - 1} others in ${r.name}`,
        r.grade, Math.round(r.score), "th pct");
  return `<div class="duo">${overallTile}${regionTile}</div>`;
}

function gradeCell(k, g) {
  if (!g) return `<div class="cell"><div class="k">${k}</div><div class="v" style="color:var(--text-muted)">n/a</div></div>`;
  return `<div class="cell"><div class="k">${k}</div>
    <div class="v">${g.grade} <small>${g.percentile != null ? g.percentile + "th" : ""}</small></div></div>`;
}

function subBar(label, v) {
  const w = v == null ? 0 : Math.max(2, Math.min(100, v));
  return `<div class="subrow"><div class="t"><span>${label}</span>
      <span class="muted">${v == null ? "n/a" : Math.round(v)}</span></div>
    <div class="bar"><i style="width:${w}%;background:${v == null ? naColor() : "var(--accent)"}"></i></div></div>`;
}

function render(card, key) {
  const prof = state.profile || card.default_profile;
  const o = card.overall[prof] || card.overall[card.default_profile] || {};
  const g = card.grades, cond = card.conditions, wx = cond.weather, ol = card.outlook;

  let html = `<button class="close" type="button" aria-label="Close details">✕</button>
    <h2 id="detail-title">${escapeHTML(card.mountain.name)}</h2>
    <div class="sub">${card.mountain.verified ? "✓ verified station" : "⚠ unverified station"} · ${card.mountain.key}</div>
    ${duo(card, key)}
    <div class="cap" style="margin:2px 0 4px">${prof} profile${o.leaning ? ` · leaning ${o.leaning}` : ""}</div>
    <div class="grid2">
      ${gradeCell("Season", g.season)}
      ${gradeCell("Last 30 days", g.in_season)}
      <div class="cell"><div class="k">Base</div><div class="v">${cond.base_depth != null ? cond.base_depth + '"' : "—"}
        ${g.base && g.base.grade ? `<small>${g.base.grade}</small>` : ""}</div></div>
      <div class="cell"><div class="k">Fresh (7d)</div><div class="v">${cond.fresh_7d != null ? cond.fresh_7d + '"' : "—"}</div></div>
    </div>`;

  if (card.cover_factor != null && card.cover_factor < 1)
    html += `<div class="note">Cover gate ×${card.cover_factor} — a thin base caps the overall score.</div>`;

  html += `<div class="sec">Sub-scores</div>
    ${subBar("Season to date", card.subscores.season)}
    ${subBar("Last 30 days", card.subscores.in_season)}
    ${subBar("Conditions", card.subscores.conditions)}
    ${subBar("Incoming snow", card.subscores.forecast)}`;

  if (card.forecast) {
    const f = card.forecast;
    html += `<div class="sec">Incoming snow</div>
      <div class="storm ${f.alert ? "is-alert" : ""}"><span>next ${f.window_hours}h</span>
        <span>${f.inches}" · ${f.grade}${f.alert ? " ⚠" : ""}</span></div>`;
  }
  if (ol && ol.thaw_index != null && ol.thaw_index >= 0.15) {
    const bits = [];
    if (ol.rain_72h_in >= 0.1) bits.push(`${ol.rain_72h_in}" rain`);
    if (ol.tmax_72h_f > 40) bits.push(`highs to ${ol.tmax_72h_f}°F`);
    html += `<div class="note warn">⚠ Thaw risk next 72h${bits.length ? ` (${bits.join(", ")})` : ""} — dragging the forecast score.</div>`;
  }
  if (ol && ol.refreeze_index != null && ol.refreeze_index >= 0.15)
    html += `<div class="note warn">🧊 Refrozen crust likely — a recent thaw refroze and hasn't been resurfaced by new snow.</div>`;

  if (wx) {
    html += `<div class="sec">Current weather</div>
      <div class="cap">${wx.temperature_f != null ? wx.temperature_f + "°F" : ""}
        ${wx.wind_mph != null ? "· " + wx.wind_mph + " mph wind" : ""}
        ${wx.sky_cover_pct != null ? "· " + wx.sky_cover_pct + "% cloud" : ""}</div>`;
  } else {
    html += `<div class="note">No live forecast for this snapshot — score built from snow history + base.</div>`;
  }
  if (card.sources) {
    const s = card.sources;
    html += `<div class="cap" style="margin-top:10px;opacity:.72">data: history ${s.history}${s.forecast ? ` · forecast ${s.forecast}` : " · no forecast"}${s.weather ? ` · weather ${s.weather}` : ""}</div>`;
  }

  el.innerHTML = html;
  el.querySelector(".close").addEventListener("click", close);
  focusSilently(el.querySelector(".close"));
  announce(`${card.mountain.name}. Overall ${o.grade || "not scored"}.`);
}

// A compact card for a historical date, built from the roster row (no per-date
// card files). Snow-based: no live weather/thaw, and the "incoming" is the snow
// the station actually recorded in the forward window.
function renderHistCard(row, key) {
  if (!row) { el.innerHTML = '<div class="placeholder">No data.</div>'; return; }
  const oc = row.grade && row.grade !== "N/A" ? colorFor(row.grade) : naColor();
  const rTile = row.region_score == null
    ? `<div class="side"><div class="lbl">Within ${row.region}</div>
         <div class="score-row"><span class="badge">${badgeSVG("—", "—", { size: 34 })}</span>
         <span class="num">—</span></div>
         <div class="of">${row.in_season === false ? "off-season" : row.in_season == null ? "no recent data" : "not enough data"}</div></div>`
    : `<div class="side"><div class="lbl">Within ${row.region}</div>
         <div class="score-row"><span class="badge">${badgeSVG(row.region_grade, row.region_grade, { size: 34 })}</span>
         <span class="num">${Math.round(row.region_score)}<span>th pct</span></span></div>
         <div class="of">ranked in ${row.region}</div></div>`;

  el.innerHTML = `<button class="close" type="button" aria-label="Close details">✕</button>
    <h2 id="detail-title">${escapeHTML(row.name)}</h2>
    <div class="sub">as of ${state.histDate} · historical (snow-based)</div>
    <div class="duo">
      <div class="side">
        <div class="lbl">Overall</div>
        <div class="score-row"><span class="badge">${badgeSVG(row.grade || "—", row.grade || "—", { size: 34 })}</span>
          <span class="num">${row.score != null ? Math.round(row.score) : "—"}<span> / 100</span></span></div>
        <div class="of">across all tracked mountains</div>
      </div>
      ${rTile}
    </div>
    <div class="grid2">
      <div class="cell"><div class="k">Season</div><div class="v">${row.season_grade || "—"}</div></div>
      <div class="cell"><div class="k">Base</div><div class="v">${row.base_depth != null ? row.base_depth + '"' : "—"}
        ${row.base_grade && row.base_grade !== "N/A" ? `<small>${row.base_grade}</small>` : ""}</div></div>
      <div class="cell"><div class="k">Fresh (7d)</div><div class="v">${row.fresh_7d != null ? row.fresh_7d + '"' : "—"}</div></div>
      <div class="cell"><div class="k">Snow next 3d</div><div class="v">${row.incoming_inches != null ? row.incoming_inches + '"' : "—"}</div></div>
    </div>
    <div class="note">Retrospective score: history up to ${state.histDate} plus the snow that
      actually fell over the next 3 days. Thaw/weather aren't part of historical scores.</div>`;
  el.querySelector(".close").addEventListener("click", close);
  focusSilently(el.querySelector(".close"));
  announce(`${row.name}. As of ${state.histDate}, overall ${row.grade || "not scored"}.`);
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
