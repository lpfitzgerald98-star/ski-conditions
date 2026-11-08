// Run: node --test push-worker/test/
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { validEndpoint } from "../src/index.js";

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
  const big = { subscription: { ...SUB, pad: "x".repeat(5000) } };
  assert.equal((await worker.fetch(post("/subscribe", big), e)).status, 400);
  const junk = new Request("https://w.dev/subscribe", { method: "POST", body: "not json" });
  assert.equal((await worker.fetch(junk, e)).status, 400);
});

test("CORS preflight is answered", async () => {
  const r = await worker.fetch(new Request("https://w.dev/subscribe", { method: "OPTIONS" }), env());
  assert.equal(r.status, 204);
  assert.equal(r.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
});
