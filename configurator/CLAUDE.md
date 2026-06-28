# CLAUDE.md

Orientation for Claude sessions working on the **pyEfis configuration manager**
(the website at <https://pyefis.aerocommons.org>). Read this first; see
[README.md](README.md) for the human-facing overview and
[../docs/system_designer.md](../docs/system_designer.md) for the product vision.

## What this is

A single **Cloudflare Worker** (Hono) that serves a web app where builders sign
in, lay out their pyEfis EFIS panels visually, store the designs, and (next
phase) push them to their aircraft. One origin serves both the **editor UI**
(static `public/`) and the **accounts + design-storage API** (`/auth`, `/api`),
so the session cookie applies with no CORS.

## Where it lives + the two-repo split

- **This code:** `makerplane-data/configurator/`, branch **`feat/accounts-auth`**.
- **Instrument rendering + editor assets:** the **`makerplane/pyEfis`** repo
  (branch `display-changes`, off `gpu-required`). Workers can't run Qt, so pyEfis
  generates `schema.json`, palette SVGs, `groups.json`, and SVS images and they're
  uploaded to R2. **If you change instrument options or add an instrument, you
  edit pyEfis (`src/pyefis/editor/schema.py`), regenerate, and re-upload to R2** —
  not this repo. See [README → Editor assets](README.md#editor-assets-generated-by-pyefis).

Keep changes on the feature branches; do **not** push to upstream `makerplane/*`
without explicit authorisation (standing instruction across these projects).

## Deploying (READ THIS)

```bash
cd configurator
npx wrangler deploy          # builds + deploys; provisions the custom domain
```

- Deploy uses the **wrangler OAuth login** (`wrangler login`) already done on this
  machine. **Do NOT set `CLOUDFLARE_API_TOKEN`** — the token in
  `CloudFlare R2 Bucket Keys.txt` (gitignored) is **R2-scoped only** and cannot
  deploy Workers/D1/KV.
- **Edge cache:** `wrangler deploy` returns immediately, but the edge can serve
  the *previous* `public/` file for several seconds. When verifying with `curl`,
  re-fetch a few times until you see your change before concluding it failed —
  this has bitten every editor deploy. Pattern that works:
  ```bash
  for i in 1 2 3 4 5; do curl -s URL -o /tmp/e.html; grep -q MARKER /tmp/e.html && break; done
  ```
- Secrets are already set in prod (`SESSION_SECRET`, `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`). `APP_URL` is a non-secret `var` in `wrangler.jsonc`.
- DB migrations: `npm run migrate` (remote D1) / `npm run migrate:local`.

## Architecture map

- **`src/index.ts`** — the whole router. Public: `/healthz`, `/assets/*` (R2),
  catch-all → `ASSETS` static. Auth: `/auth/google/*`, `/auth/email/*`,
  `/auth/logout`. Protected (`requireUser`): `/api/me`, `/api/projects[/:id]`,
  `/api/projects/:id/devices`, `/api/devices/:id[/config]`. Every `/api` query is
  **ownership-scoped through `user_id`**.
- **`src/db.ts`** — all D1 SQL. **`src/session.ts`** — stateless signed cookie
  (`sid` = `b64url(JSON{uid,exp}).hmac`, 30 d) + `requireUser`.
  **`src/google.ts`**, **`src/email.ts`/`src/mail.ts`**, **`src/crypto.ts`**
  (Web Crypto only). **`src/types.ts`** — `Bindings`.
- **`wrangler.jsonc`** — bindings (`DB` D1, `KV`, `CONFIGS` R2 `makerplane-configs`,
  `ASSETS` static `public/` with `run_worker_first`), the `pyefis.aerocommons.org`
  custom domain, `APP_URL`.
- **D1 schema** (`migrations/0001_init.sql`): `users, identities, projects,
  devices, configs`. The `devices` table already has `claim_code`,
  `device_token_hash`, `claimed_at`, `last_pull_at` for the upcoming pairing flow.
- **R2** (`makerplane-configs`): `configs/<user>/<device>/v<n>.yaml` (private, via
  authed `/api`) and `assets/editor/…` (public, via `/assets/*`).

## The editor — `public/editor.html` (the big file, ~1200 lines)

Vanilla JS, no build step. Key internals:

- `const ASSETS = "/assets/editor"`. `boot()` loads `schema.json` + `groups.json`,
  checks `/api/me`, then loads the device's saved design via
  `GET /api/devices/:id/config`. **Save** = serialize the design to YAML (js-yaml)
  → `PUT /api/devices/:id/config { yaml }`.
- **`state`** holds `schema, layout {rows:110, columns:200}, screen {w,h,inches},
  instruments[], selected, groups, …`. The grid is pyEfis's normalised
  110×200; `rect()` maps grid→pixels (a JS port of `screenbuilder_layout`).
- **Instrument rendering** lives in `renderCanvas()`, which branches per type and
  calls a `build*(inst)` function returning a DOM node:
  - text: `LIVE_TEXT` set → HTML in the chosen web font.
  - gauges: `LIVE_GAUGE` → `buildArcGauge` / `buildBarGauge` (HTML/CSS + SVG).
  - `buildAttitude`, `buildHSI`, `buildTape` (airspeed/altimeter), `buildHeadingTape`,
    the dials (`buildAirspeedDial`/`buildAltimeterDial`/`buildVsiDial`/
    `buildTurnCoordinator`), `buildTrendTape`, `buildVsiPfd`, `buildWind`.
  - `buildVirtualVfr` → **an `<img object-fit:cover>`** of `svs/<scene>.webp`
    (real captured SVS frame; NOT redrawn in SVG). `preview_scene` picks the master.
  - everything else → the static palette SVG from R2.
  - `setOpt()` re-renders so option edits update live.
- Complex SVG built via the `svgFromString(...)` template helper; `_polar` for
  dial geometry.
- Other features in the same file: layers/z-order, screen-size + DPI targeting,
  ghosted grid + snap, composition guides + smart alignment, element groups
  (drag/scale/ungroup), and the **live two-way YAML code pane** (`syncCode` /
  debounced `applyCode`; canvas↔YAML, focus-guarded so it never clobbers typing).

`public/index.html` is the dashboard (auth → projects → devices → `/editor?device=N`).

## Instrument-fidelity rule (important)

**HARD RULE: the live previews must reproduce what the device (pyEfis) actually
renders, as closely as the technology reasonably allows. No freelancing on
instrument appearance in the configurator.** This is a *fidelity* requirement, not
a style — the target look is whatever the pyEfis widget draws, whatever that look
is. pyEfis is the single source of truth; the twin tracks it and never invents.
**When building or changing an instrument twin: render the real pyEfis widget
first, then match it.** (The pyEfis visuals are themselves a work in progress
toward a category-leading catalog — when a widget's look improves, its twin
follows.)
- Reference renders: `python tools/render_instrument.py <type> --safe -o out.png`
  (in pyEfis; QPainter widgets render offscreen, GL/SVS do not).
- SVS/Virtual VFR: GL can't render in a browser or headless. Capture real frames
  with `tests/visual_svs_test.py` (`SVS_SCREENSHOT` + `SVS_SCREENGRAB`, polar
  renderer, `SVS_W/SVS_H` for size) on a machine with a GPU + local terrain data;
  the look is **hypsometric** (green below you → yellow/red near your altitude →
  magenta/purple above → blue sky, with a wireframe mesh + airport flags).
- Growing the catalog (more instrument types, variations, and visual quality) is
  an active goal; alternative/modern instrument styling is tracked as pyEfis issue
  **#69**. Every new type/variation is still bound by the fidelity rule above.

## Status / next

Done: auth, projects/devices/configs CRUD, and the full editor with live
device-faithful previews for every instrument. **Next = device deployment (#65):**
claim-code pairing → scoped device token → compile the design to a pyEfis screen
YAML → signed `config` pack → device-pull endpoint the on-Pi `pyefis-data`
updater fetches and atomic-swaps into `~/makerplane/pyefis/config`.

## Conventions

- No emojis in code or commit messages.
- Prefer a focused commit per change; the user values commit-history granularity.
- Two repos move together for editor work: pyEfis (`display-changes`, schema/
  assets) and makerplane-data (`feat/accounts-auth`, this Worker). Note both SHAs
  when relevant.
- Don't commit generated assets here — they live in R2, sourced from pyEfis.
