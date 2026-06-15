# CI build pipeline — move heavy pack builds off the workstation

**Status: planned.** The heavy data-pack builds (terrain, water, roads) run on
Bill's workstation today — hours of compute, tens of GB of disk.

**The goal is an autonomous, self-updating pipeline** — one that refreshes the
published data on a schedule with **no human in the loop**, keeping R2 current
with upstream (FAA cycles, OSM/Geofabrik, Copernicus). Getting the builds off
the workstation is a *consequence* of that, not the point. Expect **live
iteration** on the first runs (disk limits, Geofabrik behavior under CI, runner
sizing) — author the workflows, then shake them out against real Actions logs.

## You already have the template: `cyclical.yml`

The navdata pipeline is **already autonomous**. `cyclical.yml` runs on a daily
cron, fetches the current FAA NASR/DOF (and next AIRAC), **skips anything
already published** (change-detection), builds, signs, and uploads to R2 — fully
hands-off, keyed to the 28-/56-day FAA cycles. The whole job here is to extend
that *same pattern* to terrain/water/roads, each with the right cadence and a
source-changed check.

## What "autonomous" requires beyond "build in CI"

1. **Schedules** — a cron per data type at its natural cadence (below).
2. **Change-detection / idempotency** — only rebuild + publish when the source
   actually changed, so editions don't churn for no reason. (navdata's
   skip-if-present is the model; for OSM use Geofabrik's extract date, for
   terrain the GLO-30 edition tag.)
3. **Automatic edition bump + manifest swap on publish** — a refresh must bump
   the non-cyclical edition and drop the superseded entry so the Pi auto-pulls
   and prunes the old (done manually today in `work/upload_water_r2.py`; must
   become a workflow step). The updater compares cycle, not hash.
4. **Failure alerting** — "no intervention" only works if a human gets pinged
   *when it breaks*. Wire GitHub Actions failure notifications (and optionally
   Slack/email) so a silent stall doesn't leave stale data.
5. **Quality baked into the build** — the DP simplification, min-area, and
   `--keep-fclass water reservoir` filters live in `build_water_db.py`, so an
   automated rebuild can't regress to fake-lake artifacts. (This is *why* these
   fixes belong in code, not one-off cleanups.)

### Cadence per type
- **navdata** — daily (done). FAA 28-/56-day cycles.
- **water / roads** — monthly or quarterly; OSM/Geofabrik refresh continuously,
  Geofabrik republishes extracts ~weekly. Change-detect on Geofabrik's date.
- **terrain** — near-static (terrain doesn't move); check for a *new GLO-30
  edition* (rare, ~yearly) and rebuild only then.

## Why GitHub Actions, not Cloudflare

R2 is the **storage/distribution** layer and stays exactly as-is. Cloudflare's
*compute* is Workers — short-lived, CPU-time-capped, no real disk — so it can't
run an hours-long GDAL / shapefile / multi-million-polygon job. The build
compute belongs in **GitHub Actions** (or any VM/self-hosted runner); R2 remains
the upload target. "Buy GitHub compute" (larger runners) is the right lever; no
extra Cloudflare spend is involved.

## What already works (don't rebuild it)

- **`cyclical.yml`** — the daily FAA navdata pipeline already runs in CI:
  fetch (NASR/DOF) → build → **sign with the minisign key** → **upload to R2**.
  Proves auth + signing + upload from Actions end-to-end.
- **`water.yml`** — a `workflow_dispatch` that builds a *small* scope (default
  `texas`) via `fetch_geofabrik_water.py` + `build_water_db` and runs
  `build-pack --upload`. The loop works; it just hasn't been scaled.
- **`terrain.yml`** — placeholder (`exit 1`), workstation-only note.
- **Secrets are set**: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
  `MINISIGN_SECRET_KEY`. Public key is embedded in `pyefis_data`.
- **Build tools exist**: pyEfis `tools/build_water_db.py` (now Douglas-Peucker),
  `tools/build_highway_db.py`, `tools/fetch_geofabrik_water.py`; packtools
  `make_terrain`, `build-pack`, `upload.R2Store`, `run_cyclical`.

So the unsolved part is **only the heavy-data plumbing**: source fetch, disk,
time, and rate-limits at full scale.

## The three builds and their real constraints

| Build | Source | Output | Intermediates | Hard parts |
|-------|--------|--------|---------------|-----------|
| **Roads** | Geofabrik per-state `gis_osm_roads_free_1` | ~108 MB | ~10 GB bundles | Geofabrik rate-limit; shares bundles with water |
| **Water** | Geofabrik per-state `gis_osm_water_a_free_1` + global OSM coastline | ~2.5 GB | ~10 GB bundles | Disk; Geofabrik rate-limit; CA pinned to 2018 shapefile; DP makes it CPU-heavier/slower |
| **Terrain** | Copernicus GLO-30 tiles | ~90 GB (8 region packs) | the 90 GB itself | Disk (won't fit one runner); Copernicus fetch |

### Geofabrik rate-limit (water + roads)
Geofabrik silently 302-redirects to its homepage after ~110 bulk downloads
(returns a ~3 KB HTML page → `BadZipFile`). CI must fetch **sequentially with
retries** and **cache** bundles (`actions/cache`, keyed by Geofabrik's
`-latest-` date) so re-runs don't re-download. **California has no current
shapefile** (Geofabrik dropped `.shp.zip` for it after 2018) — pin
`california-180101-free.shp.zip` (already special-cased in `build_na_water.py`).

### Disk
Standard runners give ~14 GB free. Water (~10 GB intermediates, cleaned per
state) fits a **larger runner**; terrain (90 GB) does **not** fit one runner.

## Proposed workflow shape

- **Trigger**: `workflow_dispatch` with inputs (scope/regions, `--min-area-km2`,
  edition tag). Optionally a slow `schedule` for periodic refresh.
- **Roads / Water** — one job on a **larger runner**; fetch states sequentially
  (retry + `actions/cache`), clean each bundle after extract (the
  `build_na_*` scripts already do), build, then `build-pack --upload`. Roads and
  water can **share** the cached Geofabrik bundles in one workflow to fetch once.
- **Terrain** — a **matrix over the 8 regions** (`us-west/central/east/south`,
  `alaska`, `canada-west/east`, `mexico-central-america`), each job fetching
  only its region's GLO-30 tiles, building one region pack, uploading. Each job
  fits a runner; they run in parallel.
- **Edition bump on every rebuild**: the on-Pi updater compares the **cycle**
  string, not the content hash, so a rebuild must use a **new edition tag**
  (e.g. `2026q2r2`) and the publish step must **drop the superseded manifest
  entry** for that id, so `select()` returns the new one and the Pi auto-pulls +
  prunes the old. (Same-cycle re-upload would not trigger a re-pull.)
- **Manifest serialization**: every upload re-signs the shared `manifest.json`.
  **Never run two upload jobs concurrently** — the terrain matrix must funnel
  through a single publish step (or upload sequentially), or they'll clobber
  each other's manifest. Build in parallel, publish serially.

## Auth / secrets

R2 + minisign already set. Confirm whether the Copernicus GLO-30 source needs
credentials — if fetched from the AWS open-data mirror it's anonymous; if from
the Copernicus portal it may need a (free) account → add a secret then.

## Cost

Standard runners are effectively free (2,000 min/mo). Larger runners are
~pennies/minute, so a full water or terrain rebuild is **a few dollars a run** —
negligible for an occasional rebuild. The terrain matrix is 8× short parallel
jobs rather than one long one.

## Risks / live-debugging items (expected — not one-shot)

- Runner **disk** limits, especially terrain → tune region chunking / runner size.
- **Geofabrik** behavior from CI IP ranges (rate-limit, 302s) → sequential +
  retry + cache; consider a mirror if it's hostile.
- **Copernicus/GLO-30** fetch source, auth, and throughput in CI.
- **Wall-time** vs the 6 h job limit (DP made water slower) → split by state
  group if needed.
- **Manifest concurrency** (see above) — the most likely silent corruption bug.

## Phased plan

1. **Roads** first — smallest output, exercises the full
   fetch → build → sign → upload → R2 loop on a real heavy-ish build, including
   the Geofabrik cache + rate-limit handling.
2. **Water** — reuse the roads' cached bundles, larger runner, DP build, edition
   bump + old-entry drop.
3. **Terrain** — per-region matrix + Copernicus fetch + serial publish.
4. **Make it autonomous** — add the `schedule:` crons, source-changed detection
   (so it only rebuilds/publishes on real upstream changes), automatic
   edition-bump + manifest-swap in the publish step, and **failure alerting**.
   Update `docs/{water,roads,terrain}.md` runbooks to say "the workflow keeps
   this current" instead of "run on the workstation."

Phases 1–3 are still `workflow_dispatch` (manual trigger) so each build can be
shaken out by hand first; phase 4 flips them to scheduled + self-publishing.

## Related

- `.github/workflows/{cyclical,water,terrain}.yml` — model on `cyclical.yml`.
- `docs/{terrain,water,roads}.md` — current workstation runbooks.
- `packtools/run_cyclical.py`, `packtools/upload.py` (R2Store), `cli build-pack`.
- The edition-bump + manifest re-pull mechanics (this doc, "Proposed shape").
