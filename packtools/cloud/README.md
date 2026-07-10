# Terrain build container (QNAP Container Station / any Docker host)

Builds the GLO-30 **mip pyramid** and publishes region packs to R2 in one batch
run, then exits. Runs on a QNAP NAS (free, at home) or lifts unchanged to AWS
ECS/EC2 later — the image is portable; only the volume path and secrets change.

Pipeline (`entrypoint.sh`): fresh-clone pyEfis + makerplane-data (so ongoing map
work is always current) → `build_terrain_mips.py` (parallel) → `make-terrain
--upload` per region → verify the signed manifest. Idempotent: the mip build
skips tiles already present, so the pyramid **persists on the NAS** and re-runs
are fast — ideal while the map rendering is still being iterated.

## ⚠️ The one gotcha: QNAP native volume paths

Container Station runs Docker **natively on the NAS**, so a bind mount's host
side must be the **native storage-pool path**, not a friendly symlink:

```
    /share/<POOL>/pyEfisData/EarthData    e.g.  /share/CACHEDEV1_DATA/pyEfisData/EarthData
```

The tiles live on the `pyEfisData` share (SMB `\\10.110.10.6\pyEfisData`), but the
container mount must use the **native pool path** above. Find `<POOL>` by SSH'ing
the NAS and running `ls /share/` (common: `CACHEDEV1_DATA`, `ZFS19_DATA`). **Do NOT
use** `\\10.110.10.6\pyEfisData` (UNC/SMB), `/share/pyEfisData` (symlink), or `Z:\...`
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
   `keys/minisign.sec`). `.env` is gitignored — never commit it.
4. **Run it.** In Container Station, "Create Application" from `docker-compose.yml`;
   or over SSH:
   ```bash
   cd /share/<POOL>/.../packtools/cloud
   docker compose up --build          # builds the image, runs once, exits
   docker compose logs -f terrain-build
   ```

## What to expect

- **Build:** ~1–1.5 h on the V1500B (4c/8t, `JOBS=8`) for the full NA tree the
  first time; minutes on re-runs (mips already built). ~+31 GB on the NAS.
- **Upload:** ~150 GB to R2 over your home uplink (~25–40 min at gigabit).
- **Success:** the log ends with `signature OK` and exit 0. Verify from anywhere:
  `curl -s https://navdata.aerocommons.org/manifest.json` → 8 `terrain-*` entries.
- **Don't** run this while the nightly navdata cron is publishing (both rewrite
  the manifest). Pick a quiet window.

## Config (env, all optional except secrets)

| var | default | notes |
|---|---|---|
| `EDITION` | `2024ed` | reuse the cycle tag → in-place manifest upsert (see docs/terrain.md) |
| `TILE_ROOT` | `/data/glo30hgt` | native tiles (container path; `/data` is the NAS mount) |
| `PACK_OUT` | `/data/pack-scratch` | region packs stage here, deleted after each upload |
| `REGIONS` | all 8 NA | space-separated subset to (re)publish just some |
| `JOBS` | `0` (all cores) | mip build workers; `8` set in compose for the V1500B |
| `CODE_REF` | `dev` | branch/tag of pyEfis + makerplane-data to run |

## Portability

Same image on AWS: swap the bind mount for the tile tree source (EBS, or Path B
deriving it from Copernicus) and pass the same env. See `docs/terrain_cloud_build_plan.md`.
