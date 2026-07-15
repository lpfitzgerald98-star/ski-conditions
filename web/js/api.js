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

// The snapshot manifest (as_of, profiles, region_tree). Static reads the built
// meta.json; live asks the backend's /meta (which carries the region hierarchy),
// falling back to a minimal shim against an older backend without the route.
export async function loadMeta() {
  if (!LIVE) return getJSON(`${DATA_BASE}/meta.json`);
  try { return await getJSON(`${API_BASE}/meta`); }
  catch {
    return { profiles: ["dynamic", "weekend", "month", "season"],
             default_profile: "dynamic", regions: [], as_of: null };
  }
}

// The ranked roster for a profile. Static reads the prebuilt file; live falls
// back to /scores (the map normally boots off the SSE snapshot instead).
export async function loadScores(profile) {
  if (!LIVE) return getJSON(`${DATA_BASE}/scores.${profile}.json`);
  const data = await getJSON(`${API_BASE}/scores?profile=${encodeURIComponent(profile)}`);
  return data.mountains;
}

// The retrospective-history manifest (available dates + bounds). Static only;
// null when history hasn't been built or in live mode.
export async function loadHistoryIndex() {
  if (LIVE) return null;
  try { return await getJSON(`${DATA_BASE}/hist/index.json`); }
  catch { return null; }
}

// The ranked roster for one PAST date (retro scores).
export async function loadHistoryDate(dateStr) {
  return getJSON(`${DATA_BASE}/hist/${dateStr}.json`);
}

// One mountain's full scorecard.
export async function loadCard(key, { network = false } = {}) {
  if (!LIVE) return getJSON(`${DATA_BASE}/cards/${key}.json`);
  const q = new URLSearchParams({ network: String(network) });
  return getJSON(`${API_BASE}/score/${key}?${q}`);
}
