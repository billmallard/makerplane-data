# Pack test record: `pack-report/1`

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** schema, 2026-10-28 (AER-4099, testing-visibility item TV1).
`packtool report` writes records and `packtool report --check` re-runs them
(AER-4100, TV2; see [Writing and checking a record](#writing-and-checking-a-record)).
Nothing publishes one yet: the publish wiring is TV3. The `auspex` section (below) is **pending Bill's sign-off at the TV14
schema review** and must not be emitted before then.

Every pack edition gets one test record: the checks that ran on it, what each
one measured, and what each one **cannot** catch. It is evidence, never a
warranty. It does not say a pack is right; it says what was looked at and what
was found.

Source of the design: `makerplane/briefs/testing_visibility_plan.md`
sections 4.1, 4.2, 4.6 and 4.7 (maos-workspace). Where this document and the
plan disagree, the plan wins and this document is the bug.

## Where it lives

```
reports/<id>/<cycle>/report.json      the record (this schema)
reports/<id>/<cycle>/report.html      rendered from the JSON, never hand-written
reports/rejected/<id>/<cycle>/<built.at>-<sha12>/report.json
                                      a build the gate refused (D4); one key per attempt
```

Same R2 bucket and hostname as the packs (`navdata.aerocommons.org`).

## Binding to the signed manifest

The manifest entry for the pack carries `report_url` and `report_sha256`
(see [Manifest fields](#manifest-fields)). `report_sha256` is the sha256 of
the exact bytes of `report.json` as uploaded, so the minisign signature that
a device already checks over `manifest.json` also covers the report. A record
cannot be swapped after publish without breaking that hash.

Serialize the same way as the manifest (`Manifest.to_bytes`): UTF-8,
`sort_keys`, 2-space indent, trailing newline. Hash those bytes; upload those
bytes.

A record is **immutable**. Things learned after publish (a defect found
later, superseded, expired, retracted, an after-publish Auspex run) go in the
separate, unsigned `status.json` overlay (TV8, [community_loop.md](community_loop.md)),
never into the record.

## Top-level fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema` | string | yes | Always `"pack-report/1"`. |
| `record` | string enum | yes | `build`, `retro` or `rejected`. See below. |
| `pack` | object | yes | Which bytes this record is about. |
| `origin` | string enum | yes | `federal`, `community` or `user-supplied`; same value as the manifest `origin`. |
| `built` | object | yes | Who built it, from what code, where. |
| `sources` | array | yes | Upstream inputs. May be empty only for `retro` when the inputs are no longer known; say so in a `not-run` check. |
| `previous` | object or `null` | yes | The edition this one is compared against; `null` for a first edition. |
| `checks` | array | yes | One object per check. Never empty. |
| `tables` | object | yes | Row counts per table, with the previous edition's counts. |
| `summary` | object | yes | Tally of `checks[].status`. |
| `notice` | string | yes | The short standing text, verbatim (below). |
| `metrics` | object | no | Per-check breakdowns keyed by check id (below). |
| `gated` | boolean | no | `false` when the pack's kind has no check catalogue QA has accepted as a gate (`report.UNGATED`: rivers, cifp, airspace, plates). |
| `ungated` | string | when `gated` is `false` | Why the kind is ungated. |
| `auspex` | object | no | At most one. Fixed schema, **pending sign-off** (below). |

Consumers ignore unknown top-level fields, so `pack-report/1` may gain
optional fields. Removing or changing the meaning of a field is
`pack-report/2`. The `auspex` section is the exception: it is closed (below).

### `record`

- **`build`** -- written by the build job itself, from the same bytes, before
  the manifest is signed. The normal case.
- **`retro`** -- written after the fact for an edition published before
  `pack-report/1` existed (TV4). It describes the published bytes; `built`
  fields that can no longer be recovered are `null`, and any check that
  cannot be reproduced on the old bytes is `not-run` with its reason.
- **`rejected`** -- a build the gate refused (D4). It is never listed in the
  manifest. It is published because "the gate caught it" is evidence the gate
  works.

### `pack`

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Manifest id, e.g. `procedures-conus`. |
| `kind` | string | One of `packmeta.KINDS`. |
| `cycle` | string | Manifest cycle. |
| `sha256` | string | 64 lowercase hex; equals the manifest entry's `sha256`. |
| `bytes` | integer | Pack size; equals the manifest entry's `bytes`. |
| `schema_version` | string | The pack's own content schema version, from `pack_meta`. |

### `built`

| Field | Type | Meaning |
|---|---|---|
| `at` | string | ISO-8601 UTC time the build started. |
| `repo` | string | `billmallard/makerplane-data`. |
| `commit` | string | Full commit sha; equals the manifest `build_commit`. |
| `builder_refs` | object | Other repos the build ran code from, name to full sha, e.g. `{"pyEfis": "..."}`. |
| `run_url` | string or `null` | The CI run, if any. |
| `runner` | string | `github-hosted`, `self-hosted`, or `local`. |

### `sources[]`

| Field | Type | Meaning |
|---|---|---|
| `name` | string | e.g. `FAA CIFP`, `Copernicus GLO-30`, `OpenStreetMap (Geofabrik)`. |
| `edition` | string | The upstream edition or cycle. |
| `url` | string | Where it was fetched. |
| `retrieved` | string | ISO-8601 UTC. |
| `sha256` | string | sha256 of what was fetched. |

### `previous`

`{"cycle": "...", "sha256": "..."}` of the edition the deltas in `tables` and
any delta checks were computed against, or `null`.

### `checks[]`

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | Stable check id, e.g. `PROC-AWY-002`. Never reused for a different check. |
| `title` | string | yes | What it checks, in one line. |
| `status` | string enum | yes | `pass`, `warn`, `fail` or `not-run`. |
| `blocking` | boolean | yes | Whether a `fail` stops the publish. |
| `measured` | number, string or `null` | yes | What the check found. `null` only for `not-run`. |
| `expected` | number or string | yes | What the check accepts, in words a reader can apply. |
| `cannot_catch` | string | yes | A plain statement of what this check would miss. Never empty. |
| `since` | string | no | The issue or change that introduced the check, e.g. `AER-1979`. |
| `note` | string | for `warn` and `not-run` | For `not-run`, why it did not run. For `warn`, the build note explaining it; a `warn` without one blocks the publish (plan 4.3). |
| `not_run_reason` | string | no | For `not-run`, a short reason key, e.g. `first-edition`, `no-previous`, `mosaic-pack`. Only a key the catalogue entry declares can exempt a blocking check (below). |
| `raised` | boolean | no | `true` when the check crashed. It is recorded as `fail`, and it blocks the publish whatever `blocking` says: it measured nothing. |
| `provisional` | string | no | The check's threshold has no measured baseline yet; this says so and why. |

`not-run` is a first-class status. A check that exists but did not run is
listed as `not-run` with its `note`. Leaving it out would imply it passed.

**What blocks a publish** (`report.publish_blockers`, QA AER-4102):

The gate takes the check set and each check's `blocking` flag from the
catalogue for the record's kind in `packtools/report.py`, never from the
record it judges (R1). Editing `blocking`, dropping a check or changing
`record` in a record cannot clear it.

- a record that is not `build`: only a build record can clear the gate (R1);
- a catalogue check missing from the record, or a kind that is not known (R1);
- a check that raised, whatever its `blocking` flag (C2);
- a blocking check that failed;
- a `warn` with no build note;
- in a `build` record, a blocking check that is `not-run`, unless its
  `not_run_reason` is one the check's catalogue entry declares (C1). The
  declaration lives in `packtools/report.py` (`Check.exempt`), never in the
  record or at run time. Declared today: `first-edition` on the delta checks
  `OSM-001`, `ROW-003` and `TER-006`, given only when the caller passes
  `--first-edition`; and `mosaic-pack` on `TER-002`, only for the pack whose
  `pack_meta.id` is `terrain-mosaic` (R2). A regional terrain pack with no
  native tiles fails `TER-002`. `ACC-001` declares none,
  so a missing or unloadable pyEfis reader always blocks;
- in a `build` record, a kind that is ungated.

A `retro` or `rejected` record never clears the gate.

A check can "pass". A pack is never "passed".

### `tables`

`{"<table>": {"rows": <int>, "prev": <int or null>}}`. `prev` is `null` when
`previous` is `null` or the table is new.

### `summary`

`{"pass": n, "warn": n, "fail": n, "not_run": n}` -- the tally of
`checks[].status`, nothing else. A renderer reads this for the catalog line;
it must equal the tally of `checks` or the record is malformed.

### `metrics`

Optional. The detail behind a check's one-line `measured`, keyed by check
id, so that the next edition's delta checks can read it from this record
without the old pack: `OSM-001` per-region feature counts, `GEO-002` points
outside the coverage envelope per column, `UNQ-002` reused-ident counts,
`PROC-FIX-001` unresolved legs by fix type, `ACC-001` the device query
results, `TER-002` tile counts. `--check` compares `checks` and `tables`,
not `metrics`.

### `notice`

Exactly the manifest `notice` text, `packtools.manifest.NOTICE`:

> Experimental / Amateur-Built use only. Not certified. This data is provided
> as-is with no warranty and any use of the data is at your own risk.

## The `auspex` section -- PENDING SIGN-OFF

> **Pending Bill's sign-off at the TV14 schema review**, including the
> borderline call on whether the `coverage` figures cross. Until then no
> report carries this section. The shape below is plan section 4.6
> transcribed, not a proposal.

Auspex is a proprietary AI test engine, named in pack reports as a black box
(D2; `oracle_gate.md` firewall rule 3, standing call). Its evidence enters a
report only through this section, and **this section's schema is the wall:
a field it does not define does not cross.** Unlike the rest of the record,
it is closed -- no additional fields at any level. A section with an unknown
field is malformed and must not be published.

QA writes this section. AVIONICS-DATA's code only carries it through and
checks it against this shape.

```json
"auspex": {
  "engine": "Auspex -- proprietary AI test engine; method not published",
  "engine_build": "<opaque id>",
  "check": "pack_check",
  "run_at": "2026-10-28T00:00:00Z",
  "when": "pre-publish",
  "verdict": "pass",
  "coverage": {"checked": 0, "covered": 0, "skipped": 0, "unreadable": 0},
  "findings": [{
    "locator": {"table": "water", "row_id": 0, "hole": 0, "lat": 0.0, "lon": 0.0},
    "statement": "...", "expected": "...", "actual": "...", "issue": "#NNN"}],
  "what_a_pass_means": "The pack does not contradict itself. It never means the pack is right about the world."
}
```

| Field | Type | Meaning |
|---|---|---|
| `engine` | string | Exactly the string above. The only description of the engine a report ever carries. |
| `engine_build` | string | An opaque id for which build ran. Nothing about it. |
| `check` | string | The named check, e.g. `pack_check`. |
| `run_at` | string | ISO-8601 UTC. |
| `when` | string enum | `pre-publish` (section is in the signed record) or `after-publish` (section is a dated addendum, `auspex-<date>.json`, listed with its sha256 in `status.json`; the record is never edited). |
| `verdict` | string enum | `pass`, `fail` or `inconclusive`. |
| `coverage` | object | `checked`, `covered`, `skipped`, `unreadable`: this run, on this pack. **Crossing is the borderline call pending sign-off.** |
| `findings[]` | array | Testcase shape: what is wrong, never how it was found. `locator` (where to look), `statement`, `expected`, `actual`, `issue`. Each is checkable without Auspex. |
| `what_a_pass_means` | string | The capability statement above. |

Never in this section, or anywhere else in a report: the method, rubrics,
labels or corpus; the engine's own performance (precision, recall,
false-pass rates); totals across runs.

`packtool report --check` prints this section's results as "not reproducible
here (Auspex)", with each finding's locator.

## Writing and checking a record

```bash
packtool report PACK [--previous PREV.pack|PREV.report.json | --first-edition] \
                [--inputs DIR] [--note CHECK_ID=TEXT ...] [--sources sources.json] [-o out.json]
packtool report --check PACK [--against report.json|URL] [--previous ...]
packtool report --list-checks [--kind KIND]
```

`packtool report` runs the kind's catalogue and writes the record in the
canonical bytes above. It exits 1 when the record would stop a publish (the
rules under [`checks[]`](#checks)). The record is written either way.

`--first-edition` declares that the pack's id has no earlier edition. It is
the only way a blocking delta check may be `not-run` in a build record: a
missing `--previous` without it blocks, so a forgotten argument cannot pass
as a first edition.

`--check` re-runs the checks on a pack you downloaded and compares each
check's `status` and `measured`, plus `tables` and the pack's identity,
against the published record. Without `--against` it finds the record
through the signed live manifest (signature, then the pack's sha256, then
`report_sha256`). A check that needs something you did not supply (the
previous edition for a delta, the upstream CSVs for `schema_guard` layers
1-3, a pyEfis checkout for device acceptance) prints as "not reproducible
here" with the reason, not as a mismatch. It exits 1 on any mismatch.

Every request either command makes sends its own user agent: the CDN
answers 403 to Python's default `urllib` one (`packtools.fetch.USER_AGENT`).

### Device acceptance (ACC-001)

pyEfis's own reader opens the pack and answers a fixed set of queries for
the kind: airports near five US cities, ident lookups, airway legs, road
and water features near fixed points, one terrain tile or mosaic level
(`packtools/device_probe.py`). The probe runs in its own process against
a separate pyEfis checkout (`--pyefis-src`, `PYEFIS_SRC_DIR`, or the
`src/` beside `PYEFIS_TOOLS_DIR`), the same way the builders run
`pyEfis/tools`; no pyEfis code is in this repo. If no checkout is available,
or its reader cannot be imported (for terrain the SVS reader needs PyQt6),
the check is `not-run` with the reason, and in a build record that blocks
the publish. The terrain query requires the grid side the tile's size
implies, not just a non-empty grid.

### The v1 check catalogue

`packtool report --list-checks` prints every kind's checks with their
expectation and `cannot_catch` line; `packtools/report.py` is the source.
Thresholds are named constants at the top of that file.

**QA reviews the definitions and thresholds (AER-4102)** before any of them
gates a publish (plan section 4.4). The author proposes; QA decides what to
accept. QA's first verdict (catalogue v1 at `1913743`) was "changes
needed" for every kind; AER-4137 applies those changes and awaits QA's
re-verdict. `ROW-002`, `GEO-002` and `OSM-001`'s thresholds are accepted
only as provisional (`provisional` in the record).

Which checks apply:

| Applies to | Checks |
|---|---|
| Every kind | `PACK-001..004` identity, attribution and license, origin, sources; `ACC-001` device acceptance |
| SQLite packs | `SQL-001` quick_check; `ROW-001` no empty table (water declares `waterway_lines` empty by design); `ROW-002` row counts against the previous edition; `ROW-003` no table of the previous edition gone, unless `schema_version` changed |
| navdata, navaids | `SG-001..004`, the four `schema_guard` layers, persisted |
| obstacles | `OBS-001` row floor (no `schema_guard` contract exists for FAA DOF) |
| navdata, airports, navaids, obstacles, procedures | `GEO-001` valid coordinates; `UNQ-001` unique identifiers |
| FAA kinds | `GEO-002` points outside the US coverage envelope against the previous edition |
| navaids | `UNQ-002` identifiers the FAA reuses, against the previous edition |
| procedures | `PROC-FIX-001` unresolved-fix legs; `PROC-AWY-001`, `PROC-AWY-002` airway idents and the AER-1979 area merge; `PROC-MSA-001` every source PS record stored (needs `--inputs`), `PROC-MSA-002` sectors tile 360 degrees, `PROC-MSA-003` altitude and radius bounds, `PROC-MSA-004` unresolved MSA centres (AER-4446); `PROC-MORA-001`..`003` Grid MORA cell count (30 per band, band floor), duplicate cells and value range, all blocking, and `PROC-MORA-004` the `UNK` cell count (AER-4447); `PROC-GLD-001` and `PROC-DIFF-001` (`not-run`) |
| terrain | `TER-001` member layout and sizes; `TER-002` tiles inside the region; `TER-006` no native tile of the previous edition missing; `TER-003` water masks (AER-2287); `TER-004` sampled elevation range; `TER-005` (`not-run`) |
| water, highways | `IDX-001` every R-tree indexes every row of its table, plus `rtreecheck` |
| water, highways, rivers | `OSM-001` counts per region (#17); `OSM-002` per state (`not-run`, TV7); `OSM-004` bounding boxes; `OSM-005` coverage map (`not-run`, TV5); highways also `OSM-003` schema version (AER-1715) |
| rivers, cifp, airspace, plates | **Ungated** (`report.UNGATED`): the record is written with `gated: false`, and a build record of these kinds never clears the gate |

## The publish gate (TV3)

Every path that adds a pack to the live manifest runs the gate
(`packtools/gate.py`) on the exact file it is about to upload, before the
manifest is signed: the cyclical run (`run_cyclical`) and everything that
calls `packtools.publish.publish` (`build-pack --upload` for roads and
water, `make-terrain --upload` from the QNAP entrypoint, `make-plates
--upload`). In order:

1. **Verify the live manifest.** Its signature is checked against the
   signing key before anything is decided from it. A missing or bad
   signature stops the publish.
2. **Refuse an (id, cycle) already listed.** A published record is
   immutable, so a rebuild publishes under a new cycle tag.
   `make-terrain --upload` skips a region already listed at the edition, the
   way the cyclical run skips a present cycle.
3. **Check the entry against the file.** The entry's `sha256` and `bytes`
   must match it.
4. **Find the previous edition in the signed live manifest, and nowhere
   else.**
   - No edition of the id listed (and none accepted earlier in the same
     run): this is a first edition (`--first-edition`).
   - Otherwise the newest edition ranked below this one is the previous one.
     The gate reads its bound record (hash-checked against `report_sha256`).
     For an edition published before records existed, it reports on that
     edition's pack instead (hash-checked against `sha256`).
   - Listed but unreadable: there is no previous record, and not a first
     edition either, so every blocking delta check blocks.
   - Nothing in config, and no flag on a scheduled run, can declare a first
     edition.
5. **Write the record in this job, from these bytes.** It runs with the
   device probe on and with the build notes the person who started the run
   wrote. A note key is `CHECK` or `<pack id>:CHECK`; the cyclical run takes
   only `<pack id>@<cycle>:CHECK`, so a note cannot carry to the next
   cycle. A note naming no check in the kind's catalogue is refused.
6. **Judge it.** The record's `pack` (id, kind, cycle, sha256, bytes) must
   equal the file being signed, hashed again after the checks ran. The
   record must pass `validate_report`. Then `publish_blockers` decides,
   taking the check set and the `blocking` flags from the catalogue, not
   from the record.

An **accepted** pack's record is uploaded to
`reports/<id>/<cycle>/report.json`, then the pack, and the manifest entry
gets `report_url`, `report_sha256`, `origin` and `build_commit` before the
manifest is signed. A **refused** pack's record is uploaded with `record:
rejected` to `reports/rejected/<id>/<cycle>/<built.at>-<sha12>/report.json`.
Nothing else is written. When one publish carries several packs, nothing is
published unless every pack is accepted.

Every write of the manifest, retract and license backfill included, goes
through `publish.sign_and_put`. It calls `manifest.validate(obj,
previous=live)`, which refuses two things:

- a new entry without a record;
- a changed `report_url` or `report_sha256` on an entry the live manifest
  had already bound to a record.

**Build notes come only from a person.** The scheduled cyclical run has
none, so a check that warns refuses its pack every night until someone who
has looked at the warn dispatches `cyclical.yml` by hand with `notes`
(`{"procedures-conus@2612:ROW-002": "why"}`). `roads.yml` and `water.yml`
take a `notes` input. The terrain entrypoint reads
`$PACK_OUT/build-notes-$EDITION.txt`, a file named for the edition.

**Job summaries** are rendered from the records: `summary.record_markdown`,
in `run_cyclical` and in `packtool job-summary --records <out>/reports`. A
run that stopped before any pack reached the gate says it has no record.

## Report pages (TV5)

`packtool report-site` (`packtools/reportsite.py`) renders the public pages
from the records in the bucket. It never writes a record; it owns only:

```
reports/index.json                   per-edition tally the catalog line reads
reports/index.html                   every pack with a record
reports/check.html                   check it yourself (the --check walk-through)
reports/<id>/index.html              history: editions, row-count trends, check status over time
reports/<id>/<cycle>/report.html     one edition, with the standing text below
```

`site.yml` runs it after `deploy-site` and daily at 11:20 UTC, so a record a
publish path uploads gets its page with no deploy. A catalog row whose edition
has no record says "No test report yet for this edition."; it is not hidden.

Before rendering, every string in a record is scrubbed of local paths, IP
addresses, internal hostnames and links to non-public hosts. Links are drawn
only to an allowlist of public hosts, so a board id such as `AER-1979`
appears as bare text. The build fails, and writes nothing, if any page
carries a banned word outside the standing text and the notice, or a host
detail the scrub missed.

Preview locally from the committed fixture records:

```bash
packtool report-site --from tests/fixtures/report_site --out /tmp/preview
python -m http.server -d /tmp/preview      # then open /index.html
```

## Manifest fields

`PackEntry` (`packtools/manifest.py`) gains five fields. All are additive and
default to `""`, with no `manifest_version` bump -- the same pattern as
`license`/`license_url` (AER-546/547).

| Field | Meaning |
|---|---|
| `report_url` | Absolute URL of this edition's `report.json`. |
| `report_sha256` | sha256 of those exact bytes, 64 lowercase hex. |
| `origin` | `federal`, `community` or `user-supplied` (`manifest.ORIGINS`; `docs/do200b_applicability.md` section 5). A pedigree label, not a quality score. |
| `notice` | `manifest.NOTICE`, verbatim. `PackEntry.from_pack` fills it by default. |
| `build_commit` | Full makerplane-data commit sha that built the pack. |

**The publish gate.** `validate(obj, previous=prior_manifest)` refuses any
entry whose `(id, cycle)` is not in `prior_manifest` unless it has a
`report_url`, a 64-hex `report_sha256` and a non-empty `notice` (and, if
`origin` is set, one of the three values). Entries already published stay
valid without any of these; their records arrive as `retro` reports (TV4).
The publish paths start passing `previous` when TV3 gives them a report to
publish -- passing it before then would refuse every nightly build.

**Devices.** The device parse path calls `validate` without `previous` and
never looks at these fields, and `PackEntry.from_dict` drops any field it
does not know. So a device released before these fields existed reads a
manifest that has them exactly as it reads one that does not
(`tests/test_pi_updater.py::test_device_ignores_unknown_manifest_fields`).

## Wording

Every string a report, a renderer or a manifest field carries follows plan
section 4.7. It is evidence. These words are never used about a pack or a
check result: verified, certified, approved, safe, compliant, airworthy.

The page block every rendered report carries ("What this report is and is
not") is the long form in plan section 4.7, verbatim.

## Example

The plan's sketch for `procedures-conus` 2611. Row counts are the real values
measured 2026-10-28; sha fields and the check list are placeholders.

```json
{
  "schema": "pack-report/1",
  "record": "build",
  "pack": {"id": "procedures-conus", "kind": "procedures", "cycle": "2611",
           "sha256": "<64 hex>", "bytes": 21798912, "schema_version": "1"},
  "origin": "federal",
  "built": {"at": "2026-10-28T03:10:00Z", "repo": "billmallard/makerplane-data",
            "commit": "<40 hex>", "builder_refs": {"pyEfis": "<40 hex>"},
            "run_url": "https://github.com/billmallard/makerplane-data/actions/runs/<id>",
            "runner": "github-hosted"},
  "sources": [{"name": "FAA CIFP", "edition": "2611", "url": "<url>",
               "retrieved": "2026-10-28T03:00:00Z", "sha256": "<64 hex>"}],
  "previous": {"cycle": "2610", "sha256": "<64 hex>"},
  "checks": [
    {"id": "PROC-AWY-002", "title": "Airway idents unique per area code",
     "status": "pass", "blocking": true, "measured": 0, "expected": "0 collisions",
     "cannot_catch": "An airway whose legs are all present but in the wrong order.",
     "since": "AER-1979"},
    {"id": "PROC-DIFF-001", "title": "Independent differential against a commercial database",
     "status": "not-run", "blocking": false, "measured": null,
     "expected": "no unexplained differences",
     "cannot_catch": "Errors shared by both databases.",
     "note": "Not yet run (AER-1702)."}
  ],
  "tables": {"airways": {"rows": 1244, "prev": 1504},
             "airway_legs": {"rows": 16960, "prev": 19391}},
  "summary": {"pass": 1, "warn": 0, "fail": 0, "not_run": 1},
  "notice": "Experimental / Amateur-Built use only. Not certified. This data is provided as-is with no warranty and any use of the data is at your own risk."
}
```
