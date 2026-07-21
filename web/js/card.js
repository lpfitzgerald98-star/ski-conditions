// The detail card: a mountain's full scorecard, shown in a dialog over the map.
// Two scores side by side (absolute overall + within-region percentile) on their
// own curves, then grades, sub-score bars, incoming storms, weather and warnings.

import { loadCard, loadTripPattern } from "./api.js";
import { colorFor, textOn, badgeSVG, naColor, letterFor } from "./grades.js";
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
  const token = ++reqToken;
  // Future (trip) and historical dates both render a compact card straight from the
  // roster row -- no per-date card files. The row carries the trip breakdown. The
  // token still guards trip cards: Part 1 of the commentary loads asynchronously
  // (static mode fetches a per-mountain file), and a fast subsequent click must not
  // let a stale fetch overwrite the card that's now showing.
  if (state.tripDate) { el.dataset.key = key; renderTripCard(row, key, token); return; }
  // Historical dates have no per-date card file (that'd be 79 x N files); render a
  // compact card straight from the roster row, which already carries the grades.
  if (state.histDate) { el.dataset.key = key; renderHistCard(row, key); return; }
  if (el.dataset.key !== key) {
    el.innerHTML = `<div class="placeholder">Loading ${row ? escapeHTML(row.name) : key}…</div>`;
  }
  el.dataset.key = key;
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

// -- Trip Predictor commentary (Parts 2+3) ----------------------------------
// Part 1 (seasonal pattern) is written once, in Python, over the whole 366-day
// climatology (ski.trip_commentary) -- see loadTripPattern. Parts 2 (how this
// year is tracking vs. that pattern) and 3 (the takeaway) depend on TODAY's
// live score and the picked date's lead time, which change daily and are
// already sitting on every trip row in both modes -- writing them a second
// time in Python would just be a second engine to keep in sync for numbers
// Python doesn't need to look up again. Same combinatorial-variety trick as
// the live commentary (ski/commentary_rules.py): a few phrasing choices per
// slot, chosen by a seed stable per (mountain, trip date) so the SAME pick
// always reads the same way, ported to JS since there's no seeded RNG built in.
function _seededRng(seed) {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}
const _pick = (rng, arr) => arr[Math.floor(rng() * arr.length)];

// Beyond this many days out, there's no live forecast signal left to talk
// about at all -- matches config.MEDIUM_RANGE['horizon_hours'] (240h = 10
// days), the real outer edge of forecast skill the scoring engine itself
// uses (see pipeline.medium_range_percentile). A reader planning a trip this
// far out isn't asking "what's the forecast" -- they're asking "how does this
// mountain usually ski this time of year", so Part 2 (today-vs-baseline
// tracking, "no live read" caveats) has nothing useful to add and is skipped
// entirely; the takeaway (Part 3) carries the "so what" alone, grade-driven.
const _NEAR_TERM_LEAD_DAYS = 10;

const _TRACKING_BAND = [
  [20, "running well ahead of"], [8, "running a bit ahead of"],
  [-8, "tracking about in line with"], [-20, "running a bit behind"],
  [-Infinity, "running well behind"],
];
function _trackingBand(delta) {
  return _TRACKING_BAND.find(([floor]) => delta >= floor)[1];
}

// The takeaway's tier, from the BLENDED trip grade (row.grade -- the same
// letter the hero badge shows), not the raw historical baseline alone: this
// is what makes an A+ read positively regardless of why it's an A+ (strong
// current conditions near-term, or a strong historical pattern far out).
// Same A/B/C/other-is-poor collapse as commentary_rules._tier, independently
// implemented in JS since it's a two-line lookup, not worth importing a
// whole module for.
function _tier(grade) {
  const l = (grade || "")[0];
  return { A: "great", B: "good", C: "fair" }[l] || "poor";
}
const _TAKEAWAY = {
  great: ["This is shaping up as a strong window for the trip.",
          "Worth planning around -- this reads as one of the better bets."],
  good: ["A solid, reasonable choice for the trip.",
         "This should be a decent bet overall."],
  fair: ["A middling read -- not a standout, but not a bad option either.",
         "Nothing special expected here, but nothing alarming either."],
  poor: ["Worth tempering expectations for this window.",
         "This isn't shaping up as a strong pick right now."],
};
const _LOW_CONF_SUFFIX = [
  " With a shorter station record here, hold this loosely.",
  " The record here is thin, so treat this as a rough guide.",
];

// Part 2: how today compares to the historical pattern, explicit about how
// much today's conditions actually count at this lead time. Returns null
// beyond _NEAR_TERM_LEAD_DAYS -- there's no live forecast signal left to
// weigh in on at that range, so there's nothing honest to say about "today"
// at all (see _NEAR_TERM_LEAD_DAYS); the takeaway alone carries Part 3.
function _trackingText(row, info, rng) {
  const lead = info.lead_days;
  if (lead > _NEAR_TERM_LEAD_DAYS) return null;

  const short = row.name.split(",")[0].trim();
  const pct = Math.round((row.current_weight ?? info.current_weight ?? 0) * 100);
  const weightClause = pct >= 50
    ? _pick(rng, [`with the trip only ${lead} days out, today's conditions are doing most of the talking here`,
                  `today's conditions carry real weight here (${pct}%) with the trip this close`])
    : _pick(rng, [`with the trip ${lead} days out, today's conditions still carry real weight (about ${pct}%) in this call`,
                  `at ${pct}% weight, today's conditions still meaningfully shape this call ${lead} days out`]);

  if (row.current_score != null && row.historical_baseline != null) {
    const band = _trackingBand(row.current_score - row.historical_baseline);
    return _pick(rng, [
      `Right now, ${short} is ${band} its usual pace for this date, and ${weightClause}.`,
      `Today's conditions at ${short} are ${band} the historical norm for this date -- ${weightClause}.`,
    ]);
  }
  if (row.current_score != null) {
    // No baseline (thin/no historical record for this window), but a live
    // read exists and the trip is close enough for it to matter.
    return `There's no deep historical record for this exact window at ${short}, so ${weightClause}.`;
  }
  // No live read even though the trip is within the forecast-relevant
  // window (e.g. the mountain is between seasons right now) -- worth saying
  // here, unlike the far-out case, since the reader might expect one.
  return `There's no live conditions read for ${short} right now, so this call leans on its historical pattern for the date.`;
}

// Part 3: the plain-language takeaway, driven by the BLENDED grade -- close
// in, that's mostly today's conditions; far out, it's almost entirely the
// historical baseline. Either way, an A+ reads positively here: the tier
// comes from row.grade, the same letter already blended for exactly this
// lead time, not a second, disconnected judgment call.
function _takeawayText(row, rng) {
  let text = _pick(rng, _TAKEAWAY[_tier(row.grade)]);
  if (row.low_confidence) text += _pick(rng, _LOW_CONF_SUFFIX);
  return text;
}

// The Trip Predictor card: a blended prediction for a FUTURE date, built from the
// roster row (no per-date card files). Shows the blend so it's explainable -- the
// historical baseline, today's conditions, and how much each counts at this lead --
// plus the three-part commentary (seasonal pattern / tracking / takeaway).
async function renderTripCard(row, key, token) {
  if (!row) { el.innerHTML = '<div class="placeholder">No data.</div>'; return; }
  const info = state.tripInfo || {};
  const grd = v => (v == null ? "—" : (letterFor(v) || "—"));
  const wpct = Math.round((row.current_weight ?? info.current_weight ?? 0) * 100);
  const g = row.grade && row.grade !== "N/A" && row.grade !== "—" ? row.grade : "—";
  const base = row.historical_baseline, cur = row.current_score;
  const heroNum = row.trip_score != null ? Math.round(row.trip_score) : "—";
  const offseason = row.trip_score == null;

  const rng = _seededRng(`${key}|${state.tripDate}`);
  const commentaryParts = [row.pattern || ""];   // live mode: already on the row
  if (row.trip_score != null) {
    const tracking = _trackingText(row, info, rng);   // null beyond the forecast-relevant window
    if (tracking) commentaryParts.push(tracking);
    commentaryParts.push(_takeawayText(row, rng));
  }

  el.innerHTML = `<button class="close" type="button" aria-label="Close details">✕</button>
    <h2 id="detail-title">${escapeHTML(row.name)}</h2>
    <div class="sub">trip prediction · ${state.tripDate} · ${info.lead_days} days out</div>
    <div class="duo">
      <div class="side hero">
        <div class="lbl">Predicted for your trip</div>
        <div class="score-row"><span class="badge">${badgeSVG(g, g, { size: 46 })}</span>
          <span class="num">${heroNum}<span>${row.trip_score != null ? " / 100" : ""}</span></span></div>
        <div class="of">${wpct}% today · ${100 - wpct}% history</div>
      </div>
      ${scoreTile("Historical baseline", "typical for this week of the year",
                  grd(base), base != null ? Math.round(base) : null, " / 100")}
    </div>
    ${offseason
      ? `<div class="rank-hero muted">no prediction — ${row.n_years ? "historically off-season this week" : "no historical record here"}</div>`
      : ""}
    <div class="grid2">
      <div class="cell"><div class="k">Today's conditions</div>
        <div class="v">${cur != null ? Math.round(cur) : "—"}${cur != null ? " <small>/100</small>" : ""}</div></div>
      <div class="cell"><div class="k">Weight on today</div><div class="v">${wpct}<small>%</small></div></div>
      <div class="cell"><div class="k">Lead time</div><div class="v">${info.lead_days}<small>days</small></div></div>
      <div class="cell"><div class="k">History</div>
        <div class="v">${row.n_years || 0}<small>yrs${row.low_confidence ? " · low conf" : ""}</small></div></div>
    </div>
    <div class="note commentary trip-commentary">
      <p class="trip-pattern">${row.pattern ? escapeHTML(row.pattern) : "Loading seasonal pattern…"}</p>
      ${commentaryParts.slice(1).map(p => `<p>${escapeHTML(p)}</p>`).join("")}
    </div>`;
  el.querySelector(".close").addEventListener("click", close);
  focusSilently(el.querySelector(".close"));
  announce(`${row.name}. Trip prediction ${g}.`);

  // Live mode already embedded `pattern` on the row -- nothing more to fetch.
  // Static mode fetches it lazily (per-mountain file); fill it in once it
  // lands, guarded so a fast subsequent click can't have this overwrite a
  // card that's since moved on to a different mountain or date.
  if (!row.pattern) {
    let mmdd = state.tripDate.slice(5);
    if (mmdd === "02-29") mmdd = "02-28";
    const text = await loadTripPattern(key, mmdd);
    if (token !== reqToken || state.tripDate == null) return;
    const slot = el.querySelector(".trip-pattern");
    if (slot) slot.textContent = text || "No seasonal pattern available for this mountain.";
  }
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
