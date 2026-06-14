# makerplane-data

A Garmin-style **navigation-data currency system** for [pyEfis](https://github.com/makerplane/pyEfis)
and other MakerPlane / AeroCommons avionics. Subscribe once, pull over WiFi
at the hangar (or import from a USB stick), and the EFIS tells you when
something is stale.

This repo covers **reference-data currency only** — terrain, airports,
obstacles, instrument procedures, water, charts. It is *not* the runtime
flight-data bus; that contract lives in `canfix.json` / FIX-Gateway.

> Status: **Phase A** (the data contract) — in progress. See
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
| `packtools/` | 1 | A/B | pack builder: cycles, signing, pack_meta, manifest, regions, CLI |
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
PYTHONPATH=. python -m pytest        # 39 tests, ~0.4s
```

## What's deliberately *not* done in Phase A

- **Leg 1 fetchers** (`fetch_nasr/cifp/dof/water`) and the daily CI workflow → **Phase B**.
- **Tool sharing.** The FAA→sqlite build tools live in pyEfis today
  (`tools/build_airport_db.py`, `build_obstacle_db.py`, …). Interim plan is
  to `pip install` them from git; the standalone `pyefis-tools` package is a
  later, focused refactor. Phase A's `build-pack` takes an *already-built*
  sqlite so the contract can be proven without that dependency.
- **The Pi updater and the website** → Phases C and E.

## Data licensing

MIT covers the tooling. Packaged data carries its own terms (FAA = public
domain, Copernicus = redistributable, OSM water = ODbL attribution, NavCanada
charts = user-supplied only). Each pack embeds its `attribution`. See
[LICENSE](LICENSE).
