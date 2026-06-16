# makerplane-data

A Garmin-style **navigation-data currency system** for [pyEfis](https://github.com/makerplane/pyEfis)
and other MakerPlane / AeroCommons avionics. Subscribe once, pull over WiFi
at the hangar (or import from a USB stick), and the EFIS tells you when
something is stale.

This repo covers **reference-data currency only** — terrain, airports,
obstacles, instrument procedures, water, charts. It is *not* the runtime
flight-data bus; that contract lives in `canfix.json` / FIX-Gateway.

> Status: **Phases A–F complete and live** ✅ — the data contract, the daily
> build pipeline, the on-Pi updater, terrain + water + road packs, the public
> website with an on-device pack picker, and the in-EFIS DATA flag, all proven
> end-to-end against live FAA/OSM data and the production origin
> (**`navdata.aerocommons.org`**, Cloudflare R2, zero egress). Current release:
> **0.1.7**. CIFP packs remain deferred (GPL indexer). See
> [docs/data_manager_implementation.md](docs/data_manager_implementation.md)
> for the full phase plan (A–G).

## The one idea

The signed **`manifest.json` is the contract** between three independent legs.
Freeze its schema and each leg can be built and swapped on its own:

```
  LEG 1  BUILD PIPELINE     GitHub Actions: fetch FAA/Copernicus/OSM,
  (packtools/)              build packs, embed pack_meta, sign the manifest
        │  packs + manifest.json + manifest.json.minisig
        ▼
  LEG 2  DISTRIBUTION       Cloudflare R2 (zero egress) at
                            navdata.aerocommons.org; static site + picker
        │  HTTPS  /  USB sneakernet
        ▼
  LEG 3  ON-PI UPDATER      pyefis-data: verify signature -> verify sha256
  (pyefis_data/)            -> stage -> atomic symlink swap -> DATA flag
```

## Trust chain

```
keys/minisign.pub (committed)
   └─ verifies ─▶ manifest.json.minisig  (ed25519, minisign format)
        manifest carries ─▶ per-pack sha256 ─▶ verifies each .pack
```
Compromising distribution is not enough to push a malicious pack — that
needs the offline secret key. The Pi treats **any** verification failure as
"do not install" and leaves the live data untouched (verify-then-rename).

## Packages

| Path | Leg | Phase | What |
|---|---|---|---|
| `packtools/` | 1 | A/B/D | pack builder: cycles, signing, pack_meta, manifest, regions, sources, fetch, build, upload, orchestrator, terrain (`make-terrain`), CLI |
| `.github/workflows/` | 1/2 | B | `ci.yml` (tests + dry-run), `cyclical.yml` (daily build+sign+upload), water/terrain dispatch |
| `pyefis_data/` | 3 | C/F | on-Pi updater: config, core (Remote/Inventory/Updater), CLI (status/catalog/sources/drives/update/import/verify), systemd units |
| `site/` | 2 | E | static Cloudflare site + on-device pack picker (`data.yaml` sample) |
| `regions.yaml` | — | A | region bboxes for terrain grouping & Pi region-of-interest |
| `keys/minisign.pub` | — | A | signing public key (committed; secret never is) |

Pack kinds shipping today: **navdata** (airports/runways), **obstacles** (FAA
DOF), **terrain** (Copernicus GLO-30 region tiles), **water** (OSM coastlines/
lakes/reservoirs, ODbL), **highways** (OSM motorway/trunk, ODbL). CIFP deferred.

## Phase A — what's here now

`packtools` is the data contract, exercisable today:

- **`cycles.py`** — FAA 28-day AIRAC + 56-day DOF arithmetic with AIRAC-id
  labels. Clean reimplementation of the (correct but cryptic) logic in
  `faa-cifp-data/download.py`, anchored at the verified `2024-04-18 = AIRAC
  2404` and pinned with unit tests.
- **`signing.py`** — ed25519 signing/verification in pure PyNaCl, formatted
  to the **minisign** spec (legacy `Ed` signatures + trusted-comment global
  signature). One implementation shared by the builder and the Pi. The
  secret key is an unencrypted base64 blob (for unattended CI), stored in a
  GitHub Actions secret / offline backup — never committed.
- **`packmeta.py`** — the self-describing header inside every pack: a
  `pack_meta` table (sqlite packs) or `pack_meta.json` member (zip/tile
  packs), so a lone pack file still identifies itself.
- **`manifest.py`** — the catalog schema, canonical (stable-bytes)
  serialization, strict validation, currency-window queries, and
  old-cycle pruning.
- **`regions.py` / `regions.yaml`** — 8 North-America region groups + tile
  assignment by SW corner.
- **`cli.py`** — `packtool genkey | build-pack | verify`.

### Try it

```bash
pip install -e .

# one-time throwaway keypair (commit keys/minisign.pub only)
packtool genkey --out keys

# turn an already-built sqlite into a signed, manifest-registered pack
packtool build-pack path/to/obstacles.sqlite \
    --id obstacles-conus --kind obstacles \
    --attribution "FAA DOF (public domain)" --regions conus \
    --sec keys/minisign.sec --out work

# verify the manifest the way the Pi will
packtool verify work/manifest.json --pub keys/minisign.pub
```

```bash
PYTHONPATH=. python -m pytest        # 108 tests, ~3s
```

## Phase B — the daily build pipeline (Leg 1)

`packtools` now fetches, builds, signs, and uploads packs unattended:

- **`sources.py`** — every upstream FAA URL in one place, built from the
  cycle (current + next). All patterns verified live 2026-06-14:
  NASR APT CSV `…/28DaySub/extra/<DD_Mon_YYYY>_APT_CSV.zip`,
  CIFP `…/cifp/CIFP_<YYMMDD>.zip`, DOF `…/Obst_Data/DAILY_DOF_CSV.ZIP`.
- **`fetch.py`** — resumable download + zip extract.
- **`build/`** — the interim tool-sharing shim: invokes pyEfis's
  `build_airport_db.py` / `build_obstacle_db.py` by path (`PYEFIS_TOOLS_DIR`).
- **`upload.py`** — `ObjectStore` interface with an R2 backend (S3 API,
  lazy boto3, `HEAD`-to-skip) and a `LocalStore` for dry-run/tests.
- **`run_cyclical.py`** — the orchestrator: per source → compute cycle →
  skip-if-present → fetch → build → embed → sha256 → upload → upsert
  manifest → prune → sign → upload. Idempotent; a not-yet-published *next*
  cycle is logged, not fatal. `--dry-run`, `--no-upload`, `--date`, `--only`.

Proven end-to-end against **live FAA data**: both the current (AIRAC 2606)
and next (2607) airports packs built — 19,407 airports / 23,178 runways —
manifest signed and verified, no upload target required.

```bash
# dry-run (no network, no secrets) — what the daily cron would build today
python -m packtools.run_cyclical --dry-run

# real build into a local R2 mirror (needs PYEFIS_TOOLS_DIR + a key)
PYEFIS_TOOLS_DIR=/path/to/pyEfis/tools \
python -m packtools.run_cyclical --no-upload --only airports-conus \
    --sec keys/minisign.sec --work work/live
```

## Phase C — the on-Pi updater (Leg 3)

`pyefis-data` keeps a Pi 5 current. It shares `packtools.signing` /
`packtools.manifest` with the build side, so verify can never drift from sign.

- **`config.py`** — `~/.makerplane/pyefis/data.yaml` (base_url, packs, root,
  auto_update, stage_next). Construct-never-raises: a missing/bad config yields
  defaults, never an exception.
- **`core.py`** — `Updater` with the safety contract: the manifest signature is
  verified (embedded public key) *before* it's trusted or cached; a pack is
  downloaded to `staging/`, **sha256-verified against the signed manifest, then
  moved into place and `current` atomically symlink-flipped** — a bad
  download/signature can never disturb the live data. Offline falls back to the
  last good (still-verified) cached manifest. Pre-stages the next AIRAC cycle so
  rollover is seamless.
- **`cli.py`** — `pyefis-data status | catalog | sources | drives | update |
  import <dir> | verify` (all with `--json`), production public key embedded.
  `update --only id,id --progress` installs exactly a selection, persists it to
  `data.yaml`, and streams JSON progress events — this is what the on-device
  pack picker drives. A user **cancel** (SIGTERM, from the picker's Cancel
  button) is handled gracefully: the in-flight download unwinds, the partial
  staging file is dropped, and the install exits cleanly leaving live data
  untouched.
- **`systemd/`** — user service + daily timer, and a udev rule + templated
  service for the USB-stick import path (hangars without WiFi).

```bash
pyefis-data verify                 # check the live manifest signature
pyefis-data status                 # installed vs catalog, per-pack currency
pyefis-data catalog                # every available pack (the picker view)
pyefis-data update                 # pull stale packs, verify, atomic-swap
pyefis-data import /media/usb/makerplane-data   # same verify path, offline
```

On the EFIS this is wrapped by a touch **pack picker** (the Update screen):
pick packs from the catalog, choose the storage drive, watch byte-level
progress, and Cancel a long transfer to return to the picker. Deselecting an
installed pack removes it on the next update.

Proven against the **live production origin**: `update` pulls navdata + terrain
+ water + highways from R2, verifies each sha256 against the signed manifest,
and atomic-swaps them in; an interrupted or bad download never disturbs the
live data. Verify-then-swap, bad-sha-leaves-current-untouched, bad-signature-
refused, offline-cache, idempotent re-run, deselect-removes, graceful-cancel,
and USB import are all unit-tested (108 tests total).

## What's live now (Phases D–F)

- **Distribution (Leg 2) is LIVE and publicly served.** The daily pipeline
  builds + signs + uploads to the Cloudflare R2 bucket `makerplane-data`
  unattended, served at **`https://navdata.aerocommons.org`** (custom domain,
  edge-cached, zero egress). Manifest:
  <https://navdata.aerocommons.org/manifest.json>. Reproduce-from-nothing
  runbook: [docs/cloudflare_setup.md](docs/cloudflare_setup.md).
- **Terrain (Phase D).** Copernicus GLO-30 region packs built on a workstation
  (`packtool make-terrain`), served from R2, pulled + verified on the Pi, read
  by the pyEfis SVS. See [docs/terrain.md](docs/terrain.md).
- **Water & highways.** Single sqlite packs (`kind: water` / `kind: highways`)
  from OpenStreetMap (ODbL attribution), built with `build-pack --upload`,
  served + consumed like navdata. The water build decimates polygons with
  Douglas–Peucker and a min-area declutter so reservoirs render as branches,
  not blobs. See [docs/water.md](docs/water.md).
- **Website + on-device pack picker (Phase E).** A static site at the origin
  lists every pack; on the EFIS the touch picker turns `data.yaml` into a
  point-and-tap selection with a graceful Cancel.
- **In-EFIS DATA flag (Phase F).** A boot Data Status screen + a subtle PFD
  annunciator report currency (green/amber); the EFIS *informs*, never restricts.

## Deferred / interim

- **CIFP packs.** Registered as a source but build deferred — its indexer is
  GPL (pyAvTools) and this repo is MIT. Build via faa-cifp-data's tooling or
  reimplement the index.
- **Tool sharing.** Still the interim `pip install pyEfis from git` shim
  (`PYEFIS_TOOLS_DIR`); the standalone `pyefis-tools` package is a later refactor.

## Data licensing

MIT covers the tooling. Packaged data carries its own terms (FAA = public
domain, Copernicus = redistributable, OSM water = ODbL attribution, NavCanada
charts = user-supplied only). Each pack embeds its `attribution`. See
[LICENSE](LICENSE).
