# Changelog

Notable changes to the `makerplane-data` packages (`packtools`, `pyefis_data`)
and the `configurator`. Semver on the Python package; process in
[docs/release_process.md](docs/release_process.md).

## Unreleased

### Device updater (pyefis_data)
- **`config-pull` no longer adds `screens/virtualvfr_db.yaml` for SVS-off
  panels either (AER-5421).** pyEfis #457 deleted the legacy CIFP Virtual
  VFR overlay that read it. Release gate: a device must run a pyEfis that
  carries #457 before it gets this `config-pull`.

## 0.5.0 — 2026-11-11

Promotes the `dev` work since 0.4.0 (52 PRs). Its core is the device-side
safety work: the install notice, refusal of a replayed older catalog, and the
opt-in safety advisory feed. It also carries the testing-visibility follow-ons
(TV4 to TV10) and new procedures and airspace content.

### Device updater (pyefis_data)
- **Software notice accepted at install and update (L2, AER-4340).**
  `update` and `import` install nothing until the notice is accepted for the
  installed `pyefis-data` version, and each accept is appended to
  `~/.local/state/makerplane/acknowledgments.jsonl`. Bench units
  (`bench_auto_accept: true`) accept automatically and say so.
  See [docs/install_acknowledgment.md](docs/install_acknowledgment.md).
- **Opt-in safety advisory feed (L5, AER-5148).** Off by default:
  `pyefis-data advisories --enable` / `--disable`. When on, `update` and
  `import` fetch the signed `advisories.json`, refuse a lower sequence, match
  it on the device and write an `advisories` block into `status.json`.
  Nothing about the device is sent, and an empty feed prints nothing.
  See [docs/advisories.md](docs/advisories.md).
- **`config-pull` drops the `virtualvfr_db.yaml` include** for SVS panels;
  legacy Virtual VFR is marked deprecated (AER-4354).
- **An older signed catalog is refused (CERT3, AER-5033).** `update`,
  `import`, `status` and `catalog` compare a verified manifest's `generated`
  with the cached one. If it is older, they refuse it, keep the cached
  manifest, and install nothing. `status` (and `status.json`) gains a
  `catalog` row, `CATALOG REFUSED`, severity white, plus a top-level
  `rollback_refused` field. `update`/`import` then exit 2 with the reason.
  `--allow-rollback` accepts the older catalog on purpose and appends a JSON
  line to `<root>/rollback.log`. It cannot be combined with `--dry-run`.
- **`notice` on every `status` row (AER-4351).** `pyefis-data status --json`
  and the `status.json` written after `update` now carry `packs[i].notice`:
  the `notice` of the manifest entry for the installed cycle. It is `""` when
  no cycle is installed or the entry predates the field. Additive; existing
  readers are unaffected. pyEfis's About screen reads it from `status.json`.

### Packs and pipeline (packtools)
- **Procedures and airspace content.** MSA from CIFP PS records (AER-4446)
  and Grid MORA from CIFP AS records (AER-4447) in `procedures-conus`; an
  airspace pack built from CIFP UC/UR records (AIRSP1, AER-4445).
  PROC-TBL-001 refuses a procedures pack missing a builder table (AER-4598).
- **Manifest writes are a compare-and-set (MANIFEST1, AER-5176):** writers
  that overlap keep both entries instead of the last one winning.
- **Provenance.** Each pack records the pyEfis commit that built it (CERT5a,
  AER-5181). Actions-built packs carry build-provenance attestations (TV10,
  AER-4346). CI verifies packtools signatures with the stock `minisign`
  binary (AER-5240).
- **Terrain and water.**
  - Stage-only terrain re-edition with a forced `.wmask` rebuild and
    `wmask-diff` (AER-4595).
  - A GLO-30 fetch window and a tree check (AER-4706). CI fails when a
    region reaches past the tile list (AER-5175).
  - `water.yml` takes a staged, workstation-built `water.sqlite` (AER-4676).
  - The roads build refuses an empty state extract directory (AER-4332).
  - The cloud image installs `libglib2.0-0t64` so ACC-001 can import pyEfis's
    SVS reader.

### Testing visibility (packtools)
- **Retro records (TV4, AER-4444)** for the live packs, bound through a narrow
  signing exception. A v1 retro previous is measured from its pack when it
  lacks per-state metrics (AER-4676).
- **Pack inspector (TV6):** an in-browser inspector that checks the signature
  and sha256 before opening a pack.
- **Catalogue v2 (TV7):** cross-source checks, golden facts, per-state diffs
  and diff maps, terrain checks.
- **Community loop (TV8, AER-4345):** issue forms, pack labels, a `status.json`
  overlay and a GitHub Release per publish.
- **PR impact report (TV9, AER-4572)** for builder changes.
- **Pack format pages (SCH1)** at `navdata.aerocommons.org/schema/`, generated
  from the packs.
- `packtool report --auspex` carries the approved Auspex section into the
  record (AER-4606). The report pages drop the standing-text heading
  (AER-4532).

### Configurator and device config
- **Aircraft profile.**
  - Validated on the server (CERT11, AER-5060). The aircraft form also
    enforces the server's strict speed order (#179).
  - Fuel profile schema 2, with stable tank ids, labels, slots and
    capacity (FUEL1a, AER-5271), migrated in the browser on load.
  - Ownship identity: registration, derived ICAO address, hex override
    (AER-5147).
  - `IAS.Vgnd`, the on-ground IAS limit for the engine alarm, is exposed
    (AER-4636).
- **Theming:** token-or-custom colour controls and a panel theme picker
  (AER-5084).
- **Preview twins:**
  - moving-map ND mode, EXPANDED MAP and CTR MAP (G11b, AER-5272);
  - the moving-map zoom rail (G23);
  - range rings (G13, AER-4461);
  - `map_style` nav_only (AER-4472);
  - `heading_display` previews white (AER-5092);
  - the SVS preview ignores the removed `svs_terrain_fill` (AER-5151);
  - tab_section font size (lockstep with pyEfis #356).
- Schema-numeric options are coerced to numbers on save.

### Docs and CI
- The GitHub wiki is generated from `docs/wiki/` on merge to `dev`.
- A weekly scheduled CI run on `dev` and `main` (AER-4353).
- New and updated docs:
  - the terrain runbook (build01 default host, QNAP fallback);
  - the fuel-provider design brief (FUEL1);
  - `water.md` on `.wmask` consumers;
  - the LICENSE-AUDIT entry for fix-gateway's dronecan dependency;
  - the cloud README QNAP key note.

### Operational notes for this release
- **Automatic updates pause on devices until someone accepts the notice for
  0.5.0.** A new `pyefis-data` version requires a new accept (L2), so a box
  that upgrades stops installing data until someone runs
  `pyefis-data accept` or accepts in the picker. Bench units with
  `bench_auto_accept: true` continue.
- **After the merge to `main`:** dispatch the `advisories` workflow (dry run,
  then for real) to publish the empty sequence-1 feed Bill approved on
  AER-5148.
- The nightly cron on `main` starts running the catalogue v2 checks, the
  manifest compare-and-set and the pyEfis commit record. Check the first
  nightly after the release.
- No D1 migrations are in this release, and `wrangler.jsonc` is unchanged.
- No new pack kinds, and `MANIFEST_VERSION` stays 1.

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
