// One-time setup for GladeGrade snow alerts. Run from push-worker/ after
// `npx wrangler login`:
//
//   node setup.mjs
//
// It creates the Cloudflare KV store, deploys the Worker, makes the two secrets
// (the Worker's admin token and the VAPID signing key) and hands them straight to
// Cloudflare and GitHub over stdin, so they never appear on screen or in a file.
// What it prints at the end (the Worker URL and the VAPID public key) is public:
// those two go into web/js/config.js.
//
// Needs: Node 18+, a Cloudflare account (`npx wrangler login`), and the GitHub CLI
// signed in with access to the ski-conditions repo. Safe to re-run; re-running
// makes new secrets, which signs out every phone that had alerts on.

import { execSync, spawnSync } from "node:child_process";
import { createECDH, randomBytes } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const REPO = "lpfitzgerald98-star/ski-conditions";
// stdin stays attached: on a new account wrangler may ask to register a
// workers.dev subdomain.
const sh = (cmd) => execSync(cmd, { encoding: "utf8", stdio: ["inherit", "pipe", "inherit"] });
const withStdin = (cmd, args, input) => {
  const r = spawnSync(cmd, args, { input, encoding: "utf8", shell: process.platform === "win32" });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} failed:\n${r.stderr || r.stdout}`);
};

console.log("Checking Cloudflare login...");
sh("npx wrangler whoami");

let toml = readFileSync("wrangler.toml", "utf8");
if (toml.includes("REPLACE_WITH_KV_NAMESPACE_ID")) {
  console.log("Creating the KV store for subscriptions...");
  const out = sh("npx wrangler kv namespace create SUBS");
  const id = out.match(/id\s*=\s*"([0-9a-f]{32})"/)?.[1] || out.match(/"id":\s*"([0-9a-f]{32})"/)?.[1];
  if (!id) throw new Error(`Couldn't find the namespace id in wrangler's output:\n${out}`);
  toml = toml.replace("REPLACE_WITH_KV_NAMESPACE_ID", id);
  writeFileSync("wrangler.toml", toml);
}

console.log("Deploying the Worker...");
const deployed = sh("npx wrangler deploy");
const url = deployed.match(/https:\/\/[\w.-]+\.workers\.dev/)?.[0];
if (!url) throw new Error(`Couldn't find the Worker URL in wrangler's output:\n${deployed}`);

console.log("Setting the admin token on Cloudflare and GitHub...");
const token = randomBytes(32).toString("base64url");
withStdin("npx", ["wrangler", "secret", "put", "ADMIN_TOKEN"], token);
withStdin("gh", ["secret", "set", "PUSH_ADMIN_TOKEN", "--repo", REPO], token);

console.log("Making the VAPID signing key and storing it on GitHub...");
const ecdh = createECDH("prime256v1");
ecdh.generateKeys();
withStdin("gh", ["secret", "set", "VAPID_PRIVATE_KEY", "--repo", REPO], ecdh.getPrivateKey().toString("base64url"));
const publicKey = ecdh.getPublicKey().toString("base64url");

withStdin("gh", ["variable", "set", "PUSH_API", "--repo", REPO], url);

console.log(`
Done. Both values below are public, so it's fine to paste them into a chat:

  PUSH_API         = "${url}"
  VAPID_PUBLIC_KEY = "${publicKey}"

They go into web/js/config.js. The Worker URL is also saved as the PUSH_API
variable on GitHub, so the daily build can find it.`);
