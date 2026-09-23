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

**`schema_version` tracks the `highway_lines` columns** (AER-1715):
`build-pack --kind highways` no longer just stamps the package-wide default
— it opens the built sqlite and looks at what's actually there
(`packmeta.detect_highways_schema_version`), so `1` means no `flags`/`ref`
columns and `2` means they're present. Raises rather than guessing if it
sees a column set it doesn't recognise (add the new shape to
`HIGHWAYS_TABLE_SCHEMAS` first). This is forward-only: packs already
published before this change keep whatever `schema_version` they were
built with (`1`, regardless of their real columns) rather than being
retroactively relabeled — `HighwayDB` still probes `PRAGMA table_info`
itself rather than trusting the field, so nothing reads it as authoritative
yet, and relabeling a live pack means re-signing and republishing it for a
field nothing currently gates on. A future reader that wants to gate on
`schema_version` instead of probing needs to know only packs built from
this point on carry an accurate value.

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
#   roads without anyone noticing until someone flew there). A transient
#   Geofabrik 502/503/timeout on a state is routine over an ~80-minute CONUS
#   fetch, not exceptional (makerplane-data#60): fetch_state retries a single
#   state a few times with short backoff, and any state still down after that
#   gets one more pass once the rest of CONUS has been tried -- long enough
#   for a clustered mirror hiccup to clear. A state down through both passes
#   is still the same hard failure above. A state whose upstream *generation*
#   is wedged rather than transiently erroring -- Geofabrik's daily build 200s
#   but the archive is a README-only stub with no layers, verified for
#   Delaware on 2026-09-07 across multiple consecutive days -- can't be
#   outlasted by the retry pass either, since a retry just re-downloads the
#   same stub. `STATE_SNAPSHOT_OVERRIDES` in make_roads.py pins that state to
#   its last known-good dated snapshot instead of the "latest" alias until
#   Geofabrik's generation for it is confirmed healed.

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
`colorado,texas`) is the cheap way to test the workflow itself -- **but pass
`publish: false`** when doing so. `--upload` always targets the production
`highways-conus` id no matter what `states` was. That bit a states-limited
test build on 2026-09-12 (makerplane-data#60): a Colorado-only
`2026q3r1-smoketest` build was uploaded with `publish` defaulting to true
(the input didn't exist yet) and, because a non-cyclical pack's currency was
picked by comparing cycle *strings* (`Manifest.select`) and `"...smoketest"`
sorts after `"2026q3r1"`, it stayed selected as the live CONUS pack even
after the real `2026q3r1` build published. `Manifest.select` and
`prune_old_cycles` now rank a hyphen-suffixed cycle below every canonical
one regardless of how it sorts lexically (AER-1109), so a repeat of this
exact incident no longer needs a manual retract to fix the live selection --
but a states-limited pack still gets published under the production id and
still confuses anything reading the manifest by hand, so the `publish:
false` discipline above still stands. Undo a bad publish like that one with
`packtool remove-pack --id highways-conus --cycle 2026q3r1-smoketest`, which
retracts just that manifest entry (not the pack object) and re-signs.

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
