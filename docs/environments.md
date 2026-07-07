<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DEV / QA / PROD environments (spec / plan)

Status: **DRAFT for review** (2026-07-07). Concerns the **configurator** (the
Cloudflare Worker at pyefis.aerocommons.org), the **editor assets** on R2, and the
**branch structure** across pyEfis / makerplane-data / fix-gateway. Companion to
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
| Domain | `pyefis.aerocommons.org` | `dev.pyefis.aerocommons.org` | `qa.pyefis.aerocommons.org` |
| D1 | `makerplane_configurator` | `…_dev` | `…_qa` |
| KV | (one) | own | own |
| R2 asset prefix | `assets/editor/` | `assets/editor/dev/` | `assets/editor/qa/` |
| R2 config prefix | `configs/` | `configs-dev/` | `configs-qa/` |
| Secrets | prod OAuth/mail | dev OAuth/mail | qa |

Isolation is the point: DEV has its **own** users/devices/designs (own D1) and its
**own** asset copy (own R2 prefix), so nothing a developer does can reach a real
aircraft or a real account. One shared R2 *bucket* is fine — isolation is by
**prefix**, which keeps billing/ops simple. (A separate bucket per env is a later
option if prefix-scoping proves leaky.)

`wrangler dev` (local, `http://localhost:8787`, `.dev.vars`) is the implicit
fourth, **LOCAL** — already supported; it should point at the DEV D1/R2 prefix or
a local emulation, never prod.

## 3. R2 asset layout (env-scoped prefixes)

Editor assets move under an env segment:

```
  makerplane-configs/
    assets/editor/prod/schema.json  groups.json  palette/*.svg  svs/*.{json,webp}
    assets/editor/dev/   …same shape…
    assets/editor/qa/    …same shape…
    configs-prod/<user>/<device>/v<n>.yaml
    configs-dev/…   configs-qa/…
```

The Worker learns its env from a non-secret var (`ENV: "dev"|"qa"|"prod"`) and its
`/assets/*` handler serves `assets/editor/${ENV}/…`; the config API reads/writes
`configs-${ENV}/…`. pyEfis CI uploads generated assets to `assets/editor/${ENV}/`
for the env its branch maps to (section 5). **Nothing else in the Worker changes**
— only the prefix it composes.

Migration note: keep the current bare `assets/editor/…` working as a **prod
alias** during the transition (section 8) so a half-migrated state never dark-holes
the live editor.

## 4. Branch structure

Each repo gets the same three long-lived lines; the names below are the target
(current lines in parentheses):

| Repo | dev | qa | prod |
|---|---|---|---|
| **makerplane-data** (configurator + assets) | `develop` (← `feat/accounts-auth`) | `qa` | `main` |
| **pyEfis** (asset *source* + device code) | `develop` (← `display-changes`) | `qa` | `main` (← `gpu-required`) |
| **fix-gateway** (device data hub) | `develop` (← `gcu`) | `qa` | `main` |

- **pyEfis is the asset source.** Its branch decides which `assets/editor/${env}/`
  the generated `schema.json`/SVGs land in. `develop` → dev prefix, etc.
- **makerplane-data** owns the Worker deploy per env and the config storage.
- Feature branches (like `controls`) cut from `develop`, merge back to `develop`;
  promotion to `qa`/`main` is a separate, reviewed merge.

Promotion is **forward-only merges**: `develop → qa → main`. A hotfix branches
from `main`, lands on `main`, and is merged back down. This is the structure Bill
described ("merge controls back into origin branches, not main directly") made
uniform across the three repos.

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
  "r2_buckets": [{ "binding": "CONFIGS", "bucket_name": "makerplane-configs" }],

  "env": {
    "dev": {
      "name": "makerplane-configurator-dev",
      "vars": { "APP_URL": "https://dev.pyefis.aerocommons.org", "ENV": "dev" },
      "routes": [{ "pattern": "dev.pyefis.aerocommons.org", "custom_domain": true }],
      "d1_databases": [{ "binding": "DB", "database_name": "makerplane_configurator_dev", "database_id": "…dev…" }],
      "r2_buckets": [{ "binding": "CONFIGS", "bucket_name": "makerplane-configs" }]
    },
    "qa": { "…same shape, qa ids/domain…": true }
  }
}
```

Deploy per env: `wrangler deploy --env dev`. Secrets are per-env
(`wrangler secret put SESSION_SECRET --env dev`). Migrations run per D1
(`wrangler d1 migrations apply DB --env dev`). The R2 binding is the same bucket in
every env; env isolation is the **prefix** the Worker composes from `ENV`.

## 6. CI/CD — automate what is now hand-work

The manual `wrangler r2 object put` + `wrangler deploy` become GitHub Actions keyed
on branch:

- **makerplane-data** push → `wrangler deploy --env {env}` + `d1 migrations apply
  --env {env}` for the branch's env.
- **pyEfis** push → regenerate `schema.json` (`python -m pyefis.editor.schema`),
  palette SVGs, `groups.json`, SVS previews → upload to `assets/editor/{env}/`.
  (The `cyclical.yml` workflow already checks out `billmallard/pyEfis` for the
  data-tool shim, so pulling pyEfis in CI is established.)
- **Promotion** (`develop → qa → main` merge, or a manual "promote" action):
  re-generate assets from the promoted pyEfis ref into the target prefix and
  `wrangler deploy --env {target}`. Assets are reproducible from source, so promote
  by **re-generating**, not copying, to avoid drift.

Credential note: the R2-scoped token in `CloudFlare R2 Bucket Keys.txt` can `put`
objects but **cannot** deploy Workers — Worker deploys use the wrangler OAuth /
an account API token in CI. Keep that split (see configurator/CLAUDE.md).

## 7. Devices (the Pi) and env selection

A device pulls its panel config from *one* environment's configurator. Default =
**PROD**. The bench Pi (`ssh pyefis`) and any test unit point at **DEV** (or QA
during sign-off), set in the device's `pyefis-data` config (the base URL / env it
pairs and pulls from). This closes the loop: a design authored on DEV is pulled by
a DEV-paired device, so the whole chain — editor → assets → config → device — is
exercised in DEV before anything reaches a PROD aircraft. The nav-data updater
already has the config-pull + rollback machinery; env selection is one more field.

## 8. Migration plan (phased, prod-safe)

- **Phase 1 — stand up DEV.** Create the dev Worker (`…-dev`,
  `dev.pyefis.aerocommons.org`), dev D1, dev R2 prefixes; add the `env.dev` block +
  `ENV` var + prefix-composition in the Worker. Keep the bare `assets/editor/…`
  prod alias. Point our testing + the bench Pi at DEV. **Prod untouched.** This
  alone ends "upload dev schema onto prod."
- **Phase 2 — QA + branch lines.** Add the `qa` env and rename/establish the
  `develop`/`qa`/`main` lines across the three repos (from the current
  feat-accounts-auth / display-changes / gcu / gpu-required).
- **Phase 3 — automate.** CI generates + uploads assets and deploys the Worker per
  branch; add the promotion action. Retire the manual `wrangler` steps.
- **Phase 4 — finish isolation.** Env-scoped config storage + device env pairing;
  drop the prod alias once everything reads `assets/editor/prod/`.

## 9. Open questions / decisions to lock

1. **Branch names.** `develop`/`qa`/`main`, or keep the descriptive current names
   as aliases during transition? (pyEfis `main` vs the upstream-PR base
   `gpu-required` needs care — see the pyEfis CLAUDE.md PR notes.)
2. **D1 per env vs shared with an `env` column.** Separate D1 (proposed) is clean
   isolation but triples migrations; shared-with-column is cheaper but risks a
   query forgetting the env filter. (Given the ownership-scoping already on every
   `/api` query, separate D1 is the safer default.)
3. **Domains.** `dev.` / `qa.` subdomains on the aerocommons.org zone — confirm the
   zone/cert story (prod already provisions its custom domain via wrangler).
4. **Config storage isolation.** Prefix (`configs-dev/`) vs separate bucket. Prefix
   matches the assets approach.
5. **Nav-data (navdata.aerocommons.org) scope.** OUT for now — the pack pipeline is
   its own prod system with its own R2 and cadence (section 10). This spec is the
   *configurator + editor-assets + device-config* plane only.
6. **Auth in DEV.** Separate Google OAuth client + a dev mail sender, or a dev-only
   magic-link bypass to keep DEV testing frictionless?

## 10. Non-goals

- **Not** the nav-data currency pipeline (packtools → navdata.aerocommons.org).
  That is a separately-versioned prod system; its own dev/staging is a distinct
  effort and is not in scope here.
- **Not** a rewrite of the Worker — environments ride the existing router; only the
  R2 prefix and a few bindings become env-derived.
- **Not** device fleet management — env *selection* per device is one config field,
  not a management console.
