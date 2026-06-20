# System Designer & Configuration Manager — architecture & roadmap

> Status: **design / decision record.** Near-term product (the End-User
> Configuration Manager) is in active build; see the ECM milestone and issues
> #57–#63 on `billmallard/pyEfis`. This doc records *where the system lives*,
> *how it runs on Cloudflare*, *how users and their designs are stored*, *how a
> design reaches the aircraft*, and *the path from "configure one pyEfis" to a
> full "system designer."*

## The one idea

A pilot signs in once, designs their panel (and, later, their whole avionics
system) in the browser, and the aircraft pulls its configuration over WiFi —
**the same way it already pulls navigation-data packs today.** The configuration
is just another **signed pack** delivered by the existing on-Pi updater, scoped
to a device the user has paired to their account.

## The decision: where this lives

The configuration manager is **a makerplane-data / Cloudflare product**, not a
separate thing and not housed inside pyEfis. The only part that stays in pyEfis
is the *build-time code generation*, because it needs the pyEfis widget set and
Qt. Concretely, three responsibilities, three homes:

| Responsibility | Home | Why |
|---|---|---|
| **Generate** the instrument schema + render the palette thumbnails | **pyEfis repo + CI** | Needs `screenbuilder_factory` registries + real Qt widgets. Output is static files. |
| **Host** the editor, the artifacts, user accounts, and stored designs | **makerplane-data + Cloudflare** | Same R2 static-hosting + (new) Workers/D1 the data system already uses. |
| **Deliver** a finished config to the aircraft | **makerplane-data pipeline** | A new `config` pack kind, signed into the manifest, pulled by the existing on-Pi updater. |

Earlier framing put the whole editor in pyEfis on a "dependency direction"
argument. That argument only ever applied to the **generator** (it must run
where pyEfis + Qt live). The *product* — UI, accounts, storage, delivery —
belongs with the data system, because a user's "aircraft" is one thing: their
navdata subscriptions, their screen configs, and (later) their whole system
design, under one account, one trust chain, one updater.

## Product stages

1. **Configuration Manager (now).** Visually design one pyEfis screen layout in
   the browser; save it to your account; pull it onto the device with a token.
   (ECM milestone, phases 0–4.)
2. **System Designer (north star).** Drag *multiple* pyEfis displays — and other
   hardware (FIX-Gateway nodes, CAN-FIX sensors, radios, autopilot servos, …) —
   onto a canvas, wire them together, and generate a **BOM**, a **panel
   layout**, and a **wiring / connection diagram**. The single-device editor is
   the first "device editor" inside this larger project canvas.

The data model below is deliberately shaped so stage 1 grows into stage 2
without a rewrite.

## Cloudflare architecture

makerplane-data today is **R2 object storage** served at
`navdata.aerocommons.org` (custom domain, edge-cached, zero egress), uploaded
from GitHub Actions over the S3 API, with a minisign-signed manifest trust
chain and the `pyefis-data` on-Pi updater. The configurator maps onto that and
adds two managed Cloudflare primitives for the account tier:

| Piece | Cloudflare service | New? |
|---|---|---|
| Editor UI (static SPA; the Phase 1 viewer already is static HTML/JS) | R2 static site (sibling of `site/`), e.g. `config.aerocommons.org` | No — same model |
| `schema.json` + palette thumbnail PNGs | Uploaded to R2 by a pyEfis CI job (same S3 token pattern as `cyclical.yml`) | No — more static objects |
| Layout compositing, drag/resize | Client-side in the browser | No |
| Auth + accounts API, save/load designs | **Workers** (or Pages Functions) | Yes — additive |
| User + design records | **D1** (SQLite at the edge) | Yes |
| Design blobs, compiled config packs | R2 (existing bucket or a sibling) | No |
| Sessions / short-lived tokens | **KV** (or signed cookies) | Optional |

**The one hard limit:** Workers run a V8 isolate — no Python, no Qt — so **live
instrument rendering cannot happen at the edge.** It never needs to: thumbnails
and schema are rendered in **pyEfis CI** and published to R2 as static assets;
the exact / Synthetic-Vision preview runs on the **device**. (Only on-demand
cloud rendering would require a container — Cloudflare Containers, or an
external box — which we do not anticipate needing.)

## Accounts & authentication

Goal: know who is using the system, and tie each saved design to a person.

**Approach:** a Workers-based auth service, provider-agnostic, supporting:

- **Google sign-in** via OpenID Connect (OAuth 2.0 authorization-code + PKCE).
  A Worker handles the redirect/callback, verifies the Google ID token, and
  upserts the user.
- **Email account signup** via passwordless **magic link** (preferred over
  passwords: nothing to hash or leak; a Worker mints a one-time signed link,
  sent through a mail API). A password option can be added later with a
  WASM scrypt/bcrypt if demanded.

**Sessions:** a signed, httpOnly cookie (JWT or opaque token in KV), short-lived
with refresh. **Build vs buy:** start with a thin Worker doing Google OIDC +
magic-link directly, or adopt a Workers-friendly library (Auth.js with its
D1 adapter, or Lucia) to avoid hand-rolling crypto. Either way the IdP set is
pluggable so GitHub/Apple/etc. can be added.

**What we store about a user:** id, email, display name, the linked identity
provider(s), created-at, last-seen. Nothing more is required to "know who's
using it."

## Persistent data store

A user's work is an **aircraft project** containing devices, each device having
versioned configs. Records live in **D1**; large/opaque blobs (the screen YAML,
compiled config packs) live in **R2**, referenced by key.

```
users(id, email, name, created_at, last_login)
identities(user_id, provider, provider_subject)        -- google, email, …
projects(id, user_id, name, created_at, updated_at)     -- one "aircraft / system"
devices(id, project_id, kind, name, width, height,      -- kind=pyefis today
        claim_code, device_token_hash, claimed_at, last_pull_at)
configs(id, device_id, version, yaml_r2_key, created_at) -- versioned screen layouts
-- grows into the System Designer:
components(id, project_id, kind, part_no, attrs_json)    -- BOM line items
connections(id, project_id, from_ref, to_ref, type)     -- wiring/bus edges
panel(project_id, layout_json)                           -- physical panel placement
```

Stage 1 uses `users / identities / projects / devices / configs`. Stage 2 adds
`components / connections / panel` to the *same* project — no migration of the
core model.

## Device pairing & pull-to-device

This is the mechanism you described — enter a token on the device, it loads its
config from the website like a data pack — built on the trust chain that already
exists:

1. **Author.** In the editor the user saves a screen layout to a device in their
   project. A Worker compiles it to the pyEfis screen YAML, writes the blob to
   R2, and records a `configs` row.
2. **Pair.** The user adds a device in the dashboard and gets a short
   **claim code** (or copies a **device token**). They enter it once on the Pi.
   The device exchanges the claim code for a long-lived, **revocable device
   token**, scoped read-only to *that device's* config.
3. **Pull.** `pyefis-data` (extended with an auth header + a `config` pack kind)
   fetches the device's assigned config from an authenticated endpoint, which
   returns a **signed config pack** (minisign, same as navdata). The device
   verifies the signature, then atomically swaps it into the active config tree
   (`~/makerplane/pyefis/config`) and pyEfis reloads.

Two independent guarantees compose cleanly:

- **Authenticity** — the minisign signature proves the pack came from the build
  key (unchanged from today's navdata trust model).
- **Authorization** — the device token proves *which* private config this device
  may read. Public navdata stays anonymous; user configs are per-account
  private, gated by the Worker.

Reusing `pyefis_data`'s verify-then-atomic-swap means a bad or unauthorized pull
can never disturb the live panel — the same safety property navdata already has.

## The codegen pipeline (pyEfis → R2)

The bridge from pyEfis to Cloudflare is one CI step that emits the editor's
static asset bundle and uploads it to R2:

- `python -m pyefis.editor.schema` → `schema.json` (already built — Phase 0).
- `tools/render_instrument.py` → one palette thumbnail PNG per instrument type
  (already built — Phase 0; ~27 types, render once per release).
- A small manifest tying a schema version to its thumbnail set.
- Upload to R2 with the same S3 token pattern `cyclical.yml` uses.

The editor (on Cloudflare) loads these static assets; nothing Qt runs at the
edge.

## System Designer roadmap

Once accounts + projects exist, the single-device editor generalizes:

- **Canvas of devices.** Drag multiple pyEfis displays and other hardware from a
  parts library onto a project canvas; each device opens into its own editor
  (the current screen editor).
- **Topology.** Express connections — the FIX / CAN-FIX bus, serial links, power
  — as edges between components (`connections`).
- **Outputs (generated from the project):**
  - **BOM** — every component with part numbers and attribution.
  - **Panel layout** — physical instrument-panel arrangement (cutouts,
    dimensions), distinct from the on-screen grid layout.
  - **Wiring / connection diagram** — rendered from the topology graph.

The parts library and the codegen story extend naturally: pyEfis remains the
source of truth for *its* schema/thumbnails; other hardware contributes its own
descriptors. Nothing about stages 1 and 2 conflicts — stage 2 is more node types
and more generated artifacts over the same accounts/projects/Cloudflare base.

## Security & privacy notes

- Public navdata packs stay anonymous and unauthenticated. **User configs are
  private** and only reachable with a valid session (editor) or device token
  (aircraft).
- Device tokens are scoped, revocable, and hashed at rest; revoking one stops
  future pulls without touching the live config already on the device.
- Keep the minisign signing key offline / in CI secrets exactly as today; the
  account layer adds authorization, it does not replace signing.
- Store the minimum PII (email + name); designs are the user's data, exportable
  and deletable.

## Open decisions

- **Auth: build vs. library vs. managed** (thin Worker + OIDC/magic-link, vs.
  Auth.js/Lucia, vs. Clerk/WorkOS). Lean: a small Worker + Google OIDC +
  magic-link, or Lucia, to stay self-hosted on Cloudflare.
- **Hostname:** `config.aerocommons.org` vs. a path under the existing origin.
- **Config pack granularity:** per-device pack vs. an account-level bundle the
  device filters.
- **Where the editor source lives:** a new `site/config/` in makerplane-data, or
  its own small repo that deploys to the same R2/Cloudflare project.

## Milestone / issue mapping

- **ECM milestone** + issues **#57–#63** (`billmallard/pyEfis`): #57 schema +
  render (done), #58 read-only viewer (done), #59 screen-size, #60 interactive
  editor, #61 palette/includes/prefs/dbkey, #62 full-screen preview/templates,
  #63 user accounts + cloud storage.
- This doc places #63 (and the account/delivery layers) on
  makerplane-data/Cloudflare, and the schema/render generators (#57) in pyEfis
  CI publishing to R2.
