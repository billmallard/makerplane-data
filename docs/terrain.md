# Terrain data (Phase D)

How GLO-30 terrain gets from a tile tree into a pyEfis SVS, via the same
signed-pack pipeline as navdata. Terrain is the **bulk/static** data shape:
huge, edition-tagged, never "expires" — built once per edition on a
**workstation** (never on the avionics device) and served from R2.

Proven end-to-end on real hardware (2026-06): a Pi pulled the us-west pack
(~10 GB) from `navdata.aerocommons.org` in ~5 min, verified it against the
production-signed manifest, unzipped it into the tile tree, and the SVS's
own `TileCache` read it back — KSBA = 14 ft (real ~13), valid 3601² grids.

## Pack format

One zip pack per region, containing the HGT tree exactly as the SVS reads it
(`<NSdir>/<name>.hgt`, e.g. `N34/N34W119.hgt`) plus `pack_meta.json`. GLO-30
tiles are 3601×3601 int16 (≈26 MB each). A tile is assigned to a region by
its SW corner (`regions.yaml` bboxes); a tile in two regions is written into
both packs (identical content — the Pi unions them into one tile tree).

Manifest entry: `kind: terrain`, `regions: [<region>]`, `tiles_bbox`,
`effective/expires: null` (non-cyclical), plus the usual sha256/bytes/url.

## Build + upload (workstation)

Requirements on the build host: the HGT tile tree, this package
(`pip install`), `boto3`, the R2 credentials (env), and the signing secret.

```bash
# one region (repeat per region, or loop):
R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
packtool make-terrain /path/to/glo30hgt \
    --edition 2024ed --only us-west \
    --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
```

Each run builds the region pack, uploads it to R2, and **upserts** the
terrain entry into the existing manifest (preserving navdata), re-signing.
Run region-by-region for resilience (each commits independently).

Notes:
- `--no-compress` skips DEFLATE. With a fast uplink this is far quicker to
  build (no CPU compression) at the cost of larger files — and R2 storage is
  cheap with **zero egress**, so it's usually the right call. (NA total
  ≈ 91 GB uncompressed.)
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
