// GladeGrade push subscriptions: a tiny Cloudflare Worker + KV store.
//
// The site is static (GitHub Pages), so it has nowhere to keep the list of phones
// that turned alerts on. This Worker is that list, nothing more:
//   POST   /subscribe          {subscription}  from the app's bell (public)
//   POST   /unsubscribe        {endpoint}      from the app's bell (public)
//   GET    /subscriptions      list all        from the daily build (bearer token)
//   DELETE /subscriptions/:id  drop a dead one from the daily build (bearer token)
// Sending happens in the daily build (scripts/push_alerts.py), which holds the
// VAPID private key; this Worker never sees it.
//
// Bindings (wrangler.toml): KV namespace SUBS, var ALLOWED_ORIGIN, secret ADMIN_TOKEN.

// Push services browsers actually hand out endpoints for. Anything else is refused,
// so the store can't be filled with arbitrary URLs.
const PUSH_HOSTS = [
  /^fcm\.googleapis\.com$/,
  /^web\.push\.apple\.com$/,
  /^updates\.push\.services\.mozilla\.com$/,
  /\.notify\.windows\.com$/,
];
const MAX_BODY_BYTES = 8192;
const KINDS = ["storm", "powder", "grade_a", "opens"];
const MAX_MOUNTAINS = 60;
const MAX_REGIONS = 30;
const MAX_HISTORY = 200;
const NAME = /^[\p{L}\p{N} .,&'()_-]{1,60}$/u;   // mountain keys and region names

// The settings a phone sent, cut down to known fields and sane sizes. Missing or
// junk fields fall back to the defaults, which match how alerts worked before
// settings existed: the best snow anywhere, every kind, nothing followed.
export function cleanPrefs(raw) {
  const p = raw && typeof raw === "object" ? raw : {};
  const names = (v, max) => Array.isArray(v)
    ? [...new Set(v.filter((x) => typeof x === "string" && NAME.test(x)))].slice(0, max) : [];
  const kinds = Array.isArray(p.kinds) ? KINDS.filter((k) => p.kinds.includes(k)) : [...KINDS];
  return {
    best: p.best !== false,
    kinds,
    mountains: names(p.mountains, MAX_MOUNTAINS),
    regions: names(p.regions, MAX_REGIONS),
  };
}

function cleanHistory(raw) {
  if (!Array.isArray(raw)) return null;
  const ok = raw.filter((h) => h && typeof h === "object"
    && /^\d{4}-\d{2}-\d{2}$/.test(h.date) && KINDS.includes(h.kind)
    && typeof h.key === "string" && NAME.test(h.key) && ["follow", "best"].includes(h.why));
  return ok.slice(-MAX_HISTORY).map(({ date, kind, key, why }) => ({ date, kind, key, why }));
}

function cors(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function json(data, status, headers = {}) {
  return new Response(JSON.stringify(data), {
    status, headers: { "Content-Type": "application/json", ...headers },
  });
}

export async function subscriptionId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

export function validEndpoint(endpoint) {
  try {
    const url = new URL(endpoint);
    return url.protocol === "https:" && PUSH_HOSTS.some((re) => re.test(url.hostname));
  } catch {
    return false;
  }
}

async function readBody(request, limit = MAX_BODY_BYTES) {
  const text = await request.text();
  if (text.length > limit) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function authorized(request, env) {
  const got = request.headers.get("Authorization") || "";
  return Boolean(env.ADMIN_TOKEN) && got === `Bearer ${env.ADMIN_TOKEN}`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const headers = cors(env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });

    if (request.method === "POST" && url.pathname === "/subscribe") {
      const body = await readBody(request);
      const sub = body && body.subscription;
      if (!sub || !validEndpoint(sub.endpoint) || !sub.keys || !sub.keys.p256dh || !sub.keys.auth) {
        return json({ error: "invalid subscription" }, 400, headers);
      }
      const id = await subscriptionId(sub.endpoint);
      const old = JSON.parse((await env.SUBS.get(id)) || "{}");
      const record = { endpoint: sub.endpoint, keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth },
                       created: old.created || new Date().toISOString(),
                       prefs: cleanPrefs(body.prefs ?? old.prefs), history: old.history || [] };
      await env.SUBS.put(id, JSON.stringify(record));
      return json({ ok: true }, 200, headers);
    }

    if (request.method === "POST" && url.pathname === "/prefs") {
      const body = await readBody(request);
      if (!body || typeof body.endpoint !== "string") return json({ error: "invalid" }, 400, headers);
      const id = await subscriptionId(body.endpoint);
      const value = await env.SUBS.get(id);
      if (!value) return json({ error: "not subscribed" }, 404, headers);
      const record = JSON.parse(value);
      record.prefs = cleanPrefs(body.prefs);
      await env.SUBS.put(id, JSON.stringify(record));
      return json({ ok: true, prefs: record.prefs }, 200, headers);
    }

    if (request.method === "POST" && url.pathname === "/unsubscribe") {
      const body = await readBody(request);
      if (!body || typeof body.endpoint !== "string") return json({ error: "invalid" }, 400, headers);
      await env.SUBS.delete(await subscriptionId(body.endpoint));
      return json({ ok: true }, 200, headers);
    }

    if (url.pathname === "/subscriptions" && request.method === "GET") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const out = [];
      let cursor;
      do {
        const page = await env.SUBS.list({ cursor });
        for (const { name } of page.keys) {
          const value = await env.SUBS.get(name);
          if (value) out.push({ id: name, ...JSON.parse(value) });
        }
        cursor = page.list_complete ? undefined : page.cursor;
      } while (cursor);
      return json(out, 200);
    }

    const hist = url.pathname.match(/^\/subscriptions\/([0-9a-f]{32})\/history$/);
    if (hist && request.method === "PUT") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await readBody(request, 64 * 1024);
      const history = cleanHistory(body && body.history);
      if (!history) return json({ error: "invalid" }, 400);
      const value = await env.SUBS.get(hist[1]);
      if (!value) return json({ error: "not found" }, 404);
      const record = JSON.parse(value);
      record.history = history;
      await env.SUBS.put(hist[1], JSON.stringify(record));
      return json({ ok: true }, 200);
    }

    const del = url.pathname.match(/^\/subscriptions\/([0-9a-f]{32})$/);
    if (del && request.method === "DELETE") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      await env.SUBS.delete(del[1]);
      return json({ ok: true }, 200);
    }

    return json({ error: "not found" }, 404, headers);
  },
};
