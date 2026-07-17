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

function scoreTile(label, sublabel, grade, value, valueSuffix, size = 34) {
  return `<div class="side">
    <div class="lbl">${label}</div>
    <div class="score-row">
      <span class="badge">${badgeSVG(grade || "—", grade || "—", { size })}</span>
      <span class="num">${value != null ? value : "—"}<span>${value != null ? valueSuffix : ""}</span></span>
    </div>
    <div class="of">${sublabel}</div>
  </div>`;
}

// Rank of `row` within `pool` by `field` (higher value = better, #1 = best).
// Ranks only rows that have a value; returns null when this row has none.
function rankBy(row, pool, field) {
  if (!row || row[field] == null) return null;
  const vals = pool.filter(m => m[field] != null);
  if (!vals.length) return null;
  const better = vals.filter(m => m[field] > row[field]).length;
  return { rank: better + 1, total: vals.length };
}

// The headline block: absolute SKIABILITY ("how good is the skiing right now")
// is the hero grade and governs the pin. Global/regional rank ride alongside as
// the "best available" context -- so a mediocre-but-best mountain reads "C+ · #1
// to ski now". The self-relative `overall` is demoted to historical context.
function duo(card, key) {
  const prof = state.profile || card.default_profile;
  const rel = card.overall[prof] || card.overall[card.default_profile] || {};
  const ski = card.skiability || {};
  const row = state.byKey[key];
  const r = regionBlock(card, key);

  const gGlobal = rankBy(row, state.scores, "global_score");
  const gRegion = row
    ? rankBy(row, state.scores.filter(m => m.region === row.region), "regional_score")
    : null;

  const q = ski.quality_factor;
  const skiTile = `<div class="side hero">
    <div class="lbl">Skiing right now</div>
    <div class="score-row">
      <span class="badge">${badgeSVG(ski.grade || "—", ski.grade || "—", { size: 46 })}</span>
      <span class="num">${ski.score != null ? Math.round(ski.score) : "—"}<span>${ski.score != null ? " / 100" : ""}</span></span>
    </div>
    <div class="of">absolute conditions${q != null && q < 0.85 ? ` · quality ×${q}` : ""}</div>
  </div>`;

  const relTile = scoreTile("Historical context",
    "vs this mountain's own normal", rel.grade,
    rel.score != null ? Math.round(rel.score) : null, " / 100");

  // The regional rank is the headline claim ("#2 of 6 in Argentina to ski right
  // now") -- it's the one number that answers "should I go HERE", so it gets its
  // own emphasized banner rather than sitting in the small pill row. Worldwide
  // rank stays as a secondary pill underneath; off-season falls back to a muted
  // pill when there's no regional rank to show instead.
  const regionRank = gRegion
    ? `<div class="rank-hero"><b>#${gRegion.rank}</b> of ${gRegion.total} in ${escapeHTML(r.name)} to ski right now</div>`
    : card.in_season === false
      ? `<div class="rank-hero muted">off-season</div>`
      : "";

  const bits = [];
  if (gGlobal) bits.push(`<span class="rank"><b>#${gGlobal.rank}</b> of ${gGlobal.total} worldwide</span>`);
  const ranks = bits.length ? `<div class="ranks">${bits.join("")}</div>` : "";

  return `<div class="duo">${skiTile}${relTile}</div>${regionRank}${ranks}`;
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
    <div class="sub">${card.mountain.verified ? "✓ verified station" : "⚠ unverified station"} · ${card.mountain.key}</div>`;

  // Current weather: a compact one-liner right under the name, not its own
  // section down the card -- it's context for everything below, not a result.
  if (wx) {
    const bits = [
      wx.temperature_f != null ? `${wx.temperature_f}°F` : null,
      wx.wind_mph != null ? `${wx.wind_mph} mph wind` : null,
      wx.sky_cover_pct != null ? `${wx.sky_cover_pct}% cloud` : null,
    ].filter(Boolean);
    if (bits.length) html += `<div class="cap wx-line">${bits.join(" · ")}</div>`;
  } else {
    html += `<div class="cap wx-line">No live forecast for this snapshot — score built from snow history + base.</div>`;
  }

  html += `${duo(card, key)}
    <div class="cap" style="margin:2px 0 4px">historical context · ${prof} profile${o.leaning ? ` · leaning ${o.leaning}` : ""}</div>`;

  // The AI one-liner explaining the grade (built server-side with the scoring
  // job, cached per day -- see ski/commentary.py). Absent off-season or when
  // the build ran without credentials; the card simply shows nothing then.
  if (card.commentary)
    html += `<div class="note commentary">${escapeHTML(card.commentary)}</div>`;

  // Incoming snow: its own emphasized section right below the commentary --
  // it's the one forward-looking number on an otherwise backward-looking card.
  // The single biggest window stays pinned as the summary; click to expand the
  // full 24/48/72h breakout plus the 4-10 day medium-range band.
  const hs = card.forecast_horizons || [];
  const mr = ol && ol.medium_range;
  if (card.forecast || hs.length || mr) {
    const f = card.forecast;
    const summary = f
      ? `<span>next ${f.window_hours}h</span><span>${f.inches}" · ${f.grade}${f.alert ? " ⚠" : ""}</span>`
      : `<span>incoming snow</span><span>see forecast</span>`;
    let rows = "";
    hs.forEach(h => {
      rows += `<div class="frow"><span>next ${h.horizon_hours}h</span>
        <span>${h.inches != null ? h.inches + '"' : "—"}${h.percentile != null ? ` · ${h.percentile}th pct` : ""}</span></div>`;
    });
    if (mr)
      rows += `<div class="frow mr"><span>4–${Math.round(mr.horizon_hours / 24)} day range</span>
        <span>${mr.low_in}–${mr.high_in}"${mr.mid_in != null ? ` · mid ${mr.mid_in}"` : ""}</span></div>`;
    html += `<div class="forecast-box">
      <div class="sec-title">Incoming snow</div>
      ${rows
        ? `<details class="forecast"><summary class="storm ${f && f.alert ? "is-alert" : ""}">
             ${summary}<span class="chev" aria-hidden="true">▾</span></summary>
           <div class="fbreak">${rows}</div></details>`
        : `<div class="storm ${f && f.alert ? "is-alert" : ""}">${summary}</div>`}
    </div>`;
  }
  if (ol && ol.thaw_index != null && ol.thaw_index >= 0.15) {
    const bits = [];
    if (ol.rain_72h_in >= 0.1) bits.push(`${ol.rain_72h_in}" rain`);
    if (ol.tmax_72h_f > 40) bits.push(`highs to ${ol.tmax_72h_f}°F`);
    html += `<div class="note warn">⚠ Thaw risk next 72h${bits.length ? ` (${bits.join(", ")})` : ""} — dragging the forecast score.</div>`;
  }
  if (ol && ol.refreeze_index != null && ol.refreeze_index >= 0.15)
    html += `<div class="note warn">🧊 Refrozen crust likely — a recent thaw refroze and hasn't been resurfaced by new snow.</div>`;

  html += `<div class="grid2">
      ${gradeCell("Season", g.season)}
      ${gradeCell("Last 30 days", g.in_season)}
      <div class="cell"><div class="k">Base</div><div class="v">${cond.base_depth != null ? cond.base_depth + '"' : "—"}
        ${g.base && g.base.grade ? `<small>${g.base.grade}</small>` : ""}</div></div>
      <div class="cell"><div class="k">Fresh (7d)</div><div class="v">${cond.fresh_7d != null ? cond.fresh_7d + '"' : "—"}</div></div>
    </div>`;

  html += `<div class="sec">Sub-scores</div>
    ${subBar("Season to date", card.subscores.season)}
    ${subBar("Last 30 days", card.subscores.in_season)}
    ${subBar("Conditions", card.subscores.conditions)}
    ${subBar("Incoming snow", card.subscores.forecast)}`;

  if (card.sources) {
    const s = card.sources;
    html += `<div class="cap" style="margin-top:10px;opacity:.72">data: history ${s.history}${s.forecast ? ` · forecast ${s.forecast}` : " · no forecast"}${s.weather ? ` · weather ${s.weather}` : ""}</div>`;
  }

  el.innerHTML = html;
  el.querySelector(".close").addEventListener("click", close);
  focusSilently(el.querySelector(".close"));
  const skg = (card.skiability || {}).grade;
  announce(`${card.mountain.name}. Skiing right now ${skg || "not scored"}.`);
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
      <div class="side hero">
        <div class="lbl">Skiing that day</div>
        <div class="score-row"><span class="badge">${badgeSVG(row.grade || "—", row.grade || "—", { size: 34 })}</span>
          <span class="num">${row.score != null ? Math.round(row.score) : "—"}<span> / 100</span></span></div>
        <div class="of">absolute conditions</div>
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
