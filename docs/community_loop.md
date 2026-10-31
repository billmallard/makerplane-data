# The GitHub community loop (TV8)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** 2026-10-31 (AER-4345, testing-visibility item TV8). Source of the
design: `makerplane/briefs/testing_visibility_plan.md` sections 4.5 and 6
(maos-workspace). Where this document and the plan disagree, the plan wins
and this document is the bug.

A pack's test record (`report.json`, [pack_report.md](pack_report.md)) is
fixed at publish and bound to the signed catalog. Anything learned later
has to live somewhere else without rewriting it. This is that somewhere:

| Piece | Where | Written by |
|---|---|---|
| Issue forms: **Data problem**, **Tested on my hardware** | `.github/ISSUE_TEMPLATE/` | the person filing |
| `pack:<id>` labels | makerplane-data issues | created nightly from the signed catalog; added from the form's *Pack id* field or by a maintainer |
| `status.json` overlay, one per edition | `reports/<id>/<cycle>/status.json` | `packtool status-overlay`, nightly in `site.yml` |
| One GitHub Release per publish event | makerplane-data Releases, tag `data/<date>/<run>.<attempt>` | `packtool release-assets` + `gh release create`, in each Actions publish workflow |

## Issue forms and labels

Both forms carry a **Pack id** field. "Data problem" also asks for the
edition, a location, what the pack shows and what an official source shows,
so a report names one place anyone can look at.

- The forms apply `data-problem` or `tested-on-hardware`. A form silently
  skips a label that does not exist, so the nightly label step creates both.
- `issue-pack-label.yml` runs when a form is filed or edited. It reads the
  *Pack id* field and adds `pack:<id>` for each id **whose label already
  exists**. It never creates or removes a label. The issue body is untrusted:
  it reaches the script only as an environment variable and is parsed as
  data, with the stdlib.
- `packtool status-overlay --ensure-labels` (nightly) creates `pack:<id>` for
  every id in the signed live catalog, plus the two form labels, where
  missing. It never edits an existing label.
- A maintainer may add or remove `pack:<id>` by hand. That is the only way
  to attach an issue filed without the form.

The *Pack id* and *Edition (cycle)* field labels are read by
`packtools/community.py` (`PACK_FIELD`, `CYCLE_FIELD`). A test pins the
forms to those constants.

## `status.json` (`pack-status/1`)

Written beside every record under `reports/<id>/<cycle>/report.json`
(never beside a `reports/rejected/` one). **Not covered by the catalog
signature.** It is rewritten nightly, and `as_of` says when.

```json
{
  "schema": "pack-status/1",
  "pack":   {"id": "procedures-conus", "cycle": "2611", "sha256": "..."},
  "record": {"url": "https://navdata.aerocommons.org/reports/procedures-conus/2611/report.json",
             "sha256": "..."},
  "as_of":  "2026-11-01",
  "signed": false,
  "listed": true,
  "state":  "current",
  "state_reason": "in the live catalog; the edition the updater installs today",
  "known_issues": [{"number": 17, "title": "...", "url": "https://github.com/...",
                    "state": "open", "opened": "2026-10-30", "closed": null,
                    "reported_cycle": "2610"}],
  "issues_url": "https://github.com/billmallard/makerplane-data/issues?q=label%3A%22pack%3Aprocedures-conus%22",
  "auspex_addenda": [{"name": "auspex-2026-11-02.json", "date": "2026-11-02",
                      "url": "https://navdata.aerocommons.org/reports/procedures-conus/2611/auspex-2026-11-02.json",
                      "sha256": "...", "bytes": 1234}],
  "note": "Not covered by the catalog signature. ..."
}
```

### `state`

Decided against the **signed** live catalog, verified with
`keys/minisign.pub` before anything is read from it. A catalog that does not
verify writes no overlay.

| State | Listed? | Meaning |
|---|---|---|
| `current` | yes | The edition `Manifest.select` (the updater's own choice) installs today. |
| `pending` | yes | Its window has not started (the "next" cycle the nightly publishes early). |
| `superseded` | yes | Its window covers today, but the updater installs another edition. |
| `expired` | yes | Its window has ended. |
| `superseded` | no | The catalog lists a newer edition of the same id: a higher cycle (both canonical; the edition `prune_old_cycles` dropped), or one built later. |
| `retracted` | no | The catalog lists no newer edition of the id: removed from the catalog, or never listed. |

`pending` is not in the plan's list of four. It is there because the nightly
publishes current and next together, and calling the next cycle `current`
before its effective date would be false.

An unlisted record carries no dates, so an unlisted edition is never called
`expired`. Its `state_reason` says what was compared. A hyphen-suffixed test
cycle (`2026q3r1-smoketest`) sorts above its canonical cycle, so it is ranked
by build time only. Editions listed with no record were published before
records existed and count as built before any edition that has one.

### `known_issues`

Every issue (open and closed, never a pull request) labelled `pack:<id>`, on
**every** edition of that id. The label names a pack, not an edition, so the
overlay does not guess which editions an issue affects. `reported_cycle` is
the edition the form named, when it named one. Titles go through the same
hygiene scrub as the records (local paths, addresses, internal hosts).

If any page of the GitHub issues API fails, the step fails and nothing is
written. A partial list would claim fewer known issues than there are.

### `auspex_addenda`

Files named `auspex-<YYYY-MM-DD>*.json` beside the record: an Auspex run
after publish, as a dated, immutable addendum (plan 4.6). QA writes them.
The overlay lists each one by name, date, URL, size and sha256 and **does
not open it**. What may appear inside is the `auspex` schema in
[pack_report.md](pack_report.md), which is pending Bill's sign-off at the
TV14 schema review.

### On the report page

Every `report.html` has a **Known since this edition was built** section. It
is filled in the browser from the `status.json` beside the page. This keeps
the page bytes unchanged when an issue changes, and keeps issue titles (the
community's words, not ours) out of the page lint. Without the overlay, the
section still links the label query and a pre-filled Data problem form for
that edition.

## One Release per publish event

`cyclical.yml`, `roads.yml` and `water.yml` end with a **GitHub Release for
this publish** step:

1. `packtool release-assets --records work/reports --out work/release`
   reads the publish gate's records for this job. It keeps an edition only
   if the gate accepted it **and** the signed live catalog now lists it with
   a `report_sha256` equal to the record's bytes. An edition that did not
   reach the catalog is named in the log and left out. If no edition is
   left, nothing is written and no release is cut, so a nightly that
   published nothing makes no release.
2. For each edition it writes `<id>-<cycle>.report.json` (the bound bytes,
   unchanged) and `<id>-<cycle>.report.html` (rendered by the same code as
   the site, with a `<base>` pointing at the live page so its links work from
   a download), plus the notes, tag and title.
3. `gh release create data/<date>/<run>.<attempt> ... --latest=false`.

The tag prefix is `data/`, never `v`: package versions are `vX.Y.Z` tags
([release_process.md](release_process.md)), and a data tag must not look
like one. `--latest=false` leaves the "latest release" badge for software.

The job needs `contents: write` to create the release. That is the only
permission added to the publish workflows.

### Not covered

- **Terrain** publishes from the QNAP entrypoint, and **plates** from a
  workstation, not from Actions. Neither has a GitHub token, so they cut no
  release. Their records and overlays work as for every other pack.
- **`manifest-admin.yml`** (retract, license backfill) adds no edition, so it
  cuts no release. A retraction shows up as `retracted` in the overlay.
