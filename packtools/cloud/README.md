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

## What to expect

- **Build:** ~1–1.5 h on the V1500B (4c/8t, `JOBS=8`) for the full NA tree the
  first time; minutes on re-runs (mips already built). ~+31 GB on the NAS.
- **Upload:** ~150 GB to R2 over your home uplink (~25–40 min at gigabit).
- **Success:** the log ends with `signature OK` and exit 0. Verify from anywhere:
  `curl -s https://navdata.aerocommons.org/manifest.json` → 8 `terrain-*` entries.
- **Don't** run this while the nightly navdata cron is publishing (both rewrite
  the manifest). Pick a quiet window.

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
  the second to start exits 3.
- **When:** after the nightly cyclical publish (09:20 UTC) has finished. A
  publish overlapping a bind can drop it; the run says so and a re-run fixes it.
- **Re-runnable:** bound entries are skipped. `ONLY=id,id` limits a run.
- **Output:** `/data/retro-out/<stamp>/` holds every record, the schemas and
  `retro-summary.json`, which lists each failing check. Failures are
  published as found; the run never retracts or edits a pack.

## Portability

Same image on AWS: swap the bind mount for the tile tree source (EBS, or Path B
deriving it from Copernicus) and pass the same env. See `docs/terrain_cloud_build_plan.md`.
