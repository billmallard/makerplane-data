# Pack Reports and Testing

Every published pack has a test record: the checks that were run on it, what
each one measured, and what each one cannot catch. The records are public, the
code that writes them is public, and anyone can re-run them on the pack they
downloaded. This page explains what a record says and how to use it. The pages
that show the records are on [[The navdata Website]].

A test record is evidence, not a certification. Passing every check does not
mean the data is correct, complete or current. Every record says so, and the
`cannot_catch` field on each check says exactly where its blind spot is.

## The record: `pack-report/1`

Each edition's record is at `reports/<id>/<cycle>/report.json`, and the catalog
entry carries its URL and SHA-256 (`report_url`, `report_sha256`), so the record
is covered by the catalog signature
([`packtools/report.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/report.py),
[`docs/pack_report.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/pack_report.md)).

| Field | What it holds |
|---|---|
| `record` | `build` (written by the publish gate), `retro` (written later from the published bytes), or `rejected` |
| `pack` | id, kind, cycle, SHA-256, size and `schema_version` |
| `origin` | `federal`, `community` or `user-supplied` |
| `built` | When, from which commit, the builder commits, the Actions run, and the runner: `github-hosted`, `self-hosted` or `local` |
| `sources` | The upstream files the build read, each with its edition and SHA-256 |
| `previous` | The edition it was compared against |
| `checks` | One entry per check, below |
| `tables` | Row counts per table, this edition and the previous one |
| `summary` | Counts of pass, warn, fail and not run |
| `notice` | The standing notice |

Each check carries `id`, `title`, `status` (`pass`, `warn`, `fail`,
`not-run`), whether it is `blocking`, what it `measured` and `expected`, and
`cannot_catch`. A check that did not run says why, and a warning accepted by a
person carries their build note.

### Build records and retro records

The publish gate writes a `build` record before the pack is signed; see
[[Building and Publishing Packs]]. Editions published before the gate existed
got a `retro` record afterwards, written from the published bytes
([`packtools/retro.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/retro.py)).
A retro record cannot recover what was not kept, such as the upstream files, so
checks that need them show as not run. Its report page says so.

The check list grows. A report page also judges each edition against today's
list and says when today's gate would refuse it as a new build. That does not
take a published edition out of the catalog. It tells you which checks the
edition never had.

## The checks

The list depends on the kind
([`packtools/report.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/report.py)).
`packtool report --list-checks` prints it. Blocking checks stop a publish when
they fail.

| Group | Applies to | Checks |
|---|---|---|
| Every pack | all kinds | `PACK-001` header readable and names a known kind; `PACK-002` attribution and licence recorded; `PACK-003` origin recorded; `PACK-004` upstream sources recorded; `ACC-001` pyEfis's own reader opens it and answers fixed lookups |
| Databases | SQLite kinds | `SQL-001` SQLite integrity check; `ROW-001` no empty table; `ROW-002` row counts within 10% of the previous edition, or a note; `ROW-003` no table of the previous edition missing |
| FAA input format | `navdata`, `navaids` | `SG-001` to `SG-004`, the schema guard's four layers |
| Coordinates | `navdata`, `airports`, `navaids`, `obstacles`, `procedures` | `GEO-001` coordinates valid and not at 0,0; `UNQ-001` identifiers that must be unique are unique; `GEO-002` points outside the US envelope, against the previous edition (FAA kinds) |
| Obstacles | `obstacles` | `OBS-001` at least 328,500 rows |
| Navaids | `navaids` | `UNQ-002` identifiers the FAA reuses, against the previous edition |
| Procedures | `procedures` | `PROC-TBL-001` tables present; airway, MSA and grid MORA consistency (`PROC-AWY-*`, `PROC-MSA-*`, `PROC-MORA-*`); `PROC-FIX-001` legs with a fix that could not be placed; `PROC-DIFF-001` comparison against an independent database, not yet run |
| Terrain | `terrain` | `TER-001` to `TER-008`: tile files valid, inside the region, nothing lost from the previous edition, water masks present, elevation and voids sampled, every source tile present |
| OpenStreetMap | `water`, `highways` | `OSM-001`, `OSM-002`, `OSM-006` counts and length or area per region and per state; `OSM-004` bounding boxes; `IDX-001` spatial indexes cover every row; `OSM-003` highway columns match the schema version |
| Cross-source | several | `GLD-001` golden facts; `XS-001` airport elevations and `XS-002` obstacle bases against terrain (shown for review, never blocking) |

Kinds not yet allowed to publish (`airspace`, `plates`, `rivers`, `cifp`) can
be given a record, but it never clears the gate.

## Re-run a report yourself

Everything a record says about a pack can be reproduced from the pack's bytes
with public code. The same steps are on the website at
[`/reports/check.html`](https://navdata.aerocommons.org/reports/check.html).

```bash
pip install "makerplane-data @ git+https://github.com/billmallard/makerplane-data"
curl -O https://navdata.aerocommons.org/packs/procedures-conus-2612.pack
packtool report --check procedures-conus-2612.pack
```

With no other arguments, `--check` fetches the signed catalog, checks its
signature, finds your file's entry by SHA-256, fetches that edition's record and
checks it against `report_sha256`. Then it re-runs every check and compares
status and measured values, row counts and pack identity. Each check prints as
reproduced, `MISMATCH`, not reproducible here (with the reason), or not run in
the record. It exits 1 on any mismatch. Offline, compare against a record you
saved with `--against report.json`.

Some checks need more than the pack. Comparing with the previous edition needs
that edition (`--previous OLD.pack`). The schema guard needs the FAA files the
build read. The device probe needs a pyEfis checkout (`--pyefis-src`).

### Which code built a pack

Packs built on GitHub-hosted runners carry a Sigstore build-provenance
attestation that names the workflow, commit and run that produced the file:

```bash
gh attestation verify highways-conus-2026q3r1.pack \
  --repo billmallard/makerplane-data \
  --signer-workflow billmallard/makerplane-data/.github/workflows/roads.yml \
  --deny-self-hosted-runners
```

The workflow is `cyclical.yml` for airports, navaids, obstacles and procedures,
`roads.yml` for roads and `water.yml` for water built on the runner. Terrain
and other packs built on a workstation have no attestation. For those, the
signed catalog and the test record are what you can check. An attestation says
which code made the bytes, not that the data is right.

## Known problems and the status file

A record never changes once it is signed. What happens to an edition later goes
in a separate, unsigned file beside it, `reports/<id>/<cycle>/status.json`
(schema `pack-status/1`), rewritten nightly
([`packtools/community.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/community.py),
[`docs/community_loop.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/community_loop.md)):

| Field | Meaning |
|---|---|
| `state` | `current`, `pending` (not yet effective), `superseded`, `expired` or `retracted` |
| `state_reason` | Why |
| `listed` | Whether the signed catalog still lists the edition |
| `known_issues` | GitHub issues labelled for this pack: number, title, link, state, and the cycle they were reported against |
| `issues_url` | Where to see or open them |
| `signed` | Always `false`: this file is not covered by the signature |

### Reporting a data problem

If a pack disagrees with the official source, open a
[Data problem](https://github.com/billmallard/makerplane-data/issues/new?template=data_problem.yml)
issue. The form asks for the pack id, the edition, the location, what the pack
shows, and what the official source shows. When the issue is opened, a workflow
adds the `pack:<id>` label for each pack id named in the form. Labels are
created only from the signed catalog, every night, so a typo cannot create one.
The issue then appears in that edition's `known_issues`.

If you have run a pack on real hardware, the
[Tested on hardware](https://github.com/billmallard/makerplane-data/issues/new?template=tested_on_hardware.yml)
form records the outcome, the hardware, the software and the output of
`pyefis-data status`.
