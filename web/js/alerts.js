// Snow alerts: the bell, the alert settings sheet, and the Follow button on each
// mountain card.
//
// Each device picks what it hears about: mountains it follows (from their cards),
// whole regions, which kinds of event, and whether it also wants the best snow
// anywhere. The settings live here (localStorage) and, once alerts are on, with
// the device's subscription in the gladegrade-push Worker (config PUSH_API). The
// daily build reads them and picks each device's alert (scripts/push_alerts.py).
//
// iPhone/iPad only allow web push from a site added to the Home Screen (iOS 16.4+),
// so in Safari the sheet explains that instead of offering to turn alerts on.

import { PUSH_API, VAPID_PUBLIC_KEY } from "./config.js";
import { announce } from "./a11y.js";
import { state } from "./state.js";
import { optionsList } from "./regions.js";

const $ = id => document.getElementById(id);
const STORE = "gg-alert-prefs";

export const KINDS = [
  ["powder", "Powder days", "Big overnight snow at an open mountain"],
  ["storm", "Big storms coming", "A forecast storm that's big for that mountain, 1–3 days out"],
  ["grade_a", "A grades", "A mountain starts skiing at A- or better"],
  ["opens", "Season openings", "A mountain opens for the season"],
];
const DEFAULTS = { best: true, kinds: KINDS.map(k => k[0]), mountains: [], regions: [] };

const supported = () =>
  "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const standalone = () =>
  matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () =>
  /iP(hone|ad|od)/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const configured = () => Boolean(PUSH_API && VAPID_PUBLIC_KEY);
const needsHomeScreen = () => isIOS() && !standalone() && !supported();

// Whether this device can have alerts at all (or could, after Add to Home Screen).
export const alertsAvailable = () => configured() && (supported() || isIOS());

let prefs = loadPrefs();
let on = false;
let saveTimer = null;

function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || "null");
    if (raw && typeof raw === "object") return { ...DEFAULTS, ...raw };
  } catch { /* private mode or junk: fall back to defaults */ }
  return { ...DEFAULTS, kinds: [...DEFAULTS.kinds] };
}

function storePrefs() {
  try { localStorage.setItem(STORE, JSON.stringify(prefs)); } catch { /* not fatal */ }
}

function keyBytes(base64url) {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

async function currentSubscription() {
  if (!supported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

async function post(path, body) {
  const res = await fetch(`${PUSH_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = new Error(`${path}: ${res.status}`);
    err.status = res.status;
    throw err;
  }
}

async function turnOn() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    alert("Notifications are blocked for GladeGrade. Allow them in your browser or phone settings, then try again.");
    return false;
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(VAPID_PUBLIC_KEY),
  });
  try {
    await post("/subscribe", { subscription: sub.toJSON(), prefs });
  } catch (err) {
    await sub.unsubscribe();
    throw err;
  }
  return true;
}

async function turnOff() {
  const sub = await currentSubscription();
  if (!sub) return;
  try {
    await post("/unsubscribe", { endpoint: sub.endpoint });
  } finally {
    await sub.unsubscribe();
  }
}

// Push the settings to the Worker (debounced, so ticking boxes is one request).
function savePrefs() {
  storePrefs();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(syncPrefs, 600);
}

async function syncPrefs() {
  const status = $("alerts-saved");
  if (!on) { if (status) status.textContent = "Saved on this device."; return; }
  try {
    const sub = await currentSubscription();
    if (!sub) return;
    try {
      await post("/prefs", { endpoint: sub.endpoint, prefs });
    } catch (err) {
      if (err.status !== 404) throw err;
      await post("/subscribe", { subscription: sub.toJSON(), prefs });   // store lost it: re-add
    }
    if (status) status.textContent = "Saved.";
  } catch (err) {
    console.error(err);
    if (status) status.textContent = "Couldn't save just now. Your changes will be sent next time.";
  }
}

// -- the bells ----------------------------------------------------------------

function renderBells() {
  document.querySelectorAll("[data-alerts-open]").forEach(btn => {
    btn.setAttribute("aria-pressed", String(on));
    const label = on ? "Snow alerts are on: settings" : "Get snow alerts";
    btn.setAttribute("aria-label", label);
    btn.title = label;
    btn.querySelector("span").textContent = on ? "🔔" : "🔕";
  });
}

// -- follow buttons -----------------------------------------------------------

export function followButton(key) {
  if (!alertsAvailable() || !key) return "";
  const f = prefs.mountains.includes(key);
  return `<button class="follow-btn" type="button" data-follow="${key}" aria-pressed="${f}">`
    + `${f ? "★ Following" : "☆ Follow"}</button>`;
}

function renderFollowButtons() {
  document.querySelectorAll("[data-follow]").forEach(btn => {
    const f = prefs.mountains.includes(btn.dataset.follow);
    btn.setAttribute("aria-pressed", String(f));
    btn.textContent = f ? "★ Following" : "☆ Follow";
  });
}

function nameOf(key) {
  const row = state.byKey && state.byKey[key];
  return row ? row.name : key;
}

function toggleFollow(key) {
  const has = prefs.mountains.includes(key);
  prefs.mountains = has ? prefs.mountains.filter(k => k !== key) : [...prefs.mountains, key];
  savePrefs();
  renderFollowButtons();
  if (dialog && dialog.open) renderDialog();
  announce(has ? `Stopped following ${nameOf(key)}.` : `Following ${nameOf(key)}.`);
  // Following is only useful with alerts on: offer them the first time.
  if (!has && !on) openDialog();
}

// -- the settings sheet ---------------------------------------------------------

let dialog = null;

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderDialog() {
  const body = $("alerts-body");
  const homeScreen = needsHomeScreen();
  const head = homeScreen
    ? `<p class="alerts-note">To get snow alerts on iPhone or iPad, add GladeGrade to your Home Screen
         (Share, then <b>Add to Home Screen</b>), open it from there, and turn alerts on here.
         Your choices below are kept.</p>`
    : `<p class="alerts-state">${on ? "🔔 Alerts are <b>on</b> for this device." : "🔕 Alerts are <b>off</b> for this device."}</p>
       <button id="alerts-onoff" class="btn ${on ? "" : "btn-primary"}" type="button">
         ${on ? "Turn off alerts" : "Turn on alerts"}</button>`;

  const mine = prefs.mountains.length
    ? `<ul class="alerts-chips">${prefs.mountains.map(k =>
        `<li><span>${esc(nameOf(k))}</span><button type="button" data-unfollow="${esc(k)}"
           aria-label="Stop following ${esc(nameOf(k))}">✕</button></li>`).join("")}</ul>`
    : `<p class="alerts-hint">None yet. Tap <b>☆ Follow</b> on any mountain's card.</p>`;

  const regionOpts = optionsList(state.scores || []);
  const regions = regionOpts.length
    ? `<div class="alerts-regions">${regionOpts.map(o =>
        `<label class="check" style="padding-left:${o.depth * 16}px">
           <input type="checkbox" data-region="${esc(o.id)}" ${prefs.regions.includes(o.id) ? "checked" : ""}>
           <span>${esc(o.id)} <small>(${o.count})</small></span></label>`).join("")}</div>`
    : `<p class="alerts-hint">Regions appear once the mountains load.</p>`;

  const kinds = KINDS.map(([k, label, hint]) =>
    `<label class="check"><input type="checkbox" data-kind="${k}" ${prefs.kinds.includes(k) ? "checked" : ""}>
       <span><b>${label}</b><small>${hint}</small></span></label>`).join("");

  body.innerHTML = `${head}
    <fieldset><legend>Your mountains</legend>${mine}</fieldset>
    <fieldset><legend>Your regions</legend>
      <p class="alerts-hint">Alerts for every mountain in a region. Picking a big region includes everything in it.</p>
      ${regions}</fieldset>
    <fieldset><legend>Kinds of alert</legend>${kinds}</fieldset>
    <fieldset><legend>Beyond your picks</legend>
      <label class="check"><input type="checkbox" id="alerts-best" ${prefs.best ? "checked" : ""}>
        <span><b>Best snow anywhere</b><small>Also hear about the single best snow event in the world,
        even at mountains you don't follow. When you follow mountains, at most one of these a day,
        so yours always come first.</small></span></label>
    </fieldset>
    <p class="alerts-hint">At most two alerts a day. Settings are saved automatically.</p>
    <p id="alerts-saved" class="alerts-saved" role="status" aria-live="polite"></p>`;
}

function openDialog() {
  if (!dialog) return;
  renderDialog();
  if (!dialog.open) dialog.showModal();
}

function wireDialog() {
  dialog = $("alerts-dialog");
  if (!dialog) return;
  $("alerts-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", e => { if (e.target === dialog) dialog.close(); });   // backdrop tap

  dialog.addEventListener("change", e => {
    const t = e.target;
    if (t.dataset.kind) {
      prefs.kinds = KINDS.map(k => k[0]).filter(k =>
        k === t.dataset.kind ? t.checked : prefs.kinds.includes(k));
    } else if (t.dataset.region) {
      const r = t.dataset.region;
      prefs.regions = t.checked ? [...new Set([...prefs.regions, r])] : prefs.regions.filter(x => x !== r);
    } else if (t.id === "alerts-best") {
      prefs.best = t.checked;
    } else return;
    savePrefs();
  });

  dialog.addEventListener("click", async e => {
    const un = e.target.closest("[data-unfollow]");
    if (un) { toggleFollow(un.dataset.unfollow); return; }
    const btn = e.target.closest("#alerts-onoff");
    if (!btn) return;
    btn.disabled = true;
    try {
      if (on) {
        await turnOff();
        on = false;
        announce("Snow alerts turned off.");
      } else {
        on = await turnOn();
        if (on) announce("Snow alerts on.");
      }
    } catch (err) {
      console.error(err);
      alert("Couldn't change snow alerts just now. Please try again in a minute.");
    } finally {
      renderBells();
      renderDialog();
    }
  });
}

export async function initAlerts() {
  if (!alertsAvailable()) return;   // this browser can't do web push
  wireDialog();
  document.querySelectorAll("[data-alerts-open]").forEach(btn => {
    btn.hidden = false;
    btn.addEventListener("click", openDialog);
  });
  // Follow buttons are re-rendered with each card, so listen once on the document.
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-follow]");
    if (btn) toggleFollow(btn.dataset.follow);
  });

  try { on = Boolean(await currentSubscription()); } catch { on = false; }
  renderBells();
  // A device that turned alerts on before settings existed: send its settings once,
  // so what it sees here is what the build uses.
  if (on) syncPrefs();
}
