// The "snow alerts" bell: subscribes this device to web push for the day's best
// snow anywhere (storms, overnight powder, openings, A grades). The daily build
// picks and sends the alerts (scripts/push_alerts.py); the gladegrade-push Worker
// (config PUSH_API) only stores which devices asked for them.
//
// iPhone/iPad only allow web push from a site added to the Home Screen (iOS 16.4+),
// so in Safari the bell explains that instead of asking.

import { PUSH_API, VAPID_PUBLIC_KEY } from "./config.js";
import { announce } from "./a11y.js";

const $ = id => document.getElementById(id);

const supported = () =>
  "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const standalone = () =>
  matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () =>
  /iP(hone|ad|od)/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

function keyBytes(base64url) {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

async function currentSubscription() {
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

function render(btn, on) {
  btn.setAttribute("aria-pressed", String(on));
  const label = on ? "Turn off snow alerts" : "Get alerts for the best snow";
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.querySelector("span").textContent = on ? "🔔" : "🔕";
}

async function post(path, body) {
  const res = await fetch(`${PUSH_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
}

async function turnOn() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    alert("Notifications are blocked for GladeGrade. Allow them in your browser or phone settings, then tap the bell again.");
    return false;
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(VAPID_PUBLIC_KEY),
  });
  try {
    await post("/subscribe", { subscription: sub.toJSON() });
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

export async function initAlerts() {
  const btn = $("alerts-toggle");
  if (!btn || !PUSH_API || !VAPID_PUBLIC_KEY) return;

  const needsHomeScreen = isIOS() && !standalone();
  if (!supported() && !needsHomeScreen) return;   // this browser can't do web push
  btn.hidden = false;

  if (needsHomeScreen) {
    render(btn, false);
    btn.addEventListener("click", () => alert(
      "To get snow alerts on iPhone or iPad, add GladeGrade to your Home Screen " +
      "(Share, then Add to Home Screen), open it from there, and tap the bell."));
    return;
  }

  let on = Boolean(await currentSubscription());
  render(btn, on);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      if (on) {
        await turnOff();
        on = false;
        announce("Snow alerts turned off.");
      } else {
        on = await turnOn();
        if (on) announce("Snow alerts on. You'll hear about the best snow anywhere, at most twice a day.");
      }
    } catch (err) {
      console.error(err);
      alert("Couldn't change snow alerts just now. Please try again in a minute.");
    } finally {
      render(btn, on);
      btn.disabled = false;
    }
  });
}
