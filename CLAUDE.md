# CLAUDE.md — makerplane-data

Orientation for Claude sessions in this repo. Read this first, then the
human-facing [README.md](README.md). Two sibling CLAUDE.md files carry detail
this one deliberately does not duplicate:

- **[configurator/CLAUDE.md](configurator/CLAUDE.md)** — the web panel editor
  (Cloudflare Worker, `pyefis.aerocommons.org`). Read that before touching
  anything under `configurator/`.
- **[../CLAUDE.md](../CLAUDE.md)** — the `makerplane/` umbrella: how this repo
  fits the wider avionics stack (pyEfis, FIX-Gateway, the instrument-widget
  pipeline). Not a git repo itself; each subdir is.

## What this repo is (two products, one MIT-rooted repo)

1. **A Garmin-style nav-data currency system** — the Python side (`packtools/`
   builder + `pyefis_data/` on-Pi updater). Subscribe once, pull signed
   reference-data packs over WiFi or USB, and the EFIS tells you when something
   is stale. This is the repo's original purpose and where most of this doc's
   depth goes, because it has no other CLAUDE.md.
2. **The configurator** (`configurator/`) — a Cloudflare Worker web app for
   laying out pyEfis panels. Has its own CLAUDE.md; see above.

**Scope line (important):** this repo covers **reference-data currency only** —
terrain, airports, navaids, obstacles, water, roads, charts. It is **not** the
runtime flight-data bus. That contract is `canfix.json` / FIX-Gateway, in other
repos. Keep the two separate; don't blur pack data into the live bus.

## The one idea: the signed manifest is the contract

`manifest.json` is a single JSON catalog listing every pack with its currency
window, size, sha256, and URL. It sits between three legs that evolve
independently as long as its schema is frozen:

```
  LEG 1  BUILD   (packtools/)   GitHub Actions: fetch FAA/Copernicus/OSM,
                                build packs, embed pack_meta, sign the manifest
        │  packs + manifest.json + manifest.json.minisig
        ▼
  LEG 2  DISTRIBUTE             Cloudflare R2 (zero egress) at
                                navdata.aerocommons.org; static site + picker
        │  HTTPS  /  USB sneakernet
        ▼
  LEG 3  UPDATE  (pyefis_data/) verify signature -> verify sha256 -> stage
                                -> atomic symlink swap -> DATA flag on the EFIS
```

**Trust chain:** committed `keys/minisign.pub` verifies `manifest.json.minisig`
(ed25519, minisign format) → the manifest carries a per-pack sha256 → that
verifies each `.pack`. Compromising distribution is not enough to push a
malicious pack — that needs the offline secret key.

**Safety contract (avionics-adjacent — treat as load-bearing):** the Pi
verifies the manifest signature *before* trusting or caching it; a pack is
downloaded to `staging/`, sha256-verified against the signed manifest, and only
**then** moved into place and the `current` symlink atomically flipped. A bad
download, bad signature, or interrupted transfer can **never** disturb the live
data a running pyEfis is serving. Offline falls back to the last good cached
(still-verified) manifest. When editing `pyefis_data/core.py` or the CLIs,
preserve verify-then-swap; don't introduce a path that installs before verifying.

## Package map

| Path | Leg | What |
|---|---|---|
| **`packtools/`** | 1 | Pack builder. `cycles` (AIRAC/DOF date math), `signing` (minisign ed25519, pure PyNaCl — shared with the Pi), `packmeta` (self-describing header inside every pack), `manifest` (catalog schema + canonical bytes + validation + prune), `regions`/`regions.yaml` (8 N-America bboxes), `sources` (upstream FAA URLs), `fetch`, `build/` (shim to pyEfis tools), `upload` (R2/S3 + LocalStore), `publish`, `make_terrain`, `ourairports`, `run_cyclical` (the daily orchestrator), `cli` (`packtool`). |
| **`pyefis_data/`** | 3 | On-Pi updater. `config` (`~/.makerplane/pyefis/data.yaml`, construct-never-raises), `core` (`Updater`, `Remote`, inventory, the verify-then-swap install), `config_pull` (device panel-config pull + install + rollback), `cli` (`pyefis-data`), `systemd/` (user service + timer + USB-import udev/service). |
| **`configurator/`** | — | The web panel editor (Worker, TS). Own CLAUDE.md. |
| `.github/workflows/` | 1/2 | `ci.yml` (SPDX check + pytest + no-secrets dry-run), `cyclical.yml` (daily build+sign+upload), `terrain.yml`, `water.yml` (dispatch builds). |
| `site/` | 2 | Static Cloudflare site + on-device pack picker (`data.yaml.sample`). |
| `docs/` | — | Phase plan (`data_manager_implementation.md`), Cloudflare runbook, per-kind build notes (terrain/water/roads), licensing audit, the AC-DP-001 defensive publication, panel-config format, device deployment. |
| `keys/minisign.pub` | — | Signing **public** key (committed). The secret never is. |

**Pack kinds** (`packmeta.KINDS`): `navdata` (airports/runways), `navaids`
(NASR NAV/FIX/AWY), `obstacles` (FAA DOF), `terrain` (Copernicus GLO-30 region
tiles), `water` + `highways` (OSM, ODbL), plus `airports`/`cifp`. **CIFP build
is deferred** — its indexer is GPL and the tools here are permissively licensed;
it's registered as a source but not built.

## The CLIs (two entry points, `pyproject.toml [project.scripts]`)

`packtool` (build side):
```bash
packtool genkey --out keys                      # throwaway keypair (commit .pub only)
packtool build-pack SRC --id X --kind K ...      # built sqlite/zip -> signed pack (+ --upload to R2)
packtool make-terrain HGT_TREE --edition ...     # Copernicus GLO-30 -> region terrain packs
packtool verify work/manifest.json --pub keys/minisign.pub
python -m packtools.run_cyclical --dry-run       # what today's cron would build (no net, no secrets)
```

`pyefis-data` (on the Pi; production public key is embedded in `cli.py` so
verification needs no file and no network):
```bash
pyefis-data status | catalog | sources | drives     # (+ --json; the picker drives these)
pyefis-data update [--only id,id] [--source DIR] [--progress]   # pull+verify+atomic-swap
pyefis-data import <dir>                              # USB path, same verify contract
pyefis-data verify [path]                            # check a manifest signature
pyefis-data pair <code>                              # redeem a configurator claim code -> device token
pyefis-data config-pull [--wait-online N]            # pull this device's panel, install, restart pyEfis, roll back on crash
```

`update --only` persists the selection to `data.yaml` (the on-device picker *is*
the yaml editor); a user Cancel (SIGTERM) unwinds cleanly and leaves live data
untouched. `config-pull` snapshots the panel first and rolls back if the new
config crashes pyEfis — a stable PID alone is a false "up" (a lingering FIX
thread), so it also rejects tracebacks in the service log.

## Tests & CI

```bash
pip install -e .[dev]
python -m pytest        # 122 tests, ~3s, no network/secrets needed
```
CI (`ci.yml`) runs `scripts/spdx_check.py` (every source file must carry an SPDX
header), the full pytest, and a `run_cyclical --dry-run --date 2026-06-14`. PRs
also gate on **DCO sign-off** — every commit needs `Signed-off-by:` (see
CONTRIBUTING.md); commit with `git commit -s`.

## The pyEfis tool-sharing shim (don't vendor)

The FAA-data → sqlite build tools (`build_airport_db.py`, `build_obstacle_db.py`)
live in **pyEfis**, not here. `packtools/build/` invokes them by path via
`PYEFIS_TOOLS_DIR`; the `cyclical.yml` workflow checks out
`billmallard/pyEfis@dev` and points at its `tools/`. **Do not copy
that tool source into this repo** — one implementation. A standalone
`pyefis-tools` package is a later refactor (`pyproject.toml [tools]` extra is the
placeholder).

## Secrets — never commit or echo these

`.gitignore` blocks them, but be deliberate:

- `keys/minisign.sec` — the offline signing secret. Compromise = ability to sign
  malicious packs. In CI it's the `MINISIGN_SECRET_KEY` Actions secret (base64,
  unencrypted for unattended signing); never printed, never committed.
- `CloudFlare R2 Bucket Keys.txt`, `.env` — R2/S3 credentials for uploads. The
  R2 token is **R2-scoped only**: it can put objects, it **cannot** deploy
  Workers/D1/KV (that's `wrangler login` OAuth — see configurator/CLAUDE.md).
- Don't paste any of these into commits, logs, PRs, or chat.

## Deploy / publish notes

- The daily pipeline is **live and unattended**: `cyclical.yml` builds + signs +
  uploads to R2, served at `https://navdata.aerocommons.org` (manifest at
  `/manifest.json`). Reproduce-from-nothing runbook: `docs/cloudflare_setup.md`.
- **Rule (from prior incident):** a new pack `kind` must be merged to `main`
  **before** its first R2 publish — an unknown kind in a published manifest
  crashed the nightly cron. New kinds land in `packmeta.KINDS` + the updater
  first, then publish.
- Configurator deploy is separate (`npx wrangler deploy` from `configurator/`);
  see configurator/CLAUDE.md.

## Current state (2026-07-18)

- Branch model (since 2026-07-08): **`dev`** (active development — work here)
  → `qa` → `main`, promotion by merge; each push redeploys that environment
  (Worker + D1 migrations). Old feature branches (`feat/accounts-auth`, …) are
  parked history. Live thread state: `../STATE.md`.
- `packtools`/`pyefis_data` Phases A–F are complete and live end-to-end.
  Package version **0.2.11** (`pyproject.toml`; README agrees).
- `ssh pyefis` reaches the Pi 5 test bench; mid-flight patching is explicitly OK
  (it's a debug bench, not a certified article).

## Licensing (per-file SPDX is authoritative)

Multi-licensed by component — `docs/LICENSE-AUDIT.md` is the full audit:

- **Apache-2.0** — the data tools + device updater (`packtools/`,
  `pyefis_data/`, `site/`, `tests/`, `scripts/`). These are the interfaces third
  parties are meant to build against; Apache's patent grant + retaliation clause
  apply.
- **AGPL-3.0-or-later** — the hosted `configurator/` service.
- **GPL-3.0-or-later** — `configurator/public/editor.html` (ports GPL pyEfis
  widget preview code).
- **CC BY 4.0** — documentation.
- Packaged **data** carries its own terms (FAA = public domain, Copernicus =
  redistributable, OSM = ODbL attribution, NavCanada = user-supplied only); each
  pack embeds its `attribution` string.

Patent posture: the integrated architecture is disclosed as prior art in
`docs/AC-DP-001-architecture-disclosure.md` (intended to stay unencumbered).
Note `pyproject.toml` still declares `license = "MIT"` at the package level — the
per-file SPDX headers (Apache-2.0 on the code) are the authoritative statement.

## Conventions

- **No emojis** in code or commit messages.
- One focused commit per change; the user values commit-history granularity.
- Commit with `-s` (DCO sign-off) — CI enforces it on PRs.
- Every new source file needs an SPDX header (`scripts/spdx_check.py` gates it).
- `construct-never-raises` is a house rule on the Pi side: a missing/bad config
  or catalog yields defaults, never an exception — a typo can't brick the updater.
- Don't commit generated assets or built packs — packs live in R2; editor assets
  live in R2 sourced from pyEfis.
- **Do not push or open PRs to upstream `makerplane/*` without explicit
  authorisation** (standing instruction across all these repos).
