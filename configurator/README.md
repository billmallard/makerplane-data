# makerplane-configurator

The **pyEfis configuration manager** — a web app where builders sign in, lay out
their EFIS panels visually, and (soon) push the result to their aircraft. It is a
single **Cloudflare Worker** that serves the editor UI **and** the accounts /
design-storage API from one origin.

- **Live:** <https://pyefis.aerocommons.org>
- **Repo / path:** `makerplane-data/configurator/` (branch `feat/accounts-auth`)
- This is the "account tier" of the System Designer vision in
  [../docs/system_designer.md](../docs/system_designer.md): it adds **Workers + D1
  + KV** on top of makerplane-data's existing R2 hosting.

It does **not** render instruments itself (Workers can't run Qt). Instrument
schema, palette thumbnails and the synthetic-vision images are generated on the
**pyEfis** side and published to R2; the browser editor consumes them. See
[Editor assets](#editor-assets-generated-by-pyefis) below.

---

## Architecture at a glance

```
                         pyefis.aerocommons.org  (one Cloudflare Worker)
  Browser ───────────────────────────────────────────────────────────────────
   public/index.html   ── /auth/* , /api/*  ─────────►  Hono router (src/index.ts)
   public/editor.html  ── /assets/editor/*  ──┐                │
                                              │                ├─ D1  (users, projects,
                                              │                │      devices, configs)
                                              │                ├─ KV  (OAuth state,
                                              │                │      magic-link tokens)
                                              │                └─ R2  makerplane-configs
                                              │                       ├ configs/   (private, per user)
                                              └──────────────────────►└ assets/    (public, shared)
                                                                              ▲
   pyEfis CI / tools  ─── wrangler r2 object put ────────────────────────────┘
   (schema.json, palette/*.svg, groups.json, svs/*.webp)

   (future #65)  device on aircraft ── device token ──► signed config pack pull
```

Everything is same-origin, so the session cookie "just works" — no CORS.

---

## Repo layout

| Path | Role |
|------|------|
| `src/index.ts` | Hono app — **all routes** (auth, api, assets, static catch-all). Entry point. |
| `src/db.ts` | D1 queries (users, projects, devices, configs). |
| `src/session.ts` | Stateless **signed-cookie** sessions + `requireUser` middleware. |
| `src/google.ts` | Google OIDC (auth-code + PKCE + state via KV). |
| `src/email.ts` / `src/mail.ts` | Passwordless **magic-link** request/verify; email send (Resend or console). |
| `src/crypto.ts` | HMAC + base64url helpers over the Workers Web Crypto API. |
| `src/types.ts` | `Bindings` (DB/KV/CONFIGS/ASSETS/secrets) + `Variables`. |
| `public/index.html` | **Dashboard** — auth state, projects, devices, "Edit panel" links. |
| `public/editor.html` | **The visual panel editor** (the big one). Canvas, palette, properties, element groups, live YAML code pane, and all instrument drawing. |
| `migrations/0001_init.sql` | D1 schema. |
| `wrangler.jsonc` | Bindings, `vars` (`APP_URL`), the `pyefis.aerocommons.org` custom domain, and the `public/` static-asset binding. |
| `package.json` | Scripts (`dev`, `deploy`, `typecheck`, `migrate`). Only runtime dep: Hono. |

---

## Routes (`src/index.ts`)

**Public**
- `GET /healthz` → `{ service, ok }`
- `GET /assets/*` → public editor assets from R2 (`assets/…`), `cache-control: max-age=300`.
- `GET *` (catch-all) → static files from `public/` via the `ASSETS` binding (so `/`, `/editor`, etc.).

**Auth**
- `GET  /auth/google/start` → Google OIDC (authorization-code + PKCE + state in KV)
- `GET  /auth/google/callback`
- `POST /auth/email/request` `{ email }` → magic link
- `GET  /auth/email/verify?token=…`
- `POST /auth/logout`

**Session-protected** (`app.use("/api/*", requireUser)` → 401 if no valid cookie)
- `GET  /api/me`
- `GET  /api/projects` · `POST /api/projects` `{ name }`
- `GET  /api/projects/:id` (project + its devices) · `DELETE /api/projects/:id`
- `POST /api/projects/:id/devices` `{ name, kind?, width?, height? }`
- `DELETE /api/devices/:id`
- `PUT  /api/devices/:id/config` `{ yaml }` → writes a new version to R2 + a `configs` row
- `GET  /api/devices/:id/config` → latest config YAML (from R2)

All `/api` routes are **ownership-scoped**: queries join through `user_id`, so one
user can never read or write another's projects/devices/configs.

---

## Data model (D1 — `migrations/0001_init.sql`)

- **users** — `id, email (unique), name, created_at, last_login`
- **identities** — one row per linked sign-in (`provider` = `google`|`email`,
  `provider_subject` = google `sub` or the email). Lets one account link several
  providers.
- **projects** — an aircraft / system design owned by a user.
- **devices** — a device in a project (`kind` default `pyefis`, `name`, `width`,
  `height`). Pairing fields are already present for #65: `claim_code`,
  `device_token_hash`, `claimed_at`, `last_pull_at`.
- **configs** — versioned screen layouts; `yaml_r2_key` points at the R2 blob.

Design blobs themselves live in **R2**, not D1.

## Sessions & auth

Stateless: the `sid` cookie is `base64url(JSON {uid, exp}) . HMAC` (key =
`SESSION_SECRET`), `httpOnly; secure; SameSite=Lax`, 30-day TTL — no server-side
session store (`src/session.ts`). Google's `id_token` is consumed straight from
Google's token endpoint over TLS, so claims are trusted without local JWKS
verification (add JWKS checks if you expose other flows).

## R2 layout (`makerplane-configs`, binding `CONFIGS`)

- `configs/<userId>/<deviceId>/v<n>.yaml` — **private** per-user design blobs;
  only reachable through the authed `/api/devices/:id/config` routes.
- `assets/editor/…` — **public** shared editor assets, served by `GET /assets/*`.

---

## Editor assets (generated by pyEfis)

The editor's data is **not in this repo** — it's produced by the pyEfis side and
uploaded to R2 under `assets/editor/`:

| Asset | Generated by (in `makerplane/pyEfis`) | R2 key |
|-------|----------------------------------------|--------|
| `schema.json` (instrument types + per-type options) | `pyefis.editor.schema` via `tools/build_editor_assets.py` | `assets/editor/schema.json` |
| `palette/<type>.svg` (palette thumbnails) | `tools/render_instrument.py --svg` (real QPainter) | `assets/editor/palette/<type>.svg` |
| `groups.json` (element groups, e.g. engine clusters) | `pyefis.editor.groups` | `assets/editor/groups.json` |
| `svs/<scene>.webp` (synthetic-vision backdrops) | captured via `tests/visual_svs_test.py` (polar renderer) | `assets/editor/svs/<scene>.webp` |

Regenerate + publish, e.g.:

```bash
# in makerplane/pyEfis  (needs PYTHONPATH=C:/pylib;src on the dev box)
python tools/build_editor_assets.py --out work/editor_assets
# then push each file to R2 (run from configurator/, which has wrangler auth):
npx wrangler r2 object put makerplane-configs/assets/editor/schema.json \
    --file=../../pyEfis/work/editor_assets/schema.json \
    --content-type application/json --remote
# (likewise for groups.json and palette/*.svg)

# SVS backdrop, e.g. the "mountains" scene (KASE on final):
SVS_RENDERER=polar SVS_LAT=39.247 SVS_LON=-106.885 SVS_ALT=9300 SVS_HEAD=145 \
SVS_PITCH=-5 SVS_RANGE=18 SVS_W=1600 SVS_H=1000 \
SVS_SCREENSHOT=work/refs/svs_mountains.png SVS_SCREENGRAB=1 \
PYTHONPATH="C:/pylib;src" python tests/visual_svs_test.py
# -> convert to webp, then:
npx wrangler r2 object put makerplane-configs/assets/editor/svs/mountains.webp \
    --file=... --content-type image/webp --remote
```

> **Why images for SVS:** the synthetic-vision widget is a `QOpenGLWidget`; GL
> can't render in a browser (or headless offscreen), so the editor uses real
> captured frames cropped to fit (`object-fit: cover`) instead of redrawing
> terrain in SVG. See the pyEfis-side notes in memory for the capture recipe.

The browser editor fetches `/assets/editor/schema.json` + `/assets/editor/groups.json`
at boot, palette SVGs per instrument, and `svs/<scene>.webp` for Virtual VFR; it
saves/loads a design as YAML through `PUT|GET /api/devices/:id/config`.

---

## One-time setup

```bash
cd configurator
npm install

npx wrangler d1 create makerplane_configurator   # copy database_id into wrangler.jsonc
npx wrangler kv namespace create KV              # copy id into wrangler.jsonc
# R2 bucket makerplane-configs already exists (shared with navdata assets)

cp .dev.vars.example .dev.vars   # fill SESSION_SECRET + GOOGLE_* (+ MAIL_*); set APP_URL=http://localhost:8787
npm run migrate:local            # apply schema to local sqlite
```

**Google OAuth:** create an OAuth 2.0 Web client (console.cloud.google.com →
Credentials) with redirect URI `<APP_URL>/auth/google/callback` (prod:
`https://pyefis.aerocommons.org/auth/google/callback`).

## Run / deploy

```bash
npm run dev        # wrangler dev on http://localhost:8787
npm run typecheck  # tsc --noEmit
npm run deploy     # wrangler deploy  (prod build + the pyefis.aerocommons.org domain)
npm run migrate    # apply migrations to REMOTE D1
```

Production secrets are set once via `wrangler secret put SESSION_SECRET` (and
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, optionally `MAIL_API_KEY` /
`MAIL_FROM`). `APP_URL` is a non-secret `var` in `wrangler.jsonc`.

> **Deploy auth:** this project deploys using the **wrangler OAuth login**
> (`wrangler login`) on the dev machine. Do **not** set `CLOUDFLARE_API_TOKEN` —
> the token in `CloudFlare R2 Bucket Keys.txt` is **R2-scoped only** and can't
> deploy Workers/D1/KV.

> **Edge cache after deploy:** `wrangler deploy` returns instantly but the edge
> can serve the previous `public/` asset for a few seconds. When verifying with
> `curl`, re-fetch a couple of times before trusting a "stale" result.

---

## Status

**Done**
- Google + magic-link auth, signed-cookie sessions.
- Projects / devices / versioned configs CRUD (D1 + R2), ownership-scoped.
- Dashboard (`index.html`) and the full visual editor (`editor.html`): drag/drop,
  schema-driven properties, z-order, screen-size targeting, guides + snap, element
  groups, a live two-way YAML code pane, clean cockpit web fonts, and **live,
  device-faithful previews for every instrument** (gauges, tapes, AI, HSI, dials,
  trend tapes, and image-based synthetic vision).

**Next — device deployment (#65)**
The data model is ready (`claim_code`, `device_token_hash`, `last_pull_at`). To
build: claim-code pairing → scoped device token → compile the saved design JSON
to a pyEfis **screen YAML**, bundle it as a **signed `config` pack**, and serve a
device-pull endpoint the on-Pi `pyefis-data` updater fetches and atomic-swaps
into `~/makerplane/pyefis/config`.

## Notes

- Only runtime dependency is **Hono**; all crypto is Web Crypto.
- Magic-link email logs to the console unless `MAIL_API_KEY` (Resend) is set.
- See [CLAUDE.md](CLAUDE.md) for an orientation aimed at AI coding sessions.
