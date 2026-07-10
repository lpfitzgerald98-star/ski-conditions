// Live updates over Server-Sent Events, with explicit reconnect + backoff.
//
// This is the hybrid hinge. In STATIC mode (API_BASE empty) start() is a no-op and
// the app runs off the daily snapshot. Point config.API_BASE at a running backend
// and the same UI upgrades to live streaming with zero other changes.
//
// EventSource auto-reconnects, but crudely: on any drop it replays the whole
// stream from scratch. We wrap it so that (a) once a stream completes we close it
// deliberately instead of letting it loop, and (b) a genuine mid-stream drop
// reconnects on an exponential backoff with a cap, announced to the user.

import { API_BASE, LIVE } from "./config.js";
import { emit } from "./state.js";

let es = null;
let complete = false;
let retries = 0;
let backoffTimer = null;
let currentQuery = "";

const MAX_RETRIES = 6;
const BASE_DELAY = 1000;    // ms; doubles each retry, capped
const MAX_DELAY = 30000;

export function isLive() { return LIVE; }

// Begin (or restart) streaming for a profile/as_of. No-op when static.
export function start({ profile, asof } = {}) {
  if (!LIVE) return;
  stop();
  const q = new URLSearchParams({ profile: profile || "dynamic" });
  if (asof) q.set("as_of", asof);
  currentQuery = q.toString();
  complete = false;
  retries = 0;
  open();
}

function open() {
  es = new EventSource(`${API_BASE}/live/stream?${currentQuery}`);

  es.addEventListener("snapshot", ev => {
    retries = 0;                         // a frame arrived: the connection is healthy
    emit("sse:snapshot", JSON.parse(ev.data));
  });
  es.addEventListener("mountain_update", ev => emit("sse:update", JSON.parse(ev.data)));
  es.addEventListener("stream_complete", ev => {
    complete = true;
    emit("sse:complete", JSON.parse(ev.data));
    close();                             // finished: don't let EventSource re-loop
  });

  es.onerror = () => {
    if (complete) { close(); return; }   // expected close after completion
    close();
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (retries >= MAX_RETRIES) {
    emit("sse:failed", { retries });
    return;
  }
  const delay = Math.min(BASE_DELAY * 2 ** retries, MAX_DELAY);
  retries += 1;
  emit("sse:reconnecting", { attempt: retries, delay });
  clearTimeout(backoffTimer);
  backoffTimer = setTimeout(open, delay);
}

function close() {
  if (es) { es.close(); es = null; }
}

export function stop() {
  clearTimeout(backoffTimer);
  complete = true;   // suppress reconnect from an in-flight error
  close();
}
