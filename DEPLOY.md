# Deploying

Target: **Fly.io**. Reasoning, then the steps.

## Why Fly, over Render and Railway

Three requirements drive the choice: SSE that isn't buffered, a SQLite file that
survives redeploys, and a process that stays warm.

**SSE.** Fly's proxy streams responses straight through. Render fronts services
with a proxy that has historically buffered streaming responses, which is the
classic "works on localhost, map never fills in on prod" failure — the browser
gets nothing until the whole 24-second stream finishes, then all 79 events at
once. We set `X-Accel-Buffering: no` in `api.py` regardless (it's what nginx
honours), so the app can move hosts without breaking, but on Fly it's belt and
braces rather than load-bearing.

**SQLite persistence.** All three offer volumes, but the semantics matter. A Fly
volume is block storage bound to exactly one machine. That's a limitation
everywhere else and an advantage here: SQLite *cannot* be shared across machines,
so being forced into a single machine is the correct topology, not a compromise.
`fly.toml` pins `max_machines_running = 1` for this reason. Render's persistent
disks require a paid instance type and also pin you to one instance; Railway's
volumes work but the free tier sleeps.

**Staying warm.** The whole point of the SSE design is a 44ms first paint from
the cached snapshot. A host that cold-starts (Render free, Railway free) makes
that a ~10s boot, and drops any open SSE connection when it sleeps. `fly.toml`
sets `auto_stop_machines = false` and `min_machines_running = 1`.

**Cost.** One `shared-cpu-1x` machine at 1GB plus a 1GB volume. Not free — expect
roughly $5–7/month. The 256MB default gets OOM-killed mid-stream: scoring loads a
station's full history into pandas (Fernie alone is 39k rows) across three worker
threads, and a roster refresh peaked around 400MB locally.

## What ships, and where the data lives

`data/ski.db` is 90MB and **gitignored**, but it *is* baked into the image (see
`.dockerignore`) as a seed. On first boot `docker-entrypoint.sh` copies it onto the
empty volume. On every later deploy the volume already holds the live DB — with
fresher observations and a warm score cache — and the entrypoint leaves it alone.

That guard is the difference between a redeploy and a data loss. It's tested:

```
first boot (empty volume):  seeding /data/ski.db from image snapshot
redeploy (has live data):   existing database at /data/ski.db, leaving it alone
```

`SKI_DB_PATH=/data/ski.db` (set in `fly.toml` `[env]`) points `config.DB_PATH` at
the volume. Without it the app writes to the container's ephemeral filesystem and
silently loses 1.2M observations on every deploy.

## Steps

Run these from `projects/ski-conditions/`. Nothing here has been run for you —
neither `flyctl` nor Docker is installed on this machine.

**1. Install flyctl and sign in.**

```powershell
# Windows (PowerShell)
pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"
fly auth signup     # or: fly auth login
```

Fly requires a card on file even for small workloads.

**2. Create the app** (don't deploy yet — the volume must exist first).

```bash
fly launch --no-deploy --copy-config --name ski-conditions --region sea
```

`--copy-config` makes it use the committed `fly.toml` instead of generating one.
If the name is taken, pick another and update `app = ` in `fly.toml`.

**3. Create the volume**, in the same region as the app.

```bash
fly volumes create ski_data --region sea --size 1
```

1GB holds the 90MB DB with room for the WAL sidecars and years of growth. The
volume name must match `[mounts].source` in `fly.toml`.

**4. Deploy.**

```bash
fly deploy
```

The image is ~400MB (Python + pandas + the 90MB seed DB); the first build takes a
few minutes. Watch for `seeding /data/ski.db from image snapshot` in the output.

**5. Verify.** Three things, in order of what's most likely to be wrong.

```bash
fly status                       # one machine, state=started
fly logs                         # look for the seeding line, then "Application startup complete"
curl https://ski-conditions.fly.dev/health
```

Then confirm SSE is genuinely streaming and not buffered. This is the check that
matters — a buffered stream returns the same bytes, just all at the end:

```bash
curl -N -sS https://ski-conditions.fly.dev/live/stream | head -c 400
```

`-N` disables curl's own buffering. You should see `event: snapshot` appear
**immediately**, then `event: mountain_update` lines trickle in over ~24 seconds.
If everything arrives at once after a long pause, the platform is buffering.

Confirm the volume is actually mounted and holding the DB:

```bash
fly ssh console -C "ls -la /data"
# expect ski.db (~90MB), plus ski.db-wal and ski.db-shm once traffic hits it
```

**6. Prove persistence.** Worth doing once, because the failure is silent:

```bash
fly deploy                                   # redeploy
fly logs | grep "leaving it alone"           # entrypoint kept the live DB
```

**Live URL:** `https://ski-conditions.fly.dev` (assuming the app name is free).

## Things that will bite

- **Never raise `--workers` above 1.** Two workers open the same SQLite file and
  each spawn their own live-stream thread pool. Scoring is CPU-bound and holds the
  GIL, so a second worker buys lock contention, not throughput. Scale the machine,
  not the worker count.
- **Never scale to 2+ machines.** They cannot share the volume. Fly will either
  refuse or give the second machine its own empty volume, and you'll serve two
  divergent datasets depending on which one the proxy picks.
- **The seed DB goes stale.** It's a snapshot from whenever you last built. The
  live stream refreshes forecasts and the score cache, but `raw_observations` only
  grows when the ingest runs. Schedule `python cli.py ingest` (or equivalent) on
  the machine, or redeploy periodically and accept a gap.
- **`fly volumes` snapshots are not backups you've tested.** If the DB matters,
  pull a copy: `fly ssh sftp get /data/ski.db`.
