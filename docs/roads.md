# Road / highway data

Major-highway overlays (OSM motorway/trunk) for the SVS — pyEfis issue #35.
Same shape as [water](water.md): OSM from the same Geofabrik state bundles,
built to a single `highways.sqlite` (`highway_lines` + R-tree) the SVS reads
via its `highway_db_path` option. So it's one sqlite pack (`kind: highways`),
reusing the navdata install path. Small — CO+TX is ~14 MB; CONUS is a few
hundred MB. **ODbL** (OSM) — attribution required in pack_meta.

## Build + upload

Roads and water come from the *same* Geofabrik per-state bundles
(`<state>-latest-free.shp.zip`), so a build host downloads each bundle once.

```bash
# 1. extract the roads layer from each state bundle, then build highways.sqlite
python pyEfis/tools/build_highway_db.py --dest highways.sqlite \
    /path/to/colorado/gis_osm_roads_free_1.shp \
    /path/to/texas/gis_osm_roads_free_1.shp  ...    # one .shp per state

# 2. pack + upload (signed), alongside navdata/terrain/water
R2_ENDPOINT=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
packtool build-pack highways.sqlite \
    --id highways-conus --kind highways --cycle 2026q2 \
    --attribution "OpenStreetMap contributors (ODbL)" \
    --regions conus --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
```

The expensive part is the Geofabrik *download* (each state bundle is
50 MB–1.5 GB, all layers); the resulting highways DB is small. For full NA,
download all state bundles (a workstation/long job), or build by state group.
Don't run it concurrently with another upload (shared manifest).

## Consume on a prototype

```yaml
# ~/.makerplane/pyefis/data.yaml
regions: [conus]        # highways is region-gated (opt-in), like water
```
`pyefis-data update` pulls `highways-conus`, verifies, installs at
`<root>/highways/current/highways.sqlite`. Point the SVS at it:
```yaml
highway_db_path: /data/makerplane-data/highways/current/highways.sqlite
```
