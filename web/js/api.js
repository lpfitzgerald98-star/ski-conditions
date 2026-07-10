// Data access. Hides the static-vs-live split: callers ask for grades / scores /
// a card and don't care whether it came from flat JSON or the backend.

import { API_BASE, LIVE, DATA_BASE } from "./config.js";

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`);
  return res.json();
}

export async function loadGrades() {
  return getJSON(LIVE ? `${API_BASE}/grades` : `${DATA_BASE}/grades.json`);
}

// Static-only: the snapshot manifest (as_of, profiles, regions). In live mode the
// stream carries as_of/profile itself, so this returns a minimal shim.
export async function loadMeta() {
  if (!LIVE) return getJSON(`${DATA_BASE}/meta.json`);
  return { profiles: ["dynamic", "weekend", "month", "season"],
           default_profile: "dynamic", regions: [], as_of: null };
}

// The ranked roster for a profile. Static reads the prebuilt file; live falls
// back to /scores (the map normally boots off the SSE snapshot instead).
export async function loadScores(profile) {
  if (!LIVE) return getJSON(`${DATA_BASE}/scores.${profile}.json`);
  const data = await getJSON(`${API_BASE}/scores?profile=${encodeURIComponent(profile)}`);
  return data.mountains;
}

// One mountain's full scorecard.
export async function loadCard(key, { network = false } = {}) {
  if (!LIVE) return getJSON(`${DATA_BASE}/cards/${key}.json`);
  const q = new URLSearchParams({ network: String(network) });
  return getJSON(`${API_BASE}/score/${key}?${q}`);
}
