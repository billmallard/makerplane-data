# Changelog

Notable changes to the `makerplane-data` packages (`packtools`, `pyefis_data`)
and the `configurator`. Semver on the Python package; process in
[docs/release_process.md](docs/release_process.md).

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
