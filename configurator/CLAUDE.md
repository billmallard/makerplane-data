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
  - `buildVirtualVfr` → **a `<canvas>` rendered by `renderSVS()` from a real
    terrain data patch** (see "The SVS data-driven preview" below). The
    captured `svs/<scene>.webp` is only the loading/error fallback.
    `preview_scene` picks the scene (mountains / approach / coastal / final).
  - everything else → the static palette SVG from R2.
  - `setOpt()` re-renders so option edits update live.
- Complex SVG built via the `svgFromString(...)` template helper; `_polar` for
  dial geometry.
- Other features in the same file: layers/z-order, screen-size + DPI targeting,
  ghosted grid + snap, composition guides + smart alignment, element groups
  (drag/scale/ungroup), and the **live two-way YAML code pane** (`syncCode` /
  debounced `applyCode`; canvas↔YAML, focus-guarded so it never clobbers typing).

`public/index.html` is the dashboard (auth → projects → devices → `/editor?device=N`).

## The SVS data-driven preview (shipped 2026-07-02 — session handoff notes)

The virtual_vfr twin renders **real terrain live** instead of a static image.
Everything an option changes re-renders instantly. Architecture:

```
  pyEfis tools/export_svs_preview_patch.py        (SCENE POSES = source of truth)
    SRTM3 (D:/EarthData/srtm3) + NASR (pyEfis/nasr/airports.sqlite)
      -> work/svs_patches/<scene>.json   (two-tier elev grid + runways + meta)
      -> R2 assets/editor/svs/<scene>.json  (+ <scene>.webp fallback captures)
  editor.html renderSVS(canvas, patch, options)   (the JS twin of svs_gl.py)
    called from buildVirtualVfr; _svsPatchFor() caches the fetch per scene,
    calls renderCanvas() once when a patch lands (webp img shows until then).
```

Scenes: `coastal` (SBA 34.4275/-119.8546, 2500 ft, hdg 000), `mountains`
(Aspen 39.20/-106.85, 12500 ft, hdg 150), `approach` (Telluride ~3 NM final
RWY 9), `final` (**1 NM final RWY 7 KSBA at 600 ft AGL** — user-requested).

**Fidelity: the renderer is a PORT of svs.py/svs_gl.py, not an imitation.**
If you touch it, keep these tied to the pyEfis source (all verified against
the GL shader in `src/pyefis/instruments/ai/svs_gl.py`):
- Palettes + band logic: `_PALETTES`/`_clearance_color` (svs.py) and the
  fragment shader (conflict < 0 clearance; airport-proximity collapse is
  **elevation-gated**: `near_airport && elev_ft < field_elev + 400` → SAFE).
- Safe gradient: `mix(SAFE_LOW(128,117,56), SAFE, clearance-green/2500)`.
- Haze: `1 - exp(-d/haze_eff)` toward HAZE (166,196,230), where `haze_eff =
  min(max(haze_nm, 0.9*1.22*sqrt(alt_ft)), 200)` NM and `d` is **3D slant**
  distance (GL fogs by v_dist). Runway surface fogs at 0.4 strength.
- Projection: GL is `ppd * DEG_PER_RAD * tan(angle)` (camera.py); the twin's
  pinhole `f = H/(2*tan(fov/2))`, `fov = pitchDegreesShown` (default 30),
  matches within 2.3%. Proven by PIL blend-diff: the KSBA runways align.
- **No depth test on the device** — painter's order far→near, runways drawn
  after terrain. Keep it that way; it's faithful, not a shortcut.
- Water: patch cells are `-9999` sentinel where the EXPORTER flood-filled
  elev<=0 from the patch border (ocean). The GL itself only paints water for
  the heightmap void sentinel (`WATER_THR_M = -1000`) — on-device blue water
  comes from the water-polygon DB layer, which the preview approximates.

**To regenerate assets** (after changing scenes, poses, or the exporter):
```bash
cd pyEfis && PYTHONPATH="C:/pylib;src" python tools/export_svs_preview_patch.py --out work/svs_patches
PYTHONPATH="C:/pylib;src" python -m pyefis.editor.schema > work/svs_patches/schema.json
cd ../makerplane-data/configurator   # NO CLOUDFLARE_API_TOKEN (R2-scoped token can't deploy)
npx wrangler r2 object put makerplane-configs/assets/editor/svs/<scene>.json --file ... --content-type application/json --remote
npx wrangler deploy
```

**Pi reference captures** (fallback webps + fidelity A/B frames) are taken at
the exporter's poses: stop pyefis (`systemctl --user stop pyefis`), then
`QT_QPA_PLATFORM=eglfs QT_QPA_EGLFS_KMS_CONFIG=~/eglfs_hdmi.json
SVS_RENDERER=opengl SVS_TERRAIN_ONLY=1 SVS_LAT/LON/ALT/HEAD=...
SVS_RANGE=15 SVS_AUTO_RANGE=false SVS_TILE_PATH=$HOME/EarthData/srtm3
SVS_SCREENSHOT=/tmp/x.png SVS_SCREENGRAB=1 SVS_SCREENSHOT_DELAY_MS=7000
timeout 60 ~/pyEfis/.venv/bin/python tests/visual_svs_test.py`, restart
pyefis. Output is 1920x1080 regardless of SVS_W/H (eglfs fullscreens).

**Validation workflow that worked:** extract the editor functions by brace
matching into a standalone proto page (inline the patch JSON — file:// fetch
is blocked), screenshot via the browse skill, PIL blend/diff against the Pi
capture. Structural alignment (runways, ridgelines) is the pass signal;
tonal deltas get tuned against the constants above.

**Known gaps (good next increments):** no airport flag poles / obstacle
markers in the canvas (device draws them; data could ride in the patch);
coastal zero-elev flats (Goleta Slough) render water-blue where the device
shows land — proper fix is shipping water-DB polygons in the patch;
`font_percent` doesn't drive twin label sizes yet; garmin chevron geometry
redo pending (pyEfis #85).

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
