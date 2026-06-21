# makerplane-configurator

Accounts/auth + design-storage API for the pyEfis **configuration manager**, as a
Cloudflare Worker. This is the account tier from
[../docs/system_designer.md](../docs/system_designer.md): it adds **Workers + D1
(+ KV)** alongside makerplane-data's existing R2 hosting.

It does **not** render instruments (that's a pyEfis-CI step publishing static
assets to R2 — Workers can't run Qt) and it does not yet serve the editor UI;
it's the auth + persistence backbone the editor and the device-pull flow build on.

## What's here (scaffold)

- **Auth**
  - `GET  /auth/google/start` → Google OIDC (authorization-code + PKCE + state)
  - `GET  /auth/google/callback`
  - `POST /auth/email/request` `{ "email": "..." }` → passwordless magic link
  - `GET  /auth/email/verify?token=...`
  - `POST /auth/logout`
- **Session** — signed (HMAC) cookie, no server-side session store.
- **Protected API** (requires a session)
  - `GET  /api/me`
  - `GET  /api/projects`, `POST /api/projects` `{ "name": "..." }`
- **D1 schema** (`migrations/0001_init.sql`): `users, identities, projects,
  devices, configs` — the data model from the design doc.

Still stubbed / TODO: design (config) CRUD + R2 blob storage, device pairing
(claim code → device token) and the signed `config` pack endpoint the on-Pi
updater pulls, and the editor frontend.

## One-time setup

```bash
cd configurator
npm install

# 1) D1 database — copy the printed database_id into wrangler.jsonc
npx wrangler d1 create makerplane_configurator

# 2) KV namespace (OAuth state + magic-link tokens) — copy the id in
npx wrangler kv namespace create KV

# 3) Secrets for local dev
cp .dev.vars.example .dev.vars     # then fill SESSION_SECRET + GOOGLE_* (+ MAIL_*)

# 4) Apply the schema (local sqlite for dev, --remote for prod)
npm run migrate:local
```

**Google OAuth:** create an OAuth 2.0 Web client at
console.cloud.google.com → Credentials, with the authorized redirect URI
`<APP_URL>/auth/google/callback` (dev: `http://localhost:8787/auth/google/callback`).
Put the client id/secret in `.dev.vars`.

## Run / deploy

```bash
npm run dev                 # wrangler dev on http://localhost:8787
npm run typecheck           # tsc --noEmit
npm run deploy              # wrangler deploy (set prod secrets first:
                            #   wrangler secret put SESSION_SECRET  etc.)
npm run migrate             # apply migrations to the remote D1
```

For production set `APP_URL` (e.g. `https://config.aerocommons.org`) in
`wrangler.jsonc` and the secrets via `wrangler secret put`.

## Notes

- Magic-link email is logged to the console unless `MAIL_API_KEY` is set
  (Resend wired in `src/mail.ts`); swap providers there.
- The Google `id_token` is consumed straight from Google's token endpoint over
  TLS, so claims are trusted without local JWKS verification; add JWKS checking
  for defense-in-depth if you expose other flows.
- No runtime dependencies beyond Hono; all crypto uses the Workers Web Crypto API.
