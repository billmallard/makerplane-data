# Device deployment (#65) — design pass

How a panel designed at <https://pyefis.aerocommons.org> gets onto a specific
aircraft's pyEfis. Closes the loop: **editor → paired device → live screen.**

Status: **design** (nothing built yet). This doc proposes the model + a phased
plan and flags the decisions to confirm before coding. See
[system_designer.md](system_designer.md) for the broader vision and
[configurator/README.md](../configurator/README.md) for the app it extends.

## What already exists (reuse, don't reinvent)

- **`devices` table** already has the pairing columns: `claim_code`,
  `device_token_hash`, `claimed_at`, `last_pull_at` (`configurator/migrations/0001_init.sql`).
- **Designs** are stored per device as YAML in R2 (`configs/<user>/<device>/v<n>.yaml`)
  + a `configs` row, via authed `PUT|GET /api/devices/:id/config`.
- **On-Pi updater** `pyefis_data` (systemd timer): reads `~/.makerplane/pyefis/data.yaml`,
  fetches a signed manifest from `navdata.aerocommons.org`, verifies, atomic-swaps,
  writes `~/.makerplane/pyefis/status.json`. Has the download / verify / atomic-install
  primitives we want.
- **Signing**: `packtools/signing.py` + minisign keys in `keys/` (the navdata trust chain).
- **pyEfis config**: read from `~/makerplane/pyefis/config`; `create_config_dir()`
  seeds only **missing** files (so an update must *overwrite*, not rely on seeding).

The navdata channel is **public** (global manifest, region packs). A device config
is **private + per-device**, so it gets its **own authenticated channel** that
reuses the verify + atomic-swap code — it does **not** go in the public manifest.

## The four pieces

### 1. Pairing (claim code → device token)
1. In the dashboard, "Pair device" → Worker sets a short, single-use,
   TTL'd `claim_code` (e.g. 8 chars, 15 min) on the device row; shows it to the user.
2. On the Pi: `pyefis-data pair <code>` (or the on-device pack-picker GUI) →
   `POST /api/pair { claim_code }` (public, rate-limited). Worker validates
   (exists, unclaimed, unexpired), mints a long random **device token**, stores
   `sha256(token)` in `device_token_hash`, sets `claimed_at`, clears `claim_code`,
   returns the token **once**.
3. Pi stores the token (in `data.yaml` or a 0600 sidecar). Token is revocable
   (null the hash) and re-pairable.

Token auth thereafter: `Authorization: Bearer <token>` over TLS; Worker looks up
by `sha256(token)`.

### 2. Compile (design → pyEfis screen YAML)
Pure YAML transform → **runs in the Worker (TypeScript), no Qt needed**. Input:
the stored design (`{screen{w,h,inches}, layout{rows:110,columns:200}, instruments[]}`)
+ the device row. Output: a small pyEfis config overlay:

- `screens/<gen>.yaml` — a `module: pyefis.screens.screenbuilder` screen with the
  design's `layout` and a **concrete `instruments:` list** (each `{type,row,column,span,options}`).
  - **Element groups** (`type:"group"`) → expanded to their child instruments at
    absolute grid positions (same math as the editor's Ungroup).
  - **`virtual_vfr`** → inject the device-side `svs:` block (enabled + data paths
    under `/data/makerplane-data/...`); the editor's `preview_scene` is dropped
    (editor-only).
  - `preview_scene` and any other editor-only keys are stripped.
- a `main` override — `screenWidth`/`screenHeight` from the device (or design
  target) and `defaultScreen: <gen>`.

Gauge limits / V-speeds / bands are **not** here — they come from the FIX
database / aircraft config (issue #64, fix-gateway side), kept separate.

**Direction (per Bill): shrink/remove the compile step.** The gap between the
stored design and a pyEfis screen is thin (the `instruments` list is nearly
identical). The cleaner end state is for the **editor to emit native pyEfis
screenbuilder YAML directly** — groups pre-expanded to concrete instruments,
screen size carried as a small `main` snippet — so the device just drops the
files in and there is **no separate compile**. The only "transform" then is
trivial JS at editor save-time (or a tiny screenbuilder convention on the pyEfis
side that reads the editor's output as-is). It also makes the editor's live code
pane show real pyEfis config. For now the design YAML stays the stored source of
truth and the Worker does the thin reshape; migrating the editor to emit native
YAML is the path to deleting "compile" entirely (a pyEfis-side task).

### 3. Pack + serve
- `GET /api/device/config` (Bearer device token) → a per-device manifest
  `{ version, sha256, url|inline, generated }`, or **304** if the device's
  `If-None-Match`/version is current. Updates `last_pull_at`.
- The bundle = the compiled overlay (a few small YAML files) as a tar.gz.
  Compile + pack on demand in the Worker, cache by version in R2
  (`configs/<user>/<device>/compiled-v<n>.tgz`).
- **Integrity:** sha256 in the manifest for corruption + change detection only;
  transport is TLS; the token authorises. **No signing** (see decisions).

### 4. On-Pi pull + install
Extend `pyefis_data` with a config capability (one updater, reuse its plumbing):
1. If a `device_token` is configured, after the navdata pass call
   `GET <configurator>/api/device/config` with the token.
2. If `version` > installed, download the tgz, verify sha256 (+ sig if signed).
3. **Atomic-swap** the overlay into `~/makerplane/pyefis/config` (write to a temp
   tree, back up the previous, `os.replace`), then restart the `pyefis` user service.
4. Record installed version + `last_pull_at`; surface in `status.json` for the
   Update screen.

A paired device's generated screen is **Worker-managed** — local edits to those
files are overwritten on pull (the editor is the source of truth). Non-generated
files (includes, preferences) are left untouched by the overlay.

## Endpoints summary
| Method | Path | Auth | Purpose | Status |
|---|---|---|---|---|
| POST | `/api/devices/:id/pair` | session | mint a claim code (KV, 15-min TTL) | **built (P1)** |
| POST | `/device/pair` | claim code | redeem code → device token | **built (P1)** |
| GET | `/device/config` | device token | latest panel config YAML; ETag/304; stamps `last_pull_at` | **built (P2)** |

The config is served **inline as YAML** (the artifact is a small native pyEfis
fragment — [panel_config_format.md](panel_config_format.md)); there's no separate
"pack" endpoint or compile step.

## Decisions (confirmed 2026-06-22)
1. **No per-device signing.** Unlike the public navdata packs, a device config has
   no authenticity chain worth protecting — it loads and works or it doesn't, and
   the pull is already token-authorised over TLS. Keep a `sha256` for integrity /
   corruption / change detection only; no `sig` field.
2. **Extend `pyefis_data`** (don't add a sibling) — it already owns the device,
   the timer, and atomic install; add `device_token` + `configurator_url` to
   `data.yaml`.
3. **Overlay bundle**, not full tree — ship just the files that represent the
   design (generated screen + a `main` snippet) and drop them into the device's
   existing config dir, leaving its other files alone. (Must *overwrite*, since
   `create_config_dir` only seeds missing files.)
4. **Compile in the Worker for now; design YAML stays the source of truth** —
   reshaped on pull. But the **target is to eliminate compile** by having the
   editor emit native pyEfis screenbuilder YAML (see Direction in §2); pyEfis
   reading the editor's raw output is the goal, not an offline build step.

## Phased build plan
- **P1 — Pairing (DONE):** `/api/devices/:id/pair` + `/device/pair`; dashboard
  "Pair device" UI; `pyefis-data pair` subcommand storing the token. Validated on
  the Pi 5.
- **P2 — Emit + serve (DONE):** the editor writes **native pyEfis screenbuilder
  YAML** (groups expanded, `main` snippet) so the stored file *is* the device file
  — [panel_config_format.md](panel_config_format.md). Served by `GET /device/config`
  (device token, ETag/304). No compile step.
- **P3 — On-Pi install (DONE):** `pyefis-data config-pull` fetches `/device/config`
  (If-None-Match → 304), writes the native config as a **managed screen** named
  after the device's existing `defaultScreen`, and activates it by **overriding
  only that screen's include** (`SCREEN_<defaultScreen>` → `screens/managed.yaml`)
  in `preferences.yaml.custom` — **keeping the device's stock screen set**. (An
  earlier version replaced `SCREENS_CONFIG` with a 2-entry list; a *short* screen
  list + the SVS GL widget **segfaults the eglfs `QOpenGLCompositor`** — #71. The
  full set is fine.) Stock files untouched; a `.prepanel` backup is kept for
  rollback. A `virtual_vfr` gets the stock `screens/virtualvfr_db.yaml` include
  (screen-level `dbpath`) **and** an `svs` block (terrain). Validated on the Pi 5.
- **P4 — Polish (mostly DONE):**
  - **Proper release (DONE):** `makerplane-data` **v0.2.2** wheel built + installed
    on the Pi, replacing the hot-patch (`pyefis-data pair` + `config-pull` ship in it).
  - **Auto-rollback (DONE):** `config-pull` runs `restart_and_verify()` after a
    swap (PID-stability check; pyEfis is Type=simple/Restart=always so a crash
    shows as a changed Main PID) and rolls back to the last working panel
    (`managed.yaml.bak`, else the pristine `.prepanel` override). **Battle-tested
    in production** — it caught the SVS-block crash below and kept the device up.
  - **Auto-pull (DONE):** `pyefis-config-pull.service` (user, oneshot) pulls on
    **boot** after pyEfis is up — deliberately **not** a periodic timer (no
    in-flight EFIS restarts). On-demand updates use `pyefis-data config-pull`.
  - **Dashboard indicator (DONE):** per device "paired · pulled <rel> · update
    pending" from `last_pull_at` + the latest config's `created_at`.
  - **SVS terrain (DONE, #71):** the installer injects the `svs` block (terrain/
    airport/water paths) into `virtual_vfr`, and the editor's panel renders real
    synthetic-vision terrain on the Pi 5. Two fixes got there: pyEfis's AI
    **redraw-before-resize guard** (`gpu-required`, PR #274) and the **install
    keeping the full screen set** (above) — a short screen list was segfaulting the
    eglfs GL compositor, not the overlapping instruments.
  - No per-device signing (decision 1).

- **P5 — Multi-screen panels + switching (DONE, #72):** the editor lets a builder
  draw **several screens** (e.g. `PANEL` + `ROUND_DIALS`); `config-pull` now
  deploys **all** of them, not just the default, and gives a touchscreen-only
  panel a way to move between them. `makerplane-data` **v0.2.10**.
  - **The SVS-GL short-list segfault is REAL** (the hard constraint). A short
    `SCREENS_CONFIG` + the SVS GL widget segfaults the eglfs `QOpenGLCompositor`
    — an eglfs GL bug that **correlates with screen count, independent of pyEfis
    #274**. (An early attempt to use a tidy editor-only list looked stable only
    because the verifier's PID check was fooled — see below; with valid buttons it
    segfaulted past SVS GL init.) So multi-screen **keeps the device's full stock
    screen list loaded** as GL-safe ballast and weaves the editor screens into it.
  - **Install shape** (`pyefis_data/config_pull.py`), chosen by screen count:
    - *Single screen* — the proven P3 path: one `screens/managed.yaml` named after
      the device's `defaultScreen`, overriding only that include, stock list kept.
    - *Multiple screens* — keep the full stock list and:
      - **repurpose the default slot:** override `SCREEN_<defaultScreen>` to the
        default editor screen (that slot is the device's boot choice, not a
        nav-button target, so nothing breaks; boot needs no `main/` edit);
      - **append the rest:** each additional editor screen becomes a new
        `SCREEN_M_<name>` token added onto a copy of the stock list
        (`screens/managed_list.yaml`), leaving every stock screen intact. The list
        only ever **grows** (≥ stock length), so it stays GL-safe.
    - Screens are keyed by their file's **top-level name** (`gui.initialize`), so
      the editor's own names survive for `show screen` navigation even though they
      ride on stock include tokens.
  - **Switching:** the encoder/key screen-change bindings aren't assumed on a
    managed panel, so the installer **injects a small "SCREEN >" button**
    (bottom-centre, clear of the edge tapes) onto each editor screen. It fires an
    **explicit `show screen: <next editor screen>`** (not `show next screen`), so
    it cycles only the editor's screens and never lands on the stock ballast. Each
    button is a per-screen `buttons/managed-next-<name>.yaml` with a **distinct,
    registered `TSBTN{id}<s>` slot** (`s` 1‑40 per fixgw `database/variables.yaml`;
    we take the top of the range, clear of the stock buttons at ≤28). A dbkey
    naming an *unregistered* TSBTN slot is a fatal `KeyError` at screen-build time
    — the button does `fix.db.get_item()` without `create=True`.
  - **Verifier hardened (this is what caught the bugs):** `restart_and_verify`
    used to check only Main-PID stability. But a **screen-build exception doesn't
    exit pyEfis** — a non-daemon FIX thread keeps the process (and PID) alive while
    the GUI never shows, so PID-stability was a **false positive**. It now also
    rejects a `Traceback` / `Fatal Python error` / `Segmentation fault` /
    `Unable to load module` logged by the current PID (read via `systemctl
    status`, scoped to the PID). A segfault additionally changes the PID (the
    process dies + respawns), which the stability check catches.
  - **Rollback hardened:** before every install the panel state (the override +
    all `managed*.yaml` + the switch buttons) is snapshotted to **`.panel_backup/`**;
    a config that fails verification restores the whole snapshot (the *previous
    working* panel), falling back to the pristine `.prepanel` override. Also bumped
    `restart_pyefis`'s timeout **40 → 120 s**: an SVS/GL pyEfis can hang on SIGTERM
    up to its 90 s stop-timeout before systemd SIGKILLs it, and the old 40 s window
    could misread that as a failed restart and trigger a spurious rollback.
    **Battle-tested in this build** — it caught both the unregistered-dbkey
    `KeyError` and the short-list segfault and kept the device on its working panel.
  - Unit-tested (`tests/test_config_pull.py`, no Qt) and validated end-to-end on
    the Pi 5: `config-pull` installed a 2-screen panel (`PFD_AI_ONLY` + `ROUND_DIALS`,
    SVS on the first) onto the full stock list (8 screens), restarted, came up
    clean (SVS GL initialised, no crash signatures), with cross-linked `SCREEN >`
    switch buttons (`TSBTN{id}40`/`39`).
