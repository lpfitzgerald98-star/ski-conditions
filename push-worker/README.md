# gladegrade-push

Stores which devices turned on GladeGrade's snow alerts. It's a Cloudflare
Worker with a KV store, and it runs on Cloudflare's free plan.

**How alerts work**

- **Turning alerts on:** the bell in the app (`web/js/alerts.js`) subscribes the
  device to web push and sends the subscription here.
- **Choosing what to hear about:** the bell opens alert settings. Each device
  follows mountains (the ☆ Follow button on a card) and regions, picks the kinds
  of alert, and can also get the best snow anywhere. The settings are stored with
  the subscription (`POST /prefs`).
- **Choosing alerts:** after each site build, `scripts/push_alerts.py` picks each
  device's alert: its best event at a mountain it follows, otherwise (if it wants
  it) the best snow anywhere. At most two a day per device, and each device's
  recent alerts are kept with its subscription so nothing repeats.
- **Sending:** the same script fetches the subscription list from this Worker,
  using a bearer token, and sends the alert from GitHub Actions. The signing key
  stays in GitHub secrets, so this Worker never sees it.

## One-time setup

1. Create a free Cloudflare account at https://dash.cloudflare.com/sign-up.
2. From this folder, run:
   ```
   npx wrangler login
   node setup.mjs
   ```
   `setup.mjs` does the rest:
   - creates the KV store
   - deploys the Worker
   - makes the admin token and VAPID key and stores them as Cloudflare and
     GitHub secrets, without displaying them
   - saves the Worker URL as the `PUSH_API` repo variable
3. Put the two public values it prints into `web/js/config.js` (`PUSH_API`,
   `VAPID_PUBLIC_KEY`). The bell appears once both are set.

## Tests

```
node --test test/worker.test.mjs
```
