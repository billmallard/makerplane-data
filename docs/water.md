# Water data

Coastlines, lakes, and inland water polygons for the SVS — a dataset GLO-30
terrain does **not** contain. The SVS reads a single `water.sqlite`
(`water_polygons` table + R-tree) via its `water_db_path` option, so water is
one **sqlite pack** (`kind: water`), reusing the same install path as navdata.

## Sources + licensing

- **Inland water** — OSM via Geofabrik per-state extracts
  (`gis_osm_water_a_free_1`): lakes, reservoirs, wide rivers.
- **Coastline / ocean** — OSM `water-polygons-split-4326` (and optionally
  Natural Earth `ne_10m_ocean` / `ne_10m_lakes`).
- **License:** OSM is **ODbL** — attribution is required and must be carried
  in the pack (`attribution` in pack_meta), and set explicitly and
  machine-readably via `license`/`license_url` (`ODbL-1.0`). Natural Earth is
  public domain.

## Before you publish — notify QA (AER-854)

QA's Auspex water oracle (the judge comparing a rendered SVS frame against
the shipped water map) carries a size-limit census scoped to the coastline
geometry it measured — not to the `water` kind in general. A rebuild that
moves where coastline runs invalidates a verdict made against the old cycle,
even when nothing about pyEfis changed. **Before running `--upload` on a
water or terrain pack**, file an issue to QA (agent `c5173f0b-60e7-46af-b89a-b11a0dfec6be`)
naming the pack id, the old and new cycle, and what changed, whenever any of
these move:

- **A new upstream source snapshot** — a different Geofabrik state-bundle
  date, a different `water-polygons-split-4326` extract, or a Natural Earth
  version bump (whatever `fetch_geofabrik_water.py` pulled this run).
- **A change to ring-emission, clipping, or simplification** in the water
  build tools (pyEfis `build_water_db.py` / `fetch_geofabrik_water.py`) —
  e.g. the pending "ocean fills island holes" fix (`billmallard/pyEfis#44`),
  which by definition changes how multipolygon rings become filled polygons.
- **A change to the pack's region/bbox clipping scheme** — a different
  `--regions` cut, or moving off the `water-na` monolith to per-region packs
  ([regionalize_bulk_packs.md](regionalize_bulk_packs.md)).
- **A terrain release that ships baked water masks** — MP10b's `.wmask`
  channel (see [terrain.md](terrain.md#water-mask--baked-in-water-no-runtime-rasterization-mp10b))
  is a second, independently-rasterized representation of the same
  coastline that the SVS reader prefers over the runtime `water.sqlite`
  polygon path whenever present. `water.sqlite` itself doesn't move, but
  what a device renders does.

**Not a trigger** — skip the notification: any other pack kind's rebuild
(navdata, navaids, obstacles, rivers, airports/cifp) never touches
`water.sqlite`. Specifically, **highways/roads** shares the same Geofabrik
per-state download but `build_highway_db.py` reads a separate roads layer —
a highways-only rebuild (e.g. the CONUS `2026q3r1` cycle, AER-618/AER-623
RD3) never moves coastline geometry. Re-publishing the same cycle unchanged
(manifest housekeeping, re-signing, `prune_old_cycles`) isn't a rebuild
either.

## Build + upload

The water `sqlite` is built by the pyEfis tools, then packed + uploaded with
`build-pack --upload` (which signs the manifest via the shared publish path):

```bash
# 1. build water.sqlite (pyEfis tools)
python pyEfis/tools/fetch_geofabrik_water.py --states texas   # or conus
#    -> water.sqlite (ocean coastline from the shipped water-polygons extract)

# 2. pack + upload (signed) — adds water alongside navdata/terrain in R2
R2_ENDPOINT=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
packtool build-pack water.sqlite \
    --id water-conus --kind water --cycle 2026q2 \
    --attribution "OpenStreetMap contributors (ODbL); Natural Earth" \
    --license "ODbL-1.0" --license-url "https://opendatacommons.org/licenses/odbl/1-0/" \
    --regions conus --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
```

`.github/workflows/water.yml` does this in CI (workflow_dispatch). Water is
small enough to build on a runner for modest scopes; **full CONUS** is a large
Geofabrik download — use a workstation or split by state group. Don't run it
at the same time as the navdata or terrain upload (all rewrite the manifest).

## Consume on a prototype

```yaml
# ~/.makerplane/pyefis/data.yaml
regions: [conus]        # water is region-gated (opt-in), like terrain
```
`pyefis-data update` pulls `water-conus`, verifies sha256, installs it at
`<root>/water/current/water.sqlite` (atomic symlink, like navdata). Point the
SVS at it:
```yaml
water_db_path: /data/makerplane-data/water/current/water.sqlite
```
