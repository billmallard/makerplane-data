<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Release process — versioned promotion to production

Status: **process contract**, adopted 2026-08-01. Governs how work on `dev`
reaches production (`main`) in `makerplane-data`. The same `dev -> qa -> main`
+ tag shape generalises to `pyEfis` and `fix-gateway` (whose PROD branch is
`master`); this doc is written against `makerplane-data` because that is where
versioning, packs, and the nightly cron live and where the need first bit.

## Why this exists

The branch model (`environments.md`, 2026-07-08) made promotion "the merge:
`dev -> qa -> main`" but never said *when* or made it a deliberate, marked
event. The result, by 2026-08-01: **`main` drifted ~100 commits behind `dev`**
(the whole configurator, the env split, terrain mosaic, navaids, the manifest
`select()` fix), and a new pack kind (`rivers`) could not be published because
the **production nightly cron runs from `main`** and `Manifest.load()` raises
`ManifestError` on a kind `main` does not know (`manifest.py` KINDS check) —
the exact incident the "kind on main before publish" rule was written for. A
release is not a background merge; it is the event that keeps prod current and
lets new kinds ship. Cut them on a cadence, do not let `main` drift.

## Versioning

Semver `MAJOR.MINOR.PATCH` on the package (`pyproject.toml`; keep the README
version string in sync — `chore: sync version strings` is a real prior commit).

- **PATCH** — fixes, no new capability (e.g. a manifest bug fix).
- **MINOR** — new capability, backward-compatible (new pack kind, new
  configurator feature, the env split).
- **MAJOR** — a breaking change to a frozen contract (`manifest.json` schema,
  the pack format, the device API).

The 2026-08-01 backlog is a **MINOR**: new kinds + features, nothing breaks a
device on the current pack format. Cut it as **0.3.0** (from 0.2.11).

## The steps

1. **Pick the version** and update `pyproject.toml` (+ README string).
2. **CHANGELOG.md** — one entry per release, grouped by area (configurator,
   updater/packtools, environments, terrain, navaids, docs). Create the file at
   the first release; write it from `git log <lastTag>..dev`.
3. **Pre-flight readiness check** (the gate — a release is where diligence is
   owed, because a merge to `main` deploys to prod):
   - CI green on `dev` (ci.yml: SPDX + pytest + `run_cyclical --dry-run`).
   - **D1 migrations reviewed** — every migration additive/idempotent against
     the PROD D1; no drop/rename that loses prod data. A destructive migration
     is a **blocker**, not a note.
   - **Prod resource targets correct** — the env-composed R2 prefixes/D1 land
     PROD on the bare prefixes (`assets/editor/`, `configs/`, `packs/`,
     `manifest.json`), never a dev/qa resource.
   - No WIP / feature-flagged-off / "do not ship" code in the range.
   - New pack kinds are registered in `packmeta.KINDS` + the updater (so the
     post-release cron on `main` can load a manifest that contains them).
4. **Promote by merge, verifying each hop:**
   `dev -> qa` (confirm the qa deploy is healthy), then `qa -> main` (confirm
   the prod Worker deploy + that the **next nightly cron loads the manifest
   without error**). Promotion regenerates assets from source; it does not copy.
5. **Tag** `vX.Y.Z` on `main` and push the tag. The tag is the release.
6. **Post-release** — publish any packs that were waiting on the kind reaching
   `main` (see below), then re-check cron health the next morning
   (`watch/FAILURES.md`).

## The pack-kind / publish rule (why dev.navdata exists)

A new pack **kind** must reach `main` (via a release) **before** its first
**production** publish, because the prod cron loads and validates the live
manifest and dies on an unknown kind. To validate a new kind *before* the
release — the thing that was impossible on 2026-08-01 — publish it to the
**dev pack origin** (`dev_navdata_environment.md`), which has its own manifest
and never touches the prod cron. Flow for a new kind:

```
register kind on dev  ->  publish pack to dev.navdata  ->  validate on a
bench device pointed at dev  ->  cut the release (kind lands on main)  ->
publish the pack to production navdata
```

## Cadence

Cut when a coherent, validated body of work has accumulated on `dev` — not
never (the drift above) and not per-commit (churn). A good trigger: a feature
family is done and bench-validated, or a new pack kind is ready to ship.
