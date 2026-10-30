# Changelog

Notable changes to the `makerplane-data` packages (`packtools`, `pyefis_data`)
and the `configurator`. Semver on the Python package; process in
[docs/release_process.md](docs/release_process.md).

## 0.4.0 — 2026-10-30

Promotes the `dev` work since 0.3.0. Its core is testing visibility (TV0 to
TV5): a public, reproducible test record for every data pack.

### Testing visibility (packtools)
- **Publish gate (TV3).** Every path that adds a pack to the live manifest
  now builds the pack's `pack-report/1` record in the same job, from the
  exact bytes, and judges it **before signing**. That covers the cyclical
  nightly, `build-pack --upload` (roads, water), `make-terrain --upload` (QNAP
  entrypoint) and `make-plates --upload`.
  - A refused pack publishes only a `record: rejected` copy under
    `reports/rejected/`.
  - An accepted pack's record is at `reports/<id>/<cycle>/report.json`, and
    the signed manifest binds it with `report_url` and `report_sha256`.
  - Records are immutable: a published (id, cycle) cannot be republished,
    and signing refuses a re-pointed record.
  - The scheduled nightly supplies no build notes, so a check that warns
    refuses the pack. A person re-runs `cyclical.yml` with notes keyed to one
    edition.
  - Job summaries are rendered from the records.
  - Terrain records carry origin `federal`.
  - See [docs/pack_report.md](docs/pack_report.md).
- **`packtool report` and `report --check` (TV2).** Check catalogue v1 with
  a `cannot_catch` line on every check, and a device acceptance probe
  through pyEfis's own readers. QA reviewed the definitions (AER-4102), and
  the gate takes the check set and the blocking flags from the catalogue.
  rivers, cifp, airspace and plates are declared ungated, so the gate
  refuses them until QA accepts a catalogue for each.
- **Manifest fields (TV1).** Additive `report_url`, `report_sha256`,
  `origin`, `notice` and `build_commit`; a new entry must carry a record.
  Devices ignore the fields. The notice carries Bill's standing disclaimer.
- **Honesty fixes (TV0).**
  - Builder refusals fail the nightly instead of warning.
  - Blank licenses can be backfilled (`manifest-admin`).
  - Site source links point at the billmallard repos.
- **Report pages (TV5).** `packtool report-site` renders the public report
  pages, the per-pack history and the catalog summary line. `site.yml`
  deploys the site and renders the pages, daily and on deploy.

### Packs and pipeline
- **New pack kind `plates`** (d-TPP plates, georeferenced from CIFP).
  - Registered in `packmeta.KINDS`, so the nightly can load a manifest that
    lists it.
  - The device updater does not install plates yet, and the gate refuses
    the kind until it has an accepted catalogue.
- Airways are keyed by customer/area code, and procedures-conus is filtered
  to the USA (AER-1979).
- An unrecognized pack kind no longer sinks the whole catalog on a device
  (AER-1935).
- Alaska region `lon_min` widened to -177 (AER-1990).
- Terrain:
  - `make-terrain --require-wmask`;
  - the QNAP build fails without water masks;
  - `fetch-pack`, a signed-manifest-verified download;
  - `EDITION` is required.

### Configurator and device config
- Altimeter-setting units in aircraft settings, served with the device
  config and merged into `preferences.yaml.custom` by `config-pull`
  (AER-3917).
- tab_section tab position and tab-bar colours, SVS twin fidelity, and panel
  size accepting any panel.

### Docs
- DO-200B applicability brief, Seattle Avionics compatibility briefs, route
  sync design (mp-route/2), and a QA notice before coastline-moving
  publishes.

### Operational notes for this release
- The terrain runner (`billmallard/makerplane-builds`) needs PyQt6 and
  libGL before its next publish (makerplane-builds#1).
- No D1 migrations are in this release.

## 0.3.0 — 2026-08-01

First versioned release cut under the release process, promoting ~145 commits
that had accumulated on `dev` since 0.1.9. Grouped by area:

### Nav-data pipeline (packtools / pyefis_data)
- **New pack kinds:** `navaids` (NASR NAV/FIX/AWY) and `rivers` (OSM river +
  canal). Rivers renders on the moving map; the pack is published after this
  release lands the kind on `main`.
- Manifest `select()` picks the **newest** edition for no-date (BULK) packs —
  fixes devices ignoring a re-issued water/highways cycle.
- NASR **schema guard**: fail the build on upstream NASR schema drift.
- Terrain: national **mosaic** pack + mip pyramid in region packs; DEFLATE
  compression on by default; containerized cloud/QNAP build.
- Device updater: config-pull install + auto-rollback, multi-screen panels,
  `--wait-online` boot-race fix.

### Configurator (web panel editor)
- Full instrument **twin** catalogue + SVS/attitude preview fidelity pass
  (issue #67); data-driven SVS preview on canvas.
- Accounts/auth, projects/devices/configs CRUD, R2/D1 wiring, device pairing.
- **DEV/QA/PROD environment split**: per-env R2 prefixes + separate D1; a push
  to an env branch deploys that env ([docs/environments.md](docs/environments.md)).
- Soft controls: Button conditions/actions editor; Knob + Control-Bindings specs.
- Aircraft Parameters (CAN-FIX) page — D1 migration `0002_aircraft_profiles`.

### Project
- Licensing: per-file **SPDX** (Apache-2.0 tools / AGPL configurator), DCO
  sign-off, `LICENSE-AUDIT`, AC-DP-001 defensive-publication draft.
- CI: per-env configurator deploy; Node 22/24 bumps.
- Docs: docs index, **release process** + **dev.navdata** design note.

## 0.1.9 and earlier

See git history — this is the first CHANGELOG.
