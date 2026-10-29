# Pack test record: `pack-report/1`

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** schema, 2026-10-28 (AER-4099, testing-visibility item TV1).
Nothing writes a report yet; `packtool report` is TV2 and the publish wiring
is TV3. The `auspex` section (below) is **pending Bill's sign-off at the TV14
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
reports/rejected/...                  records for builds the gate refused (D4; path fixed in TV3)
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
separate, unsigned `status.json` overlay (TV8), never into the record.

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

`not-run` is a first-class status. A check that exists but did not run is
listed as `not-run` with its `note`. Leaving it out would imply it passed.

A check can "pass". A pack is never "passed".

### `tables`

`{"<table>": {"rows": <int>, "prev": <int or null>}}`. `prev` is `null` when
`previous` is `null` or the table is new.

### `summary`

`{"pass": n, "warn": n, "fail": n, "not_run": n}` -- the tally of
`checks[].status`, nothing else. A renderer reads this for the catalog line;
it must equal the tally of `checks` or the record is malformed.

### `notice`

Exactly the manifest `notice` text, `packtools.manifest.NOTICE`:

> Experimental / Amateur-Built use only. Provided as-is, with no warranty. Not
> certified. Not for primary navigation. Use at your own risk.

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
  "notice": "Experimental / Amateur-Built use only. Provided as-is, with no warranty. Not certified. Not for primary navigation. Use at your own risk."
}
```
