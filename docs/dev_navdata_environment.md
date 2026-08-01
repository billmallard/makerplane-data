<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# dev.navdata — a DEV environment for the pack pipeline (design note)

Status: **design note**, 2026-08-01. Proposes the piece the environments spec
deliberately deferred. Extends `environments.md` (§9 locked 2026-07-08, which
recorded **"navdata OUT"** of the DEV/QA/PROD split). Not yet implemented;
open decisions are called out for Bill.

## The gap

The DEV/QA/PROD split gave the **configurator** three environments
(`pyefis-dev.aerocommons.org`, env-prefixed R2, separate D1). The **pack
origin** — `navdata.aerocommons.org`, the signed `manifest.json`, and the
nightly `cyclical.yml` cron — got none. So there is exactly one place to
publish a pack, and it is production. That is why, on 2026-08-01, a finished
`rivers` pack could not be tested end-to-end (build -> manifest -> device pull)
without publishing into the prod manifest and risking the prod cron
(`release_process.md`, the kind-on-main rule). A dev pack origin removes that
trap permanently.

## Design

Mirror the configurator's env-prefix pattern; **prod keeps the bare paths**.

- **Origin:** `dev.navdata.aerocommons.org`, a Cloudflare route to the same R2
  bucket under a **dev prefix** — `packs-dev/`, `manifest-dev.json`,
  `manifest-dev.json.minisig` — leaving prod on bare `packs/` + `manifest.json`.
  (A separate dev *bucket* is the alternative; a prefix is cheaper and matches
  how the configurator already isolates DEV/QA — decision D1 below.)
- **Publish to dev:** `packtool build-pack ... --upload` targeting the dev
  prefix + `--url-base https://dev.navdata.aerocommons.org/packs`. A `packtool`
  `--env dev|prod` switch (or an explicit `--prefix`/`--url-base` pair) selects
  it; prod stays the default so nobody publishes to prod by omission.
- **Dev manifest builder:** a dev publish path (manual `packtool`, or a
  `cyclical.yml` variant keyed to the dev prefix, run from `dev`). Because it
  runs `dev` code, it knows every kind `dev` knows — so a brand-new kind builds
  and validates here with no prod exposure.
- **Devices:** a bench box opts into dev by setting its updater base URL
  (`data.yaml` `base_url` / `--base-url https://dev.navdata.aerocommons.org`).
  The verify-then-atomic-swap contract is unchanged; only the origin differs.
  Production devices are untouched.

## Why this is the durable fix for the 2026-08-01 blocker

The dev manifest is a **separate object** under a separate prefix, so a
pre-release pack (rivers today, airspace/charts tomorrow) lives only in
`manifest-dev.json`. The prod cron on `main` reads `manifest.json` and never
sees the dev-only kind, so it cannot die on it. Validate on dev, then let the
kind reach prod through a normal release. No cherry-picks, no prod risk.

## Open decisions (Bill)

1. **Prefix vs separate bucket** for dev (recommend: prefix, mirrors the
   configurator; prod bare, dev under `*-dev`).
2. **Signing key** — reuse the prod minisign key so the device's embedded
   public key verifies dev packs too (simplest; recommend), or a dev key the
   bench devices trust explicitly (stronger isolation, more device config).
3. **Dev publish trigger** — manual `packtool` only (simplest to start), or a
   scheduled dev cron alongside the prod one.
4. **QA?** — the configurator has QA; the pack origin may only need DEV + PROD
   (packs are validated on a bench, not by a staged web deploy). Decide whether
   `qa.navdata` is worth it or DEV+PROD suffices.
5. **DNS/route** — add `dev.navdata.aerocommons.org` in Cloudflare pointing at
   the bucket/Worker with the dev prefix.

## Relationship to the release process

`release_process.md` owns *when work reaches prod*; this note owns *where a
pack is validated before it does*. Together: register a kind on `dev` ->
publish it to dev.navdata -> validate on a bench device -> cut the release
(kind lands on `main`) -> publish to production navdata.
