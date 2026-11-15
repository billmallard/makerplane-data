# Building and Publishing Packs

This page is for contributors and anyone who wants to know how a pack gets from
its upstream source to the catalog. Every path into the catalog runs the same
checks and signs with the same key, whether it starts on a schedule, by hand in
GitHub Actions, or on a workstation.

```
  upstream data          FAA NASR, CIFP, DOF; Copernicus; OpenStreetMap; OurAirports
       |  fetch
       v
  schema guard           the FAA download still has the format the builder expects
       |  build (pyEfis's build tools, or this repo's)
       v
  pack_meta              the pack's own header is written into it
       |
       v
  publish gate           the pack's test record is built and judged
       |  accepted                               refused
       v                                         v
  upload record + pack                    record kept under reports/rejected/,
  add entries, sign manifest               nothing published
```

## Where builds run

| Workflow | When | Builds |
|---|---|---|
| [`cyclical.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/cyclical.yml) | Daily at 09:20 UTC, or by hand | `airports-conus`, `navaids-conus`, `obstacles-conus`, `procedures-conus` |
| [`roads.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/roads.yml) | By hand | `highways-conus`, from Geofabrik state extracts. `publish: false` builds and reports without publishing |
| [`water.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/water.yml) | By hand | Water packs. Small scopes build on the runner; the full `water-na` is a staged publish (below) |
| On a workstation | By hand | Terrain, with `packtool make-terrain`, which is too large for a hosted runner. Plates, with `packtool make-plates`, which has no workflow yet |
| [`site.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/site.yml) | Nightly at 11:20 UTC, and on a push to `main` | The website: static pages, report pages, format pages, and the nightly status overlay |
| [`impact.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/impact.yml) | On a pull request that touches a builder | Nothing published; posts a data-impact comment (below) |
| [`manifest-admin.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/manifest-admin.yml) | By hand | Retracting an edition, or filling licence fields on old entries |
| [`advisories.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/advisories.yml) | By hand, from `main` only | Signs and publishes the safety advisory feed |

The workflows run on GitHub-hosted runners. The FAA build tools
(`build_airport_db.py`, `build_navaid_db.py`, `build_obstacle_db.py`, and the
roads and water fetchers) live in pyEfis's `tools/` folder. The workflows check
out pyEfis and call them by path; they are not copied into this repository
([`packtools/build/`](https://github.com/billmallard/makerplane-data/tree/dev/packtools/build)).
A pack built by them records the pyEfis commit in `pack_meta.pyefis_commit`.

## The daily build

`python -m packtools.run_cyclical`
([`packtools/run_cyclical.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/run_cyclical.py))
works out the current cycle for each FAA source, and the next one for AIRAC
sources. It builds only editions that are not already in the store and the
catalog, so a normal day does nothing. When the FAA posts the next cycle, the
same run picks it up.

For each edition it fetches, runs the schema guard, builds, embeds `pack_meta`,
and runs the publish gate. Then it prunes the catalog to the two newest editions
of each pack, signs it, and writes it back.

- A source that fails a check ships nothing. Its last published editions keep
  serving, the other sources still publish, and the run ends red.
- A next cycle the FAA has not posted yet is a warning, not a failure.
- The catalog write is compare-and-set: if another publish changed the same
  entries in the meantime, nothing is written and the run says to re-run it.

To see what a day's run would build, with no network, no store and no key:

```bash
python -m packtools.run_cyclical --dry-run
```

When a run publishes something, the workflow also creates a GitHub release
holding each published edition's test record, and a Sigstore build-provenance
attestation for the packs it built
([`docs/provenance.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/provenance.md)).

## The schema guard

FAA files change format from time to time. Because the builders read columns by
name, a renamed or dropped column would not crash a build; it would produce a
wrong or near-empty pack on a green run. The schema guard
([`packtools/schema_guard.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/schema_guard.py))
checks each FAA download before anything is built from it:

1. **Structure.** The download's own structure file matches the committed
   snapshot in
   [`packtools/schemas/`](https://github.com/billmallard/makerplane-data/tree/dev/packtools/schemas).
   Any added, removed, renamed, retyped or widened column fails.
2. **Required headers.** Every column the builder reads is present.
3. **Vocabulary.** Policed columns hold only known values. For example, an
   airway designation must be one of `AT`, `BF`, `G`, `J`, `PA`, `PR`, `R`,
   `RN`, `V`, `SP`.
4. **Output floors.** The built pack has no empty table, and each table has at
   least its minimum row count.

The guard covers the airports and navaids builds. When it fires, that source
publishes nothing until a person reviews the change (usually against the FAA's
notice), refreshes the snapshot, and fixes the builder if needed. The refresh is
the acknowledgement: a format change becomes a reviewed event, not a silent
one. The same four layers appear in every airports and navaids test record as
checks `SG-001` to `SG-004`.

## The publish gate

Every path that adds a pack to the catalog passes through the gate
([`packtools/gate.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/gate.py)):
the daily build, and `publish()` in
[`packtools/publish.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/publish.py),
which `build-pack --upload`, `make-terrain` and `make-plates` all use.

The gate runs on the exact file about to be uploaded. It finds the previous
edition in the signed catalog, builds the pack's `pack-report/1` test record,
and refuses the pack when:

- a blocking check failed, or did not run for a reason it does not allow;
- a check warned and nobody gave a build note for it;
- a check in the kind's list is missing from the record, or raised an error;
- the record does not describe the bytes being signed;
- the `(id, cycle)` is already published. Published editions never change; a
  rebuild needs a new cycle tag;
- the kind is not yet allowed to publish (`airspace`, `plates`, `rivers` and
  `cifp`).

A refused pack's record is kept under `reports/rejected/`, and the pack is not
published. A hand-started publish of several packs is all or nothing: if one is
refused, none of them is published.
An accepted pack's record goes to `reports/<id>/<cycle>/report.json`, and the
catalog entry is bound to it with `report_url` and `report_sha256`.

A **build note** is how a person accepts a warning: a line keyed
`<pack id>@<cycle>:<CHECK>` with the reason. Notes come only from whoever
starts the build (the `notes` input on the workflows), for that edition. The
scheduled run passes none, so a warning on a scheduled build holds that source
until someone looks.

The checks themselves are on [[Pack Reports and Testing]].

## Golden facts

[`packtools/data/golden_facts.yaml`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/data/golden_facts.yaml)
holds values published elsewhere that a pack must agree with: an airport's
elevation, a runway's length, a navaid's frequency. Each fact cites its source
and a tolerance. Check `GLD-001` looks each one up in the pack. Anyone can add a
fact by pull request; CI checks that the file loads and that every fact cites
an `https` source
([`packtools/golden.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/golden.py),
[`docs/golden_facts.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/golden_facts.md)).

## The device acceptance probe

Check `ACC-001` opens the pack with pyEfis's own reader, in a separate process
with a pyEfis checkout on its path, and runs a fixed set of lookups for the
kind: five US metro areas, a few navaids and airways, known water bodies, or a
terrain tile. A pack that the device's reader cannot open fails here, before it
is published
([`packtools/device_probe.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/device_probe.py)).

## Data impact on pull requests

A pull request that changes a builder gets a comment showing what the change
does to the data
([`packtools/impact.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/impact.py),
[`docs/pr_impact.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/pr_impact.md)).
It builds a small fixture region twice, with the base branch's builder and the
pull request's, and compares row counts per table and per group (state, airport,
airspace type), and any check whose status moved. A build failure, a table or
group that drops to zero, or a newly failing check is a regression, and the job
fails. It covers `highways`, `procedures` and `airspace` today.

## Building one pack by hand

`packtool build-pack` turns a built SQLite file or zip into a pack: it embeds
`pack_meta`, and with `--upload` it publishes through the gate.

```bash
packtool build-pack water.sqlite --id water-conus --kind water --cycle 2026q4r1 \
  --regions conus --attribution "OpenStreetMap contributors (ODbL); Natural Earth" \
  --license ODbL-1.0 --upload
```

Terrain is built from a tree of HGT tiles, one pack per region. The coarse
mosaic is packaged in a separate run with `--mosaic`. The water masks inside
terrain come from the published water pack, which the build downloads and
checks against the signed catalog first:

```bash
packtool make-terrain HGT_TREE --edition 2024edr4 --upload
packtool make-terrain HGT_TREE --edition 2024edr4 --mosaic --upload
```

### Staged publish for very large builds

The full North America water pack is a large download and a many-hour build,
too much for a hosted runner. It is built on a workstation, and the unsigned
SQLite file is uploaded to a `staging/` area that the catalog never lists.
`water.yml` is then started with `staged_key` and `staged_sha256`. The runner
skips the fetch and build, refuses the file if its SHA-256 differs, and runs the
same gate, signing and upload as any other build. The test record names the
workstation build as `runner: local`.

## Signing

The catalog is signed with an ed25519 key in minisign format
([`packtools/signing.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/signing.py)).
The build reads the secret key from the `MINISIGN_SECRET_KEY` environment
variable, which in GitHub Actions is a repository secret, or from a file given
with `--sec`. The secret is never in the repository. Only
[`keys/minisign.pub`](https://github.com/billmallard/makerplane-data/blob/dev/keys/minisign.pub)
is published.

A throwaway keypair for local testing:

```bash
packtool genkey --out keys        # commit the .pub only, never the secret
```

Every write of the catalog re-signs it and stores `manifest.json` only if it
has not changed since it was read, then writes the signature.

## Taking a pack down

`packtool remove-pack --id <id> --cycle <cycle>`, or `manifest-admin.yml` with
`action: retract`, removes one edition from the catalog and re-signs it
([`packtools/publish.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/publish.py)).
The pack file itself stays in the store. Devices stop seeing the edition on
their next update. The edition's status file on the website then reads
`retracted`, or `superseded` if a newer edition exists.

## Further reading

- [`docs/ci_build_pipeline.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/ci_build_pipeline.md):
  the pipeline design.
- [`docs/terrain.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/terrain.md),
  [`docs/water.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/water.md),
  [`docs/roads.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/roads.md):
  per-kind build notes.
- [`docs/release_process.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/release_process.md):
  the release steps.
