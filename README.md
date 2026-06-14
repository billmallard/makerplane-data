# makerplane-data

A Garmin-style **navigation-data currency system** for [pyEfis](https://github.com/makerplane/pyEfis)
and other MakerPlane / AeroCommons avionics. Subscribe once, pull over WiFi
at the hangar (or import from a USB stick), and the EFIS tells you when
something is stale.

This repo covers **reference-data currency only** — terrain, airports,
obstacles, instrument procedures, water, charts. It is *not* the runtime
flight-data bus; that contract lives in `canfix.json` / FIX-Gateway.

> Status: **Phase A** (the data contract) ✅ and **Phase B** (the build
> pipeline) ✅ — code complete and proven end-to-end against live FAA data.
> The only thing not yet live is the R2 upload target (awaiting the
> Cloudflare account/bucket). See
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
                            data.makerplane.org; static Pages site
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
| `packtools/` | 1 | A/B | pack builder: cycles, signing, pack_meta, manifest, regions, sources, fetch, build, upload, orchestrator, CLI |
| `.github/workflows/` | 1/2 | B | `ci.yml` (tests + dry-run), `cyclical.yml` (daily build+sign+upload), water/terrain dispatch stubs |
| `pyefis_data/` | 3 | C | on-Pi updater CLI (status / update / import) |
| `site/` | 2 | E | static Cloudflare Pages dashboard + region picker |
| `regions.yaml` | — | A | region bboxes for terrain grouping & Pi region-of-interest |
| `keys/minisign.pub` | — | A | signing public key (committed; secret never is) |

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
PYTHONPATH=. python -m pytest        # 60 tests, ~2s
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

## What's deliberately *not* done yet

- **R2 is LIVE and publicly served.** The daily pipeline builds + signs +
  uploads to the Cloudflare R2 bucket `makerplane-data` unattended, served at
  **`https://navdata.aerocommons.org`** (custom domain, edge-cached, zero
  egress). Manifest: <https://navdata.aerocommons.org/manifest.json>. Full
  reproduce-from-nothing runbook:
  [docs/cloudflare_setup.md](docs/cloudflare_setup.md).
- **CIFP packs.** Registered as a source but build deferred — its indexer is
  GPL (pyAvTools) and this repo is MIT. Build via faa-cifp-data's tooling or
  reimplement the index. Airports + obstacles ship now.
- **Tool sharing.** Still the interim `pip install pyEfis from git` shim
  (`PYEFIS_TOOLS_DIR`); the standalone `pyefis-tools` package is a later
  refactor.
- **Water/terrain pipelines** (`water.yml`/`terrain.yml` are dispatch stubs)
  → Phases B.2 / D. **The Pi updater and website** → Phases C and E.

## Data licensing

MIT covers the tooling. Packaged data carries its own terms (FAA = public
domain, Copernicus = redistributable, OSM water = ODbL attribution, NavCanada
charts = user-supplied only). Each pack embeds its `attribution`. See
[LICENSE](LICENSE).
