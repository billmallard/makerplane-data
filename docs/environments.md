<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DEV / QA / PROD environments (spec / plan)

Status: **Decisions locked** (Bill, 2026-07-08; see §9). Was DRAFT 2026-07-07.
Concerns the **configurator** (the Cloudflare Worker at pyefis.aerocommons.org),
the **editor assets** on R2, and the **branch structure** across pyEfis /
makerplane-data / fix-gateway. Companion to
[configurator/CLAUDE.md](../configurator/CLAUDE.md) and
[control_bindings.md](control_bindings.md) (whose Phase 1 testing surfaced the pain
this fixes).

## 1. The gap (felt, not theoretical)

There is exactly one environment today: **prod**. One Worker
(`makerplane-configurator`), one D1 (`makerplane_configurator`), one R2 bucket
(`makerplane-configs`) holding *both* editor assets (public `assets/`) and every
user's saved configs (private `configs/`), one domain
(pyefis.aerocommons.org). The pyEfis-generated `schema.json` / palette SVGs /
`groups.json` are uploaded straight to that prod bucket by hand
(`wrangler r2 object put …`).

So testing an in-flight change means **touching prod**. Concretely: to let the
control-bindings map settings be wired in the editor, we just uploaded a
`display-changes` (dev) `schema.json` onto the **live** `assets/editor/schema.json`
— the only slot there is. That is the anti-pattern. A dev schema, a half-built
Phase 2 UI, or a broken migration has nowhere to live except in front of real
users.

**Goal:** three coherent environments — **DEV**, **QA**, **PROD** — so a change
proves out on DEV, promotes to QA for sign-off, and reaches PROD only when it is
ready. "Can't test until merge" becomes "test on DEV, promote to PROD."

## 2. What an "environment" is here

An environment is a coherent, isolated slice of the whole configurator stack:

| Piece | PROD (today) | DEV | QA |
|---|---|---|---|
| Worker | `makerplane-configurator` | `…-dev` | `…-qa` |
| Domain | `pyefis.aerocommons.org` | `pyefis-dev.aerocommons.org` | `pyefis-qa.aerocommons.org` |
| D1 | `makerplane_configurator` | `…_dev` | `…_qa` |
| KV | (one) | own | own |
| R2 asset prefix | `assets/editor/` (bare) | `assets/editor/dev/` | `assets/editor/qa/` |
| R2 config prefix | `configs/` (bare) | `configs-dev/` | `configs-qa/` |
| Secrets | prod OAuth/mail | dev (+ auto-login) | qa (+ auto-login) |

Isolation is the point: DEV has its **own** users/devices/designs (own D1) and its
**own** asset copy (own R2 prefix), so nothing a developer does can reach a real
aircraft or a real account. One shared R2 *bucket* is fine — isolation is by
**prefix**, which keeps billing/ops simple. (A separate bucket per env is a later
option if prefix-scoping proves leaky.)

`wrangler dev` (local, `http://localhost:8787`, `.dev.vars`) is the implicit
fourth, **LOCAL** — already supported; it should point at the DEV D1/R2 prefix or
a local emulation, never prod.

## 3. R2 asset layout (env-scoped prefixes, prod bare)

Editor assets move under an env segment for DEV/QA; **PROD keeps the bare prefix
it uses today** (§9.4), so no live object is ever migrated:

```
  makerplane-configs/
    assets/editor/schema.json  groups.json  palette/*.svg  svs/*.{json,webp}   <- PROD (bare)
    assets/editor/dev/   …same shape…
    assets/editor/qa/    …same shape…
    configs/<user>/<device>/v<n>.yaml                                          <- PROD (bare)
    configs-dev/…   configs-qa/…
```

The Worker learns its env from a non-secret var (`ENV: "dev"|"qa"|"prod"`) and
composes the R2 prefix from it: **prod uses the bare prefixes**
(`assets/editor/`, `configs/`); **dev/qa append their env** (`assets/editor/dev/`,
`configs-dev/`). Its `/assets/*` handler serves the composed asset prefix; the
config API reads/writes the composed config prefix; pyEfis CI uploads generated
assets to the prefix its branch maps to (section 5). **Nothing else in the Worker
changes** — only the prefix it composes.

Because prod keeps the bare prefix **permanently** (not as a transient alias), the
live editor is never in a half-migrated state: prod reads exactly what it reads
today, and dev/qa are purely additive.

## 4. Branch structure

Three long-lived env lines per repo — `dev`, `qa`, and the repo's existing default
(`main` or `master`) as prod. **Existing descriptive branches are preserved, not
renamed** — the Pi's git-pull origin and pyEfis `gpu-required` (the base of
upstream PR #274) keep working untouched. `dev` and `qa` are new lines added
alongside them.

| Repo | prod (default) | dev (new) | qa (new) |
|---|---|---|---|
| **makerplane-data** (configurator + assets) | `main` | from `main` | from `main` |
| **pyEfis** (asset *source* + device code) | `master` | from `master` ← `gpu-required` ← `display-changes` | from `master` |
| **fix-gateway** (device data hub) | `master`/`main` | from default ← `gcu` | from default |

- **Stand up `dev`** by cutting it from the repo's default branch and merging the
  repo's active work into it. For pyEfis that is `gpu-required` then
  `display-changes` (in that order; `display-changes` is ahead of `gpu-required`,
  so the second merge brings the extra work on top). Cut `qa` from the default;
  promote `dev` into it.
- **pyEfis is the asset source.** Its branch decides which `assets/editor/…`
  prefix the generated `schema.json`/SVGs land in: `dev` → `assets/editor/dev/`,
  `qa` → `assets/editor/qa/`, prod → bare `assets/editor/`.
- **makerplane-data** owns the Worker deploy per env and the config storage.
- Feature branches (like `controls`) cut from `dev`, merge back to `dev`;
  promotion to `qa`/prod is a separate, reviewed merge.

Promotion is **forward-only merges**: `dev → qa → prod` (main/master). A hotfix
branches from the prod default, lands there, and is merged back down. This is the
structure Bill described ("merge controls back into origin branches, not main
directly") with the existing branches kept intact rather than renamed.

**Our "PROD" is the fork's pre-prod.** These three env lines live on the **fork**
(`billmallard/*`). The fork's prod — `main`/`master`, deployed to
pyefis.aerocommons.org — is the **staging point from which pull requests are
originated to upstream `makerplane/*`**, the ultimate production destination. The
full pipeline is `feature → dev → qa → main (fork prod, deployed) → PR →
makerplane/* (upstream prod)`. Env isolation here protects the fork's deployed state
and real fork accounts; it does not replace upstream review, which stays the gate to
true production.

## 5. The native mechanism: wrangler environments

Cloudflare Workers already model this with `env.<name>` blocks in
`wrangler.jsonc` — per-env `name`, `routes`, `vars`, and bindings. Sketch (prod
stays the top-level default; dev/qa override):

```jsonc
{
  "name": "makerplane-configurator",
  "vars": { "APP_URL": "https://pyefis.aerocommons.org", "ENV": "prod" },
  "routes": [{ "pattern": "pyefis.aerocommons.org", "custom_domain": true }],
  "d1_databases": [{ "binding": "DB", "database_name": "makerplane_configurator", "database_id": "…prod…" }],
  // one bucket, every env; prod composes the bare R2 prefix, dev/qa append ENV
  "r2_buckets": [{ "binding": "CONFIGS", "bucket_name": "makerplane-configs" }],

  "env": {
    "dev": {
      "name": "makerplane-configurator-dev",
      "vars": { "APP_URL": "https://pyefis-dev.aerocommons.org", "ENV": "dev" },
      "routes": [{ "pattern": "pyefis-dev.aerocommons.org", "custom_domain": true }],
      "d1_databases": [{ "binding": "DB", "database_name": "makerplane_configurator_dev", "database_id": "…dev…" }],
      "r2_buckets": [{ "binding": "CONFIGS", "bucket_name": "makerplane-configs" }]
    },
    "qa": { "…same shape, qa ids/domain…": true }
  }
}
```

Deploy per env: `wrangler deploy --env dev`. Secrets are per-env
(`wrangler secret put SESSION_SECRET --env dev`). Migrations run per D1
(`wrangler d1 migrations apply DB --env dev`) — separate DB per env (§9.2). The R2
binding is the same bucket in every env; env isolation is the **prefix** the Worker
composes from `ENV` (prod → bare; dev/qa → env-suffixed).

`pyefis-dev.aerocommons.org` / `pyefis-qa.aerocommons.org` are **second-level**
hosts on the aerocommons.org zone, so the zone's Universal SSL (`*.aerocommons.org`)
covers them and `custom_domain: true` provisions them exactly as prod does today —
no third-level cert story (§9.3).

## 6. CI/CD — automated (implemented 2026-07-08)

The manual `wrangler r2 object put` + `wrangler deploy` are now GitHub Actions keyed
on branch:

- **makerplane-data** `.github/workflows/deploy-configurator.yml` — push to
  `dev`/`qa`/`main` → `wrangler deploy --env {env}` + `d1 migrations apply` for that
  env (`main` = the bare-prod top-level default).
- **pyEfis** `.github/workflows/editor-assets.yml` — push to `dev`/`qa`/`master` →
  regenerate `schema.json` + `groups.json` and upload to the branch's asset prefix
  (`assets/editor/{env}/`; `master` → bare `assets/editor/`). **Palette SVGs and the
  SVS preview patches are NOT built in CI**: headless runners render the Qt widgets as
  placeholders, and the SVS patches need local SRTM/NASR terrain data. Both stay
  **local-gen** — `tools/build_editor_assets.py` on a machine with a display,
  `tools/export_svs_preview_patch.py` — and are uploaded by hand.
- **Promotion is the merge**: `dev → qa → main` each re-triggers the target env's
  deploy on push. No separate promote action. Assets are reproducible from source, so
  promotion re-generates rather than copies.

Both workflows **skip green until `CLOUDFLARE_API_TOKEN` is set** (an in-step guard),
so landing them caused no red runs.

Credential note: Worker deploys, D1 migrations, and editor-asset R2 writes all use a
single **`CLOUDFLARE_API_TOKEN`** (scopes: Account = Workers Scripts + Workers KV +
Workers R2 + D1 + Account Settings:Read; Zone `aerocommons.org` = Workers Routes:Edit
+ Zone:Read) plus `CLOUDFLARE_ACCOUNT_ID`, set as GH secrets in **both** repos. The
R2-scoped S3 keys in `CloudFlare R2 Bucket Keys.txt` / the `R2_*` GH secrets are
**navdata-bucket-scoped** and cannot write `makerplane-configs` — don't reuse them
here. CI runners must use **Node ≥ 22** (wrangler 4's floor).

## 7. Devices (the Pi) and env selection

A device pulls its panel config from *one* environment's configurator. Default =
**PROD**. The bench Pi (`ssh pyefis`) and any test unit point at **DEV** (or QA
during sign-off), set in the device's `pyefis-data` config (the base URL / env it
pairs and pulls from — e.g. `pyefis-dev.aerocommons.org`). This closes the loop: a
design authored on DEV is pulled by a DEV-paired device, so the whole chain —
editor → assets → config → device — is exercised in DEV before anything reaches a
PROD aircraft. The nav-data updater already has the config-pull + rollback
machinery; env selection is one more field.

## 8. Migration plan (phased, prod-safe)

- **Phase 1 — stand up DEV.** Create the dev Worker (`…-dev`,
  `pyefis-dev.aerocommons.org`), dev D1, dev KV, dev R2 prefixes; add the `env.dev`
  block + `ENV` var + prefix-composition in the Worker (prod → bare, dev → suffixed).
  Point our testing + the bench Pi at DEV. **Prod untouched.** This alone ends
  "upload dev schema onto prod."
- **Phase 2 — QA + branch lines.** Add the `qa` env and establish the `dev`/`qa`
  lines across the three repos (cut from each default, seeding `dev` from the
  current feat-accounts-auth / `gpu-required`+`display-changes` / `gcu` work;
  existing branches preserved, not renamed).
- **Phase 3 — automate. DONE (2026-07-08).** CI deploys the Worker + migrates D1
  (makerplane-data) and regenerates + uploads `schema.json`/`groups.json` (pyEfis)
  per branch; promotion is the merge. Palette SVGs + SVS previews stay local-gen
  (§6). The manual `wrangler` deploy/schema steps are retired.
- **Phase 4 — finish isolation.** Env-scoped config storage + device env pairing.
  (No prod-alias to drop — prod is permanently bare per §9.4.)

## 9. Decisions (Bill, 2026-07-08)

The §9 open questions are resolved; the spec above already reflects them.

1. **Branch model — new lines, existing branches preserved.** Each repo gains
   `dev`/`qa` alongside its existing default (`main` or `master`) as prod;
   **existing descriptive branches stay untouched**, preserving the Pi's git-pull
   origin and keeping pyEfis `gpu-required` intact as the base of upstream PR #274.
   Stand up `dev` by cutting from the default and merging the repo's active lines
   into it (pyEfis: `dev` ← `gpu-required` ← `display-changes`, in that order —
   `display-changes` is ahead, so it layers on top). Cut `qa` from the default;
   promote `dev` into it. Promotion is **forward-only: `dev → qa → prod`**; a
   hotfix branches from prod and merges back down.

2. **Separate D1 per env.** Each env gets its own D1
   (`makerplane_configurator` / `_dev` / `_qa`) — clean isolation, so no query can
   leak DEV/QA data into PROD and a dev schema can lead prod safely. The cost
   (migrations applied per-DB, ~3×) is a trivial CI loop, preferred over a shared
   DB with an `env` column where one forgotten `AND env=?` bleeds across
   environments on top of the existing `user_id` scoping.

3. **Domains — second-level `-env` hosts, prod bare.**
   `pyefis-dev.aerocommons.org` and `pyefis-qa.aerocommons.org`; PROD stays bare
   `pyefis.aerocommons.org`. Second-level hosts are covered by the zone's Universal
   SSL (`*.aerocommons.org`) and provision via wrangler `custom_domain: true`
   exactly as prod does today — no third-level cert story (which
   `dev.pyefis.aerocommons.org` would have needed).

4. **Storage — prefix isolation, prod permanently bare.** One R2 bucket
   (`makerplane-configs`); PROD keeps the current bare prefixes (`configs/`,
   `assets/editor/`), DEV/QA are additive (`configs-dev/`, `assets/editor/dev/`,
   and `-qa`). No live prod object is ever migrated, and because bare *is* prod's
   permanent layout there is no transition alias to retire (this revises §8
   Phase 4). Separate buckets remain the later fallback only if prefix-scoping
   proves leaky.

5. **Nav-data out of scope — confirmed.** `navdata.aerocommons.org` (the
   packtools → R2 currency pipeline) is its own prod system with its own R2,
   cadence, and signing key; its dev/staging is a separate effort. This spec is the
   **configurator + editor-assets + device-config** plane only (see §10).

6. **Dev auth — auto-login in DEV + QA.** DEV and QA expose a dev-only endpoint
   that mints a session directly — no Google, no email, no magic link — so
   **automated UI tests** (landing soon) authenticate with zero friction; a fixed
   dev user by default. It is gated **belt-and-suspenders** and fails closed: on
   `ENV !== "prod"` **and** an explicit `DEV_AUTH` var that is simply never set in
   prod, so the endpoint cannot exist in PROD — where Google OAuth + real
   magic-link email remain the only ways in. Real OAuth / magic-link still work in
   DEV for hand testing; auto-login is the automation path.

## 10. Non-goals

- **Not** the nav-data currency pipeline (packtools → navdata.aerocommons.org).
  That is a separately-versioned prod system; its own dev/staging is a distinct
  effort and is not in scope here.
- **Not** a rewrite of the Worker — environments ride the existing router; only the
  R2 prefix and a few bindings become env-derived.
- **Not** device fleet management — env *selection* per device is one config field,
  not a management console.
