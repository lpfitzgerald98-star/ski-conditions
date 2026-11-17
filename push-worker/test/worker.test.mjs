// Run: node --test push-worker/test/
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { validEndpoint, cleanPrefs } from "../src/index.js";

function fakeKV() {
  const store = new Map();
  return {
    store,
    async put(k, v) { store.set(k, v); },
    async get(k) { return store.get(k) ?? null; },
    async delete(k) { store.delete(k); },
    async list() { return { keys: [...store.keys()].map((name) => ({ name })), list_complete: true }; },
  };
}

const env = () => ({ SUBS: fakeKV(), ALLOWED_ORIGIN: "https://example.github.io", ADMIN_TOKEN: "t0ken" });
const SUB = { endpoint: "https://web.push.apple.com/abc", keys: { p256dh: "p", auth: "a" } };
const post = (path, body) => new Request(`https://w.dev${path}`, {
  method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
const admin = (path, method = "GET", token = "t0ken") => new Request(`https://w.dev${path}`, {
  method, headers: { Authorization: `Bearer ${token}` } });

test("a phone can subscribe and the build can list it", async () => {
  const e = env();
  const r = await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("Access-Control-Allow-Origin"), "https://example.github.io");
  const list = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.equal(list.length, 1);
  assert.equal(list[0].endpoint, SUB.endpoint);
  assert.match(list[0].id, /^[0-9a-f]{32}$/);
});

test("subscribing twice keeps one entry", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  assert.equal(e.SUBS.store.size, 1);
});

test("unsubscribe removes the phone", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  await worker.fetch(post("/unsubscribe", { endpoint: SUB.endpoint }), e);
  assert.equal(e.SUBS.store.size, 0);
});

test("endpoints outside the browser push services are refused", async () => {
  const e = env();
  const bad = { ...SUB, endpoint: "https://evil.example.com/x" };
  assert.equal((await worker.fetch(post("/subscribe", { subscription: bad }), e)).status, 400);
  assert.equal((await worker.fetch(post("/subscribe", { subscription: { endpoint: SUB.endpoint } }), e)).status, 400);
  assert.equal(e.SUBS.store.size, 0);
  assert.equal(validEndpoint("http://fcm.googleapis.com/x"), false);
  assert.equal(validEndpoint("https://fcm.googleapis.com/fcm/send/x"), true);
  assert.equal(validEndpoint("https://db5p.notify.windows.com/w/?token=x"), true);
});

test("listing and deleting need the admin token", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  assert.equal((await worker.fetch(admin("/subscriptions", "GET", "wrong"), e)).status, 401);
  const [{ id }] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.equal((await worker.fetch(admin(`/subscriptions/${id}`, "DELETE", "wrong"), e)).status, 401);
  assert.equal((await worker.fetch(admin(`/subscriptions/${id}`, "DELETE"), e)).status, 200);
  assert.equal(e.SUBS.store.size, 0);
});

test("an unset admin token locks the admin routes", async () => {
  const e = { ...env(), ADMIN_TOKEN: "" };
  const r = await worker.fetch(new Request("https://w.dev/subscriptions", { headers: { Authorization: "Bearer " } }), e);
  assert.equal(r.status, 401);
});

test("oversized or malformed bodies are refused", async () => {
  const e = env();
  const big = { subscription: { ...SUB, pad: "x".repeat(9000) } };
  assert.equal((await worker.fetch(post("/subscribe", big), e)).status, 400);
  const junk = new Request("https://w.dev/subscribe", { method: "POST", body: "not json" });
  assert.equal((await worker.fetch(junk, e)).status, 400);
});

test("CORS preflight is answered", async () => {
  const r = await worker.fetch(new Request("https://w.dev/subscribe", { method: "OPTIONS" }), env());
  assert.equal(r.status, 204);
  assert.equal(r.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
});

const put = (path, body, token = "t0ken") => new Request(`https://w.dev${path}`, {
  method: "PUT", body: JSON.stringify(body),
  headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } });

test("a new phone gets the default settings: best anywhere, every kind", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  const [s] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.deepEqual(s.prefs, { best: true, kinds: ["storm", "powder", "grade_a", "opens"], mountains: [], regions: [] });
  assert.deepEqual(s.history, []);
});

test("settings sent with the subscription or later are stored, cleaned", async () => {
  const e = env();
  const prefs = { best: false, kinds: ["powder", "bogus"], mountains: ["alta", "<script>"], regions: ["Utah"] };
  await worker.fetch(post("/subscribe", { subscription: SUB, prefs }), e);
  let [s] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.deepEqual(s.prefs, { best: false, kinds: ["powder"], mountains: ["alta"], regions: ["Utah"] });

  const r = await worker.fetch(post("/prefs", { endpoint: SUB.endpoint, prefs: { mountains: ["mammoth"] } }), e);
  assert.equal(r.status, 200);
  [s] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.deepEqual(s.prefs.mountains, ["mammoth"]);
  assert.equal(s.prefs.best, true);
});

test("settings for a phone that isn't subscribed are refused", async () => {
  const e = env();
  const r = await worker.fetch(post("/prefs", { endpoint: SUB.endpoint, prefs: {} }), e);
  assert.equal(r.status, 404);
  assert.equal(e.SUBS.store.size, 0);
});

test("re-subscribing keeps the phone's settings and history", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB, prefs: { mountains: ["alta"] } }), e);
  const [{ id }] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  const history = [{ date: "2026-12-10", kind: "powder", key: "alta", why: "follow" }];
  assert.equal((await worker.fetch(put(`/subscriptions/${id}/history`, { history }), e)).status, 200);
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  const [s] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.deepEqual(s.prefs.mountains, ["alta"]);
  assert.deepEqual(s.history, history);
});

test("history needs the admin token and is cleaned", async () => {
  const e = env();
  await worker.fetch(post("/subscribe", { subscription: SUB }), e);
  const [{ id }] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  const history = [{ date: "2026-12-10", kind: "powder", key: "alta", why: "follow", extra: 1 },
                   { date: "nope", kind: "powder", key: "alta", why: "follow" }];
  assert.equal((await worker.fetch(put(`/subscriptions/${id}/history`, { history }, "wrong"), e)).status, 401);
  await worker.fetch(put(`/subscriptions/${id}/history`, { history }), e);
  const [s] = await (await worker.fetch(admin("/subscriptions"), e)).json();
  assert.deepEqual(s.history, [{ date: "2026-12-10", kind: "powder", key: "alta", why: "follow" }]);
});

test("cleanPrefs caps list sizes and drops duplicates", () => {
  const p = cleanPrefs({ mountains: [...Array(100).keys()].map((i) => `m${i}`).concat(["m1"]) });
  assert.equal(p.mountains.length, 60);
  assert.deepEqual(cleanPrefs(null).kinds, ["storm", "powder", "grade_a", "opens"]);
  assert.deepEqual(cleanPrefs({ kinds: [] }).kinds, []);
});
