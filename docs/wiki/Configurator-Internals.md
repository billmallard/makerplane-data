# Configurator Internals

This page is for contributors to the configurator and anyone running their own
copy. It describes how the service at
[pyefis.aerocommons.org](https://pyefis.aerocommons.org) is built. The code is in
[`configurator/`](https://github.com/billmallard/makerplane-data/tree/dev/configurator),
and [`configurator/README.md`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/README.md)
has the setup steps.

## One Worker

The whole service is one Cloudflare Worker. It serves the pages and the API from
one origin, so the sign-in cookie needs no cross-site handling. Routing uses
[Hono](https://hono.dev), its only runtime dependency, and all cryptography uses
the Workers Web Crypto API.

| Path | What it serves |
|---|---|
| `/` | The dashboard (`public/index.html`) |
| `/editor` | The Panel Editor (`public/editor.html`) |
| `/aircraft` | Aircraft parameters (`public/aircraft.html`) |
| `/assets/*` | Editor assets from object storage (below), cached for 5 minutes |
| `/auth/*` | Sign-in and sign-out |
| `/api/*` | The account API, for a signed-in user |
| `/device/*` | The device API, for a paired device |
| `/healthz` | `{"service": "makerplane-configurator", "ok": true}` |

## Storage

| Store | Holds |
|---|---|
| D1 (SQLite) | Users and their sign-in identities, projects, devices, panel versions, aircraft profile versions ([`configurator/migrations/`](https://github.com/billmallard/makerplane-data/tree/dev/configurator/migrations)) |
| KV | Short-lived state: Google sign-in state (10 minutes), email sign-in links (15 minutes), pairing codes (15 minutes) |
| R2 | Each saved panel, as `configs/<user>/<device>/v<n>.yaml`, and the editor assets under `assets/editor/` |

Every account query is scoped to the signed-in user: projects by owner, and
devices, panels and profiles through the project they belong to
([`configurator/src/db.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/db.ts)).
Device queries are keyed by the token's hash instead.

Panel versions and aircraft profile versions are append-only. A save adds a
version and never overwrites one. Deleting a device or project removes its
database rows; the saved panel files stay in storage.

## Sign-in and sessions

- **Google:** OpenID Connect with PKCE, scope `openid email profile`. The
  state is single-use and expires in 10 minutes. An email Google has not
  verified is refused.
- **Email link:** a 32-byte token, of which only the SHA-256 is stored. The
  link expires in 15 minutes and works once. The request always answers
  `{"ok": true}`, so it cannot be used to find out whether an account exists.
- **Session:** a cookie named `sid`, valid for 30 days, `HttpOnly`, `Secure`,
  `SameSite=Lax`. It holds the user id and expiry, signed with HMAC-SHA256. No
  session is stored on the server, so signing out deletes the cookie in that
  browser only.

([`configurator/src/google.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/google.ts),
[`email.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/email.ts),
[`session.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/session.ts))

## The API

All `/api/*` routes need a session and answer `401` without one.

| Route | Does |
|---|---|
| `GET /api/me` | The signed-in user |
| `GET`, `POST /api/projects` | List projects; create one (`{name}`) |
| `GET`, `DELETE /api/projects/:id` | A project and its devices; delete it |
| `POST /api/projects/:id/devices` | Add a device (`{name}`) |
| `DELETE /api/devices/:id` | Delete a device |
| `GET`, `PUT /api/devices/:id/config` | The newest saved panel; save a new version (`{yaml}`) |
| `GET /api/devices/:id/preferences` | The `preferences:` block the device would be sent |
| `POST /api/devices/:id/pair` | Start pairing: `{claim_code, expires_in}` |
| `GET`, `PUT /api/projects/:id/aircraft` | The active aircraft profile; save a new version (`{profile}`) |
| `GET /api/projects/:id/aircraft/versions` | Every profile version |
| `POST /api/projects/:id/aircraft/activate/:v` | Make an earlier version active |

The device API:

| Route | Auth | Does |
|---|---|---|
| `POST /device/pair` | A pairing code | Redeem it for a device token |
| `GET /device/config` | `Authorization: Bearer <token>` | The newest panel, with ETag and `304` support |

Both are described on [[Pairing and Config Delivery]]. A profile save is
validated on the server, and a profile that breaks a rule gets `422`; see
[[Aircraft Parameters]].

## Settings

The Worker reads these from its environment. Secrets are set in the deployment,
never in the repository.

| Name | What |
|---|---|
| `SESSION_SECRET` | Key for signing session cookies |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | The Google sign-in client |
| `APP_URL` | The site's own URL, for sign-in redirects and email links |
| `MAIL_API_KEY`, `MAIL_FROM` | Sending sign-in emails. Without a key, the link is only written to the Worker log |
| `ENV` | `dev`, `qa` or `prod` (prod when unset) |

The database, KV namespace, storage bucket and static assets are bound in
[`configurator/wrangler.jsonc`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/wrangler.jsonc).

## Environments

There are three deployments, each with its own database and KV namespace:

| Environment | Deployed from branch | Site |
|---|---|---|
| prod | `main` | [pyefis.aerocommons.org](https://pyefis.aerocommons.org) |
| qa | `qa` | its own hostname, in `wrangler.jsonc` |
| dev | `dev` | its own hostname, in `wrangler.jsonc` |

A push to one of those branches that touches `configurator/` deploys that
environment and applies its database migrations
([`.github/workflows/deploy-configurator.yml`](https://github.com/billmallard/makerplane-data/blob/dev/.github/workflows/deploy-configurator.yml)).
The three share one storage bucket and are kept apart by prefix: saved panels
under `configs-dev/` and `configs-qa/` outside prod, editor assets under
`assets/editor/dev/` and `assets/editor/qa/`. A device chooses its environment
by `configurator_url` in its `data.yaml`.

## Editor assets come from pyEfis

A Worker cannot run Qt, so it cannot draw pyEfis instruments itself. pyEfis
generates what the editor needs and publishes it under `assets/editor/`:

| Asset | What it is |
|---|---|
| `schema.json` | Every instrument type: its options, their types, defaults and help text, its category, and whether it can be drawn off-screen |
| `palette/<type>.svg` | The palette pictures |
| `groups.json` | The ready-made instrument groups |
| `fix_keys.json` | FIX key names, for the type-ahead on key fields |
| `svs/<scene>.*` | Synthetic-vision backdrops for the preview scenes |
| `aircraft_params_schema.json` | The aircraft parameter schema, generated by fix-gateway |

The rule for contributors: **to add or change an instrument or its options,
change pyEfis** (the widget and its option spec), regenerate the assets there,
and publish them. Do not edit the configurator's copy. The editor's preview of
an instrument is the one part kept in this repository, in `editor.html`, and it
follows what pyEfis draws.

## Tests

```bash
cd configurator
npm ci
npm test          # node --test test/*.test.mjs
npm run typecheck
```

CI runs `npm ci && npm test` as the `configurator-test` job. The tests run the
real functions out of `editor.html` (extracted, not copied) and cover the save
format, tab sections, option coercion, the aircraft profile rules, the device
preferences block, and the instrument previews against reference values taken
from pyEfis. They do not cover the routes or sign-in.

## Licensing

The service is AGPL-3.0-or-later: you may run or change it, but a changed copy
offered as a service must publish its source. `public/editor.html` is
GPL-3.0-or-later, because its instrument previews are ported from GPL pyEfis
widget code. Each file's SPDX header is the authority
([`docs/LICENSE-AUDIT.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/LICENSE-AUDIT.md)).
