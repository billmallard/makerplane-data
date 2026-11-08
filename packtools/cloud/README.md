# Terrain build container (build01 / QNAP Container Station / any Docker host)

Builds the GLO-30 **mip pyramid** and publishes region packs to R2 in one batch
run, then exits. Runs on a home Docker host (build01 by default, the QNAP NAS as
fallback; § Build hosts) or lifts unchanged to AWS ECS/EC2 later — the image is
portable; only the volume path and secrets change.

Pipeline (`entrypoint.sh`): fresh-clone pyEfis + makerplane-data (so ongoing map
work is always current) → `build_terrain_mips.py` (parallel) → `make-terrain
--upload` per region → verify the signed manifest. Idempotent: the mip build
skips tiles already present, so the pyramid **persists on the NAS** and re-runs
are fast — ideal while the map rendering is still being iterated.

## Build hosts: build01 by default, the QNAP as fallback

Two home hosts run this compose file against the same tile tree on the NAS:

| | **build01** (default) | **QNAP** (fallback) |
|---|---|---|
| What | Proxmox VM 103 on the GMKtec M6 Ultra: 6 vCPU, 12 GB, Docker 29 + compose v2 | the NAS itself (V1500B, 4c/8t), Container Station |
| Address | `10.110.10.40` (static, VLAN 10) | `10.110.10.6` |
| Compose dir | `/opt/terrain/cloud` (local disk) | the `packtools/cloud` copy on the NAS |
| `/data` source | NFSv4.1 mount of `10.110.10.6:/pyEfisData` | the native pool path, local |
| Speed | us-central pack 960 s; full 9-pack publish 4 h 32 min (2024edr2) | us-central pack 2,135 s; a full stage about 9 h |

Use **build01 for stage runs and publishes**. Pack building is single-threaded
zipfile DEFLATE, so per-core speed decides it, and build01 is about 2.2x the
QNAP per pack. Use the QNAP only when build01 is down. The two produce the
same packs: a us-central rebuild on build01 matched the QNAP's staged pack in
every one of its 4,044 members except `pack_meta.json`'s zip timestamp (the
build time; its content matched).

### The mount (build01)

build01 mounts `10.110.10.6:/pyEfisData` over NFSv4.1 (an `/etc/fstab`
entry) at the **QNAP's own native path**, `/share/ZFS22_DATA/pyEfisData`. So
the volume line in both compose files
(`/share/ZFS22_DATA/pyEfisData/EarthData:/data`) is the same on both hosts and
the files run unchanged. The QNAP grants `pyEfisData` to `10.110.10.40` read/write with "Squash no
users", so root in a build01 container writes to the NAS as root, the same as
it does on the QNAP. Before a run, check that the mount is up:
`findmnt /share/ZFS22_DATA/pyEfisData` and `ls /share/ZFS22_DATA/pyEfisData/EarthData/glo30hgt | head`.
If the mount is down, the path is an empty local directory and the container
stops with the "TILE_ROOT missing/empty" guard. That message names the QNAP
symlink gotcha below, but on build01 the cause is the NFS mount.

NFS makes the tree scan slower: with nothing to build, the mip-tree walk took
22 min on build01. The pack steps are CPU-bound and gain more than that.

### Keep the two images the same

The image carries `entrypoint.sh` and `retro.sh` (the Dockerfile copies them
in); only pyEfis and the packtools code are cloned at run time. So a change to
either script, or to the Dockerfile, only runs on a host whose image was
rebuilt after it. The two hosts must run the same image, or a fallback run
silently uses older pipeline logic.

Build on one host, then copy the image to the other one:

```bash
# on build01, from /opt/terrain/cloud with the updated packtools/cloud files
docker compose build
docker save makerplane/terrain-build:latest | \
  ssh <qnap-admin>@10.110.10.6 'DOCKER_CONFIG=/share/ZFS22_DATA/pyEfisData/.docker-svc-admin docker load'
# (or the reverse, if the QNAP built it; the QNAP needs the BuildKit and
#  DOCKER_CONFIG workarounds in § QNAP build quirks)

# both hosts: the image id must match
docker image inspect --format '{{.Id}}' makerplane/terrain-build:latest
```

Then start runs with `docker compose up --no-build`. `up --build` rebuilds
from the local build context, so the image can drift from the other host's.
Copy the updated `packtools/cloud/` files (compose files, scripts) to both
compose dirs as well. The image built on 2026-11-03 was copied from the QNAP
this way, and all 7 layers matched.

### `.env` on each host

Each host keeps its own `.env` next to its compose file. On build01 that is
`/opt/terrain/cloud/.env`, on local disk, mode 600. Keep it there: don't copy
it into the NFS-mounted tree, which other hosts and SMB clients of
`pyEfisData` can read. The two copies hold the same values: the R2 keys,
`MINISIGN_SECRET_KEY`, and `EDITION` if you keep it in `.env`. A stale
`EDITION` in one copy is a wrong edition id on a fallback run, so prefer
passing it in the shell. If a key is rotated, update both files, or a
fallback run fails at its first upload. Stage runs
(`STAGE_ONLY=1`) need no secrets; the entrypoint unsets them anyway.

### One job at a time, across both hosts

`entrypoint.sh` and `retro.sh` take `flock` on `/data/.packtools-cloud.lock`.
**That lock only excludes jobs on the same host.** On the QNAP it is a local
lock in the NAS kernel; on build01 the same file is reached over NFS, and that
lock does not exclude the QNAP's local one. Both hosts can hold "the" lock at
once, and then two jobs rewrite the manifest and share the same `pack-scratch/`.

So run **one terrain or retro job at a time across both hosts**. Before you
start one, check that the other host has no job running:

```bash
# on BOTH hosts (QNAP: with DOCKER_CONFIG set, § QNAP build quirks)
docker ps --filter name=terrain-build --filter name=retro-records
```

The manifest has other writers too: the daily `cyclical.yml` run re-signs it,
and it fires 4 to 10 h after its 09:20 UTC cron. Start a long publish only
after that day's scheduled run has finished (§ What to expect).

## ⚠️ The one gotcha: QNAP native volume paths

Container Station runs Docker **natively on the NAS**, so a bind mount's host
side must be the **native storage-pool path**, not a friendly symlink:

```
    /share/ZFS22_DATA/pyEfisData/EarthData     (this NAS; pool ZFS22_DATA)
```

The tiles live on the `pyEfisData` share (SMB `\\10.110.10.6\pyEfisData`), but the
container mount must use the **native pool path** above. That path was resolved from
the share symlink with `readlink -f /share/pyEfisData` → `/share/ZFS22_DATA/pyEfisData`
(on another NAS, re-resolve — the pool differs). **Do NOT use**
`\\10.110.10.6\pyEfisData` (UNC/SMB), `/share/pyEfisData` (symlink), or `Z:\...`
(mapped drive) — Container Station mounts those as an **empty volume and fails
silently**, and the container aborts with the "TILE_ROOT missing/empty" guard.
(Lesson from the OpenClaw Container Station guide.)

## First-run setup

1. **Put the tile tree on the NAS.** Copy `glo30hgt/` (the 3601² `.hgt` tree,
   ~87 GB) to `pyEfisData/EarthData/glo30hgt` over the LAN (`\\10.110.10.6\pyEfisData`).
   The `.mip/` pyramid and `pack-scratch/` are created alongside it on the NAS.
2. **Set the native path.** In `docker-compose.yml`, edit the volume's `<POOL>`
   to match `ls /share/`.
3. **Secrets.** `cp .env.example .env` and fill in the R2 creds (from *CloudFlare
   R2 Bucket Keys.txt*) and `MINISIGN_SECRET_KEY` (the base64 contents of
   `keys/minisign.sec`). `.env` is gitignored — never commit it. Add
   `EDITION=<edition id>` there too (or export it in the shell): it has no
   default, and compose refuses to start without it.
4. **Run it.** In Container Station, "Create Application" from `docker-compose.yml`;
   or over SSH:
   ```bash
   cd /share/<POOL>/.../packtools/cloud
   docker compose up --build          # builds the image, runs once, exits
   docker compose logs -f terrain-build
   ```
   On build01: `cd /opt/terrain/cloud && docker compose up --no-build`, after
   the image sync above.

## QNAP build quirks

Two things fail on the QNAP that work on an ordinary Docker host:

- **BuildKit.** `docker compose up --build` fails with `error creating zfs
  mount ... no such file or directory`. Build with the legacy builder, then
  start without building:
  ```bash
  DOCKER_BUILDKIT=0 docker build -t makerplane/terrain-build:latest .
  docker compose up -d --no-build
  ```
- **`DOCKER_CONFIG`.** The QNAP docker wrapper cannot create the svc-admin
  user's config dir, so docker commands there need
  `DOCKER_CONFIG=/share/ZFS22_DATA/pyEfisData/.docker-svc-admin`.

## What to expect

- **Build:** ~1–1.5 h on the V1500B (4c/8t, `JOBS=8`) for the full NA tree the
  first time; minutes on re-runs (mips already built). ~+31 GB on the NAS.
- **Upload:** ~150 GB to R2 over your home uplink (~25–40 min at gigabit).
- **Success:** the log ends with `signature OK` and exit 0. Verify from anywhere:
  `curl -s https://navdata.aerocommons.org/manifest.json` → 8 `terrain-*` entries.
- **Don't** run this while the nightly navdata cron is publishing (both rewrite
  the manifest). `cyclical.yml` is scheduled for 09:20 UTC but has fired 4 to
  10 h late, so wait until that day's scheduled run has finished, not until a
  clock time.
- **Don't** run it while a terrain or retro job runs on the other build host
  (§ One job at a time, across both hosts).

## Config (env, all optional except secrets and `EDITION`)

| var | default | notes |
|---|---|---|
| `EDITION` | **required** | terrain edition id; reuse the cycle tag → in-place manifest upsert (see docs/terrain.md). No default: one went stale when `2024ed` was retracted, so an unset `EDITION` fails the run |
| `TILE_ROOT` | `/data/glo30hgt` | native tiles (container path; `/data` is the NAS mount) |
| `PACK_OUT` | `/data/pack-scratch` | region packs stage here, deleted after each upload |
| `REGIONS` | all 8 NA | space-separated subset to (re)publish just some |
| `JOBS` | `0` (all cores) | mip build workers; `8` set in compose for the V1500B |
| `CODE_REF` | `dev` | branch/tag of makerplane-data to clone (and of pyEfis unless `PYEFIS_REF` is set). Ignored for makerplane-data when `entrypoint.sh` runs from inside a checkout -- that checkout is used |
| `PYEFIS_REF` | `CODE_REF` (`dev` when run from a checkout) | branch/tag of pyEfis to clone |
| `WATER_PACK_ID` | `water-na` | published water pack whose water.sqlite step 2b rasterizes into `.wmask` masks |
| `WATER_CACHE` | `/data/water-cache` | where that pack is cached (sha-checked, reused across runs) |
| `STAGE_ONLY` | `0` | `1` = build every pack but upload and sign nothing; packs stay in `$PACK_OUT/packs/`, each is diffed against `PREV_EDITION` and run through a publish-gate preview. Needs no R2 or signing secret. See docs/terrain.md, "Mask-only re-edition" |
| `PREV_EDITION` | none (required with `STAGE_ONLY=1`) | the published terrain edition the staged packs are diffed against |
| `FORCE_WMASK` | `0` | `1` = rebuild every `.wmask` (`build_water_masks.py --force`). Refused without `STAGE_ONLY=1` |
| `WMASK_BORDERS` | `us-central=N27W097:5:SE` | stage mode: `region=TILE:LEVEL:EDGES` border posts that must be water (`packtool wmask-diff --border`) |

## Retro records (TV4): `docker-compose.retro.yml`

The same image writes a `retro` pack-report/1 record for every live manifest
entry published without one, and binds it in the signed manifest
(docs/pack_report.md, "Retro records (TV4)"). One pack at a time: download to
`/data/retro-scratch`, check the sha256 against the signed manifest, run the
checks, upload and bind the record, write `schema/<kind>.json`, delete the
pack. The largest live pack is ~7.7 GB; the scratch never holds more.

```bash
cd /share/<POOL>/.../packtools/cloud
docker compose -f docker-compose.retro.yml up --build
docker compose -f docker-compose.retro.yml logs -f retro-records
```

- **Memory:** capped at 4 GB (`RETRO_MEM`); AER-2644 is why.
- **Never alongside a terrain build:** both take `/data/.packtools-cloud.lock`;
  the second to start exits 3. That holds on one host only: the lock does not
  reach from build01 to the QNAP or back (§ One job at a time, across both
  hosts).
- **When:** after the nightly cyclical publish (09:20 UTC) has finished. A
  publish overlapping a bind can drop it; the run says so and a re-run fixes it.
- **Re-runnable:** bound entries are skipped. `ONLY=id,id` limits a run.
- **Output:** `/data/retro-out/<stamp>/` holds every record, the schemas and
  `retro-summary.json`, which lists each failing check. Failures are
  published as found; the run never retracts or edits a pack.

## Portability

Same image on AWS: swap the bind mount for the tile tree source (EBS, or Path B
deriving it from Copernicus) and pass the same env. See `docs/terrain_cloud_build_plan.md`.
