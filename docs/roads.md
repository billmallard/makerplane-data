# Road / highway data

Major-highway overlays (OSM motorway/trunk) for the SVS — pyEfis issue #35.
Same shape as [water](water.md): OSM from the same Geofabrik state bundles,
built to a single `highways.sqlite` (`highway_lines` + R-tree) the SVS reads
via its `highway_db_path` option. So it's one sqlite pack (`kind: highways`),
reusing the navdata install path. Small — CO+TX is ~14 MB; CONUS is a few
hundred MB. **ODbL** (OSM) — attribution required in pack_meta.

Each row also carries `flags` (bit 0 tunnel, bit 1 bridge) and `ref`
(route shield text, e.g. `I 70`) — AER-623/RD3, pyEfis PR #165. The SVS
drops tunnel segments (the road ends at each portal instead of crossing the
ridge above) and skips subdividing bridges (straight and level between
abutments, not draped into the valley they span). Both columns are read
**when present**: a pack built before `2026q3r1` has neither, and the
reader falls back to `flags=0, ref=None` rather than raising — nothing new
is required by the manifest, and old packs keep working right up until
they're rebuilt.

## Build + upload

Roads and water come from the *same* Geofabrik per-state bundles
(`<state>-latest-free.shp.zip`), so a build host downloads each bundle once.
`packtool build-roads` (`packtools/make_roads.py`) automates the CONUS
fetch + build step:

```bash
# 1. fetch every CONUS state's roads layer + build highways.sqlite.
#    PYEFIS_TOOLS_DIR must point at a pyEfis tools/ checkout (build_highway_db.py
#    lives there, RD3a/PR#165 — writes flags/ref when the Geofabrik layer has
#    tunnel/bridge/ref fields, which it does).
PYEFIS_TOOLS_DIR=/path/to/pyEfis/tools \
packtool build-roads --states conus --dest highways.sqlite
#   --cache-dir defaults to work/geofabrik-roads (gitignored scratch, resumable —
#   re-running skips states already downloaded/extracted). --states also takes
#   a comma list of Geofabrik slugs for a partial/test build, e.g. --states
#   colorado,texas. California has no combined state bundle (Geofabrik 302s
#   'california-latest-free.shp.zip' to its homepage instead of serving it,
#   or 404ing) — the CONUS list uses its california/norcal + california/socal
#   subregion extracts instead. A state that comes back empty or non-zip is a
#   hard failure, never a silent skip (makerplane-data#17: the June 2026 build
#   used a bare 'california' entry and shipped a pack with zero California
#   roads without anyone noticing until someone flew there).

# 2. pack + upload (signed), alongside navdata/terrain/water
R2_ENDPOINT=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... \
packtool build-pack highways.sqlite \
    --id highways-conus --kind highways --cycle 2026q3r1 \
    --attribution "OpenStreetMap contributors (ODbL)" \
    --license "ODbL-1.0" --license-url "https://opendatacommons.org/licenses/odbl/1-0/" \
    --regions conus --url-base https://navdata.aerocommons.org/packs \
    --upload --bucket makerplane-data --sec keys/minisign.sec
```

The expensive part is the Geofabrik *download* (each state bundle is
50 MB–1.5 GB, all layers; `build-roads` keeps only the roads layer and
deletes the rest); the resulting highways DB is small, and the build itself
is minutes. Don't run it concurrently with another upload (shared manifest).

`.github/workflows/roads.yml` does steps 1-2 in CI (`workflow_dispatch`,
inputs `states` default `conus` and `cycle` default `2026q3r1`), the same
pattern as `water.yml`/`cyclical.yml` — no local secrets or workstation
needed, just the `MINISIGN_SECRET_KEY`/`R2_*` Actions secrets already used
for the daily cyclical build. Full CONUS is still a long-running dispatch
(one Geofabrik state bundle at a time); a partial `--states` list (e.g.
`colorado,texas`) is the cheap way to test the workflow itself.

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
