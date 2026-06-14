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
  in the pack (`attribution` in pack_meta). Natural Earth is public domain.

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
