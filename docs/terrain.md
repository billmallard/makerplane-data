# Terrain data (Phase D)

How GLO-30 terrain gets from a tile tree into a pyEfis SVS, via the same
signed-pack pipeline as navdata. Terrain is the **bulk/static** data shape:
huge, edition-tagged, never "expires" — built once per edition on a
**workstation** (never on the avionics device) and served from R2.

Proven end-to-end on real hardware (2026-06): a Pi pulled the us-west pack
(~10 GB) from `navdata.aerocommons.org` in ~5 min, verified it against the
production-signed manifest, unzipped it into the tile tree, and the SVS's
own `TileCache` read it back — KSBA = 14 ft (real ~13), valid 3601² grids.

## Mip pyramid — fast wide-range zoom (primer)

The moving map / SVS renders terrain by sampling HGT tiles. Up close that is a
handful of tiles; zoomed out to regional/national range it is *hundreds* — and
the naive renderer loaded and unpacked each full 26 MB tile just to pull a
sparse sample from it, throwing ~97 % of the work away. Measured on a Pi 5:
**300 NM ≈ 38 s to draw one frame** — unusable for a national view.

A **mip pyramid** fixes this. For every native tile we pre-compute a ladder of
progressively shrunk copies — half-size, quarter-size, … down to a ~57² thumbnail
(~6 KB) — stored beside the native tile under `.mip/<L>/`. A wide view reads the
tiny coarse tiles instead of the giant native ones, so a national window drops
from ~100 GB of tile reads to a few MB. Proven on the Pi: **300 NM 38 s → 0.38 s
(~99×)**. The coarse tiles are ordinary big-endian `>i2` HGT squares (the loader
infers the side from file size), so there is **no new tile format** and no
manifest change — they simply ride along in each region pack (§ Pack format) and
the renderer's `get_mip()` discovers them, falling back to the native tile if a
pack has none (old packs keep working, just slow at range).

Three-step build (details in § Build + upload; renderer/design spec in pyEfis
`docs/terrain_mip_pyramid.md`):

1. `pyEfis/tools/build_terrain_mips.py <tiletree>` — walk the HGT tree and write
   the shrunk copies into the parallel `.mip/` tree. Local, idempotent, leaves
   the originals untouched. Dense ladder = levels 1–6 (decimation 2ᵏ), ≈+8.6 MB
   per native tile (**~+33 %**, NA ≈ +31 GB). **Run this first** so the `.mip/`
   tree exists to be zipped in.
2. `pyEfis/tools/build_terrain_mosaic.py <tiletree> --levels 4 5 6` — stitch the
   coarse `.mip` levels into one whole-extent **mosaic** per level under
   `.mip/mosaic/` (fast — seconds). The renderer memory-maps it for
   *constant-time* wide-range zoom (see § Mosaic). Idempotent; persists beside
   the pyramid.
3. `packtool make-terrain … --upload` — zip each region (native **plus** the
   `.mip/` tiles) and, once, `make-terrain --mosaic` to publish the national
   mosaic pack. Every user's device then pulls both through the normal
   `pyefis-data update` — no per-device build.

## Mosaic — instant national zoom (primer)

The pyramid made wide-range terrain *render-bound* rather than *tile-bound*, but
a national view still opened hundreds-to-thousands of tiny `.mip` files (cold
I/O). The **mosaic** removes even that: one memory-mappable big-endian `>i2`
file per coarse level (`.mip/mosaic/L{4,5,6}.hgt` + a `.json` sidecar with the
extent/pitch), sampled in a single vectorized bilinear. Render then goes flat
**~0.19 s at any range** — constant-time nationwide zoom.

A mosaic is a **single whole-extent stitch**, so — unlike the per-tile pyramid —
it can't ride inside the per-region packs (a device unions arbitrary regions;
per-region mosaic pieces don't stitch back into one file on-device). It ships
instead as **one national `terrain-mosaic` pack**, tagged with the synthetic
region `mosaic`. The updater **auto-tracks it whenever any terrain region is
tracked**, and it unzips straight into `terrain/tiles/.mip/mosaic/`, which the
renderer's `TileCache.get_mosaic()` reads with no code change (absent ⇒ the
per-tile path still works, just slower at range).

## Pack format

One zip pack per region, containing the HGT tree exactly as the SVS reads it
(`<NSdir>/<name>.hgt`, e.g. `N34/N34W119.hgt`) plus `pack_meta.json`. GLO-30
tiles are 3601×3601 int16 (≈26 MB each). A tile is assigned to a region by
its SW corner (`regions.yaml` bboxes); a tile in two regions is written into
both packs (identical content — the Pi unions them into one tile tree).

Each pack also carries that tile's **mip pyramid** when one has been built:
coarse `.mip/<L>/<NSdir>/<name>.hgt` levels (pyEfis `docs/terrain_mip_pyramid.md`)
that the moving-map/SVS renderer's `get_mip()` reads for fast wide-range zoom.
It's a pure path convention — no manifest/pack_meta change; a tree without a
pyramid simply produces native-only packs.

Manifest entry: `kind: terrain`, `regions: [<region>]`, `tiles_bbox`,
`effective/expires: null` (non-cyclical), plus the usual sha256/bytes/url.

## Build + upload (workstation)

Requirements on the build host: the HGT tile tree, this package
(`pip install`), `boto3`, the R2 credentials (env), and the signing secret.

```bash
# 1. Build the mip pyramid INTO the HGT tree (once per edition). Parallel by
#    default (--jobs = all CPU cores); idempotent (skips built tiles). All-NA
#    is ~5 h single-core but ~tens of minutes on a many-core box, so this is
#    the step to run on a fat cloud instance. The coarse .mip/<L>/ tiles then
#    ride along in each region pack automatically.
python /path/to/pyEfis/tools/build_terrain_mips.py /path/to/glo30hgt   # -j N to cap workers

# 2. Stitch the coarse mosaic from the mip tree (fast; once per edition).
python /path/to/pyEfis/tools/build_terrain_mosaic.py /path/to/glo30hgt --levels 4 5 6

# 3. Build + upload region packs (native + pyramid), then the national mosaic
#    pack. One region shown; repeat per region, or loop:
R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
packtool make-terrain /path/to/glo30hgt \
    --edition 2024ed --only us-west \
    --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
# ... then once, the whole-extent mosaic pack (all regions share it):
packtool make-terrain /path/to/glo30hgt --edition 2024ed --mosaic \
    --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
```

In practice all of the above runs unattended in the **cloud container**
(`packtools/cloud/`, `entrypoint.sh` — mips → mosaic → region packs → mosaic
pack → verify); the manual commands are the same steps for a workstation.

Each run builds the region pack, uploads it to R2, and **upserts** the
terrain entry into the existing manifest (preserving navdata), re-signing.
Run region-by-region for resilience (each commits independently).

Notes:
- Packs now ship **DEFLATE-compressed** by default (~50 % smaller on R2 and the
  wire, at the cost of build CPU + a one-time Pi decompress on pull). Pass
  `--no-compress` to store uncompressed (faster build, larger files) if a
  specific run wants it. See § Compression & data footprint for the numbers.
- Run on a workstation that holds the GLO-30 tree — **not** the EFIS device.
- Don't run the daily navdata pipeline and a terrain upload at the same
  time; both rewrite the manifest.

North-America regions (see `packtools/regions.yaml`): `us-west`,
`us-central`, `us-east`, `us-south`, `alaska`, `canada-west`, `canada-east`,
`mexico-central-america`. (`conus` is the navdata grouping; skip it for
terrain — the `us-*` regions cover it.)

## Consume on a prototype / Pi

1. **Opt into the region(s)** in `~/.makerplane/pyefis/data.yaml`:
   ```yaml
   base_url: https://navdata.aerocommons.org
   root: /data/makerplane-data      # on the M.2, not the SD card
   regions: [us-west]               # bulk packs are opt-in by region
   ```
   (Core navdata — airports/obstacles — is tracked automatically; terrain is
   opt-in by region because it's large.)

2. **Pull it:** `pyefis-data update` downloads the region pack(s) from R2,
   verifies sha256 against the signed manifest, and unzips into
   `<root>/terrain/tiles/` (regions union into one tree;
   `terrain/tiles/.regions.json` records what's installed). USB import works
   the same way (`pyefis-data import <dir>`).

3. **Point the SVS at it** — in the `virtual_vfr`/SVS instrument options:
   ```yaml
   tile_path: /data/makerplane-data/terrain/tiles
   ```
   The SVS reads HGT tiles from this directory (1201 SRTM3 or 3601 GLO-30,
   resolution inferred from file size). Once the regions you fly are pulled,
   this has the same coverage as a hand-managed HGT tree — now kept current
   by the data manager.

## Storage

GLO-30 NA ≈ 91 GB (uncompressed) per edition in R2 (≈ $1.4/mo storage, $0
egress). On a Pi, terrain belongs on the M.2 (`/data`), not the SD card.
SD-card / small deployments pull only their flying-area regions.

## Compression & data footprint (design note)

Terrain packs now ship **DEFLATE-compressed** (`ZIP_DEFLATED`, the `make-terrain`
default; the cloud pipeline dropped its old `--no-compress`). Footprint matters
for consumers on slow or metered links, and for parity with commercial systems:
Garmin's terrain database is far smaller than ours — coarser tiles plus
compression, a deliberate trade of detail for size that still yields acceptable
results. Halving the download (~120 GB → ~60 GB all-NA) is worth the build CPU +
one-time Pi decompress; R2 storage stays cheap either way (~$1.40/mo, zero
egress). Pass `--no-compress` for a one-off uncompressed build if ever needed.

Measured DEFLATE-6 on real GLO-30 tiles from this tree:

| terrain | native | compressed | saved |
|---|---:|---:|---:|
| coastal / mixed (N34W080) | 25.9 MB | 7.4 MB | 71 % |
| Appalachian (N34W083) | 25.9 MB | 9.2 MB | 65 % |
| Rockies, rugged (N39W106) | 25.9 MB | 14.3 MB | 45 % |
| mip pyramid (per tile) | 8.65 MB | 2.78 MB | 68 % |

Blended NA ≈ **~50 % smaller** (~120 GB → ~60 GB per edition; ruggedness drives
the spread — flat coast squashes hard, mountains resist). **Decision (2026-07):
option (a) shipped** — packs are compressed by default. Two further footprint
levers remain open if a public release wants them: (b) compress only
large/rugged regions; (c) a coarser native base — decimate GLO-30 to a smaller
level-0 (closer to Garmin's approach: smaller *and* faster to render, at a
bounded detail cost) while keeping the pyramid on top.
