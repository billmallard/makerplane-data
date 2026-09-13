# Configurator route sync — design pass (FP9, phase 2)

Status: **design pass — no code in this PR.** Build happens only after Bill
has flown the FP1-FP8 first cut and approves this doc (`makerplane/BACKLOG.md`
FP9: "ready; spec after FP4, build only after Bill approves the doc").
Spec: `makerplane/briefs/flight_plan_plan.md` §3.7, Appendix B. Board: AER-811.
Issue: makerplane-data#74. Epic: pyEfis#181. Capability: CAP-205.

## 0. Summary

Routes and user waypoints become project-scoped, append-only, one-active D1
rows — the same shape `aircraft_profiles` already uses
(`configurator/migrations/0002_aircraft_profiles.sql`). A device pulls its
project's cloud-authored routes on `config_pull` (no restart needed — the
`flight_plan` instrument re-reads its catalog on page open, unlike a screen
change). A device pushes its own device-authored routes and user waypoints
back — the **first device-authenticated write** this Worker will have. The
device-side and cloud-side namespaces never overlap (`managed_*` vs
everything else), so there is no merge policy to design. Six sections below,
each ending in a decision list; §8 has the sequence diagrams the DoD asks for.

## 1. What exists today (reuse audit)

| Piece | Where | Reusable as-is |
|---|---|---|
| `devices` (claim code, `device_token_hash`, pairing) | `configurator/migrations/0001_init.sql:36-48` | Yes — routes hang off the same `devices`/`project_id` |
| Append-only, one-active-version D1 pattern | `aircraft_profiles`, `0002_aircraft_profiles.sql:6-16` | Yes — `routes`/`user_waypoints` copy the shape verbatim |
| Device-authed **read**: `GET /device/config` (Bearer token, ETag/304) | `configurator/src/index.ts:221-244`, `docs/device_deployment.md:100-109` | Pattern reused for `GET /device/routes`; **no** device-authed **write** exists yet |
| Snapshot / atomic-install / rollback discipline | `pyefis_data/config_pull.py:232-253` (`_snapshot`), `:564-626` (`rollback`, `restart_and_verify`) | Yes for the pull side, minus the restart step (see §3) |
| `managed*` naming convention marking Worker-owned files | `pyefis_data/config_pull.py` `_managed_rel_paths` (`screens/managed*.yaml`, `buttons/managed-next*.yaml`) | Directly extends to `routes/managed_<slug>.json` |
| Route JSON schema `mp-route/1` / `mp-userwpt/1` | brief Appendix B | **Frozen already** — written so FP9 needs no format change once FP4 lands it in pyEfis; this doc treats it as given, not something to re-derive |
| No-signing-on-device-channel precedent | `docs/device_deployment.md:111-115` ("no authenticity chain worth protecting... token-authorised over TLS") | Cited, **not** carried over unmodified — see §5, the calculus changes for a write |

## 2. D1 schema

```sql
-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Routes + user waypoints (docs/route_sync.md). Modelled on aircraft_profiles:
-- append-only versions, one active per (project, source, slug). json carries
-- the mp-route/1 / mp-userwpt/1 payload verbatim (brief Appendix B) so this
-- table never needs to understand the route format.

CREATE TABLE IF NOT EXISTS routes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source      TEXT NOT NULL CHECK (source IN ('cloud', 'device')),
  slug        TEXT NOT NULL,       -- url-/filename-safe; cloud slugs are
                                    -- picked in the web UI, device slugs come
                                    -- from the uploaded route's "name" (slugified)
  version     INTEGER NOT NULL,
  json        TEXT NOT NULL,       -- mp-route/1
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (project_id, source, slug, version)
);
CREATE INDEX IF NOT EXISTS idx_routes_project
  ON routes(project_id, active);

CREATE TABLE IF NOT EXISTS user_waypoints (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source      TEXT NOT NULL CHECK (source IN ('cloud', 'device')),
  version     INTEGER NOT NULL,
  json        TEXT NOT NULL,       -- mp-userwpt/1, the whole set per version
                                    -- (whole-file replace, not per-point rows —
                                    -- matches how the device writes the file)
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (project_id, source, version)
);
CREATE INDEX IF NOT EXISTS idx_user_waypoints_project
  ON user_waypoints(project_id, active);
```

`source` is the namespace column, not just metadata: it is what lets a
cloud-authored slug and a device-authored slug collide harmlessly (§4), and
it is what the web UI's "from device" section filters on (§6). It plays the
role `managed_` vs bare-name plays in the filesystem convention — deliberately
duplicated as an explicit column rather than folded into `slug`, so a SQL
query never has to parse a naming convention to find "the device's own
routes."

**Decisions for Bill**
1. One `routes` table with a `source` discriminator (above), vs two tables
   (`routes_cloud` / `routes_device`). Recommendation: one table — the web UI
   lists both in one query, and D1's per-table row/index overhead isn't a real
   cost here. Flag if you'd rather keep cloud and device data physically
   separate for backup/export reasons.
2. `user_waypoints` is whole-file-replace per version (matches
   `pyefis_data`'s own `recent.json`/`user_waypoints.json` model: it's already
   a small file the device rewrites wholesale). If waypoint counts grow large
   enough that re-uploading the whole set on every edit becomes wasteful,
   revisit as per-row storage later — not needed at FP9's scale (the guide's
   own limits: idents <=6 chars, names <=32; this is a hand-entered list, not
   a database).

## 3. Pull: `GET /device/routes`

```
GET /device/routes
Authorization: Bearer <device_token>
If-None-Match: "<etag>"

200 OK  (or 304 with empty body if If-None-Match matches)
ETag: "<sha256 of the canonical set below>"
Content-Type: application/json

{
  "routes": [
    { "slug": "ksba-ksmx", "version": 3, "route": { "schema": "mp-route/1", ... } },
    ...
  ],
  "user_waypoints": { "version": 5, "waypoints": { "schema": "mp-userwpt/1", ... } }
}
```

Only `source = 'cloud'` rows are returned — a device never pulls back its own
uploads (it already has them; `managed_*` is reserved for cloud content, so
echoing device-authored routes through the pull path would risk them landing
in the `managed_` namespace and becoming read-only to the device that created
them). The ETag covers the whole set (all active cloud routes + the active
cloud user-waypoints version) — same shape as `GET /device/config`'s
`x-config-version`, generalized from "one version int" to "hash of the set"
since routes are a list, not a single blob.

**`config_pull` integration** (extends `pyefis_data/config_pull.py`, not a new
module — same reasoning as `docs/device_deployment.md:116-118`, "extend
`pyefis_data`, don't add a sibling"):
- Each cloud route becomes `<userdir>/routes/managed_<slug>.json` (verbatim
  `mp-route/1` content) — same `managed*` convention as `screens/managed*.yaml`.
- Cloud user waypoints get merged into `user_waypoints.json` under a `managed`
  section, distinct from the device's own entries, so a device-side edit to a
  local waypoint is never clobbered by a cloud pull and a cloud waypoint is
  never mistaken for a locally-created one deletable from the device UI.
- Backup before write, atomic write, rollback on failure — same
  `_snapshot`/`_atomic_write`/rollback discipline as `config_pull.py:232-253,
  564-626`, extended to cover `routes/managed_*.json` and the `managed`
  section of `user_waypoints.json` in `_managed_rel_paths`.
- **No pyEfis restart.** Unlike a screen swap, `flight_plan` (FP5a) re-reads
  its catalog when its Catalog page opens (FP3's in-memory ident index is
  rebuilt from the JSON files on disk, not cached at process start) — so
  `restart_and_verify`/crash-rollback (`config_pull.py:555-626`) doesn't apply
  here. A bad file just fails to appear as a catalog entry; there is no
  process to crash.

**Decisions for Bill**
3. Pull cadence: boot-only (today's rule for screens, "no in-flight
   restarts") vs also on a timer. Recommendation: **also on a timer** — since
   nothing restarts, there is no in-flight-disruption risk a boot-only rule
   is protecting against, and "I built a route on my phone before the flight
   and want it on the panel without rebooting" is a real use case timer-pull
   solves for free. Suggest reusing the existing `pyefis-data` systemd timer
   cadence rather than adding a second timer unit.
4. Is a missing/stale `active` cloud route ever *removed* from the device on
   pull (i.e. does deactivating a route in the web UI delete
   `managed_<slug>.json` on next pull), or only ever added/updated? Recommend
   removal-on-deactivate — the point of one-active-per-slug is that stale
   versions don't linger — but flag explicitly since deletion-on-sync is the
   one pull behavior with no `aircraft_profiles`/config precedent (that
   pattern only ever *adds* a version).

## 4. Push: `POST /device/routes`

The first device-authenticated **write**. Everything the pull side reused a
pattern for; this one has none to reuse from — see §5 for why that matters.

```
POST /device/routes
Authorization: Bearer <device_token>
Content-Type: application/json

{
  "routes": [ { "slug": "my-x-country", "route": { "schema": "mp-route/1", ... } } ],
  "user_waypoints": { "waypoints": { "schema": "mp-userwpt/1", ... } }
}

200 OK  { "routes": [{ "slug": "my-x-country", "version": 1 }], "user_waypoints": { "version": 1 } }
4xx on: unknown token (401), malformed schema/oversize payload (400),
        slug collision with a cloud-authored slug (409 — see below)
```

Inserted as `source = 'device'` rows, versioned the same append-only way as a
cloud edit. Namespacing: a device-authored slug is whatever the route's
`name` field slugifies to; if that would collide with an *existing
device-authored* slug for the same project, it's a new version of that slug
(same as re-saving in the web UI would be); it can never collide with a
*cloud-authored* slug because `source` is part of the uniqueness key (§2) —
so the 409 above is defensive (a client bug guard), not a real namespace
hazard. The **web UI** shows `source = 'device'` rows in a "from device"
section, read-only-labelled-as-device-origin but editable like any other
route (editing it in the web UI doesn't change its `source`; it just adds a
new version under the same slug — a builder claiming a device route as their
own canonical copy is a "Save as cloud route" action, i.e. a *new* row with
`source = 'cloud'`, not a mutation of the device row).

**What triggers a push** is a `pyefis_data` decision, out of this Worker's
scope but stated here since the endpoint contract depends on it: any file
under `routes/` **not** matching `managed_*` is a push candidate (the same
glob-exclusion `_managed_rel_paths` already uses in reverse). Push timing —
on the same timer as pull, on demand from a device-side "sync" action, or
both — is open; see decision 6.

**Re-pairing.** `pyefis-data pair <code>` binds a *new* `device_token` to
whatever `devices` row the code was minted for (`configurator/src/db.ts`
`claimDevice`) — it does not revoke the old token, and it does not move any
data between projects. Concretely: if a device re-pairs to a device row in a
**different** project, its previously-pushed `source='device'` rows stay
exactly where they were, under the old project — they are not migrated,
merged, or deleted. The device's next push lands under the new project as
fresh rows. This is consistent with how `configs` already behaves (a config
version is tied to the `device_id` it was pushed under, forever) — routes add
nothing new here except that it is a *write* an unrevoked stale token could
still make, which is §5's concern, not this one's.

**Decisions for Bill**
5. Payload cap and rate limit for the write path. `GET /device/config`
   has none because it only ever returns Worker-authored data; a write
   endpoint accepts device-authored bytes and needs both, if only to bound a
   misbehaving or compromised device. Proposing 64 KiB per route / 256 KiB
   per request / one push accepted per device per 60 s as a starting point —
   these are guesses, not measurements; flag if you want different numbers or
   want them measured against real route sizes first.
6. Push trigger: timer-only (matches pull), device-initiated only (a "sync"
   button/command, so nothing uploads without the pilot asking), or both.
   No recommendation — this is a product-feel call (silent background sync
   vs an explicit action), not an engineering one.

## 5. Security: the device token

Today's device token (`pyefis_data/config.py:59`, minted in
`configurator/src/index.ts:211-224`) is long-lived, stored in plaintext on
the device, unscoped, and — per the explicit decision already on record —
deliberately unsigned, because it only ever authorised a *read*
(`docs/device_deployment.md:111-115`: "the pull is already token-authorised
over TLS... no authenticity chain worth protecting"). §4 changes that
calculus: **a leaked token can now write**, not just read. Concretely, today
a stolen token gets an attacker a copy of someone's panel layout; after FP9
it also gets them the ability to insert rows into that project's `routes`/
`user_waypoints` tables — not read *other* projects' data (D1 queries stay
`project_id`-scoped the same way `/api/*` is `user_id`-scoped per
`configurator/CLAUDE.md`'s ownership rule), but write into this one, with no
size/rate ceiling beyond whatever §4 decision 5 sets, and no way for the
project's owner to tell a device-authored row from a forged one — they carry
the same `source = 'device'` tag either way.

This doc does not resolve the question; it names the options because DoD asks
for the token scope/rotation question treated as real, not a detail.

**Decisions for Bill**
7. **Scope.** Leave the token as-is (one bearer capability, same token reads
   config and now writes routes) vs add a scope bit (`can_write_routes` on
   the `devices` row, defaulting off, an explicit opt-in step in the pairing
   flow or dashboard) vs split into two tokens (a read token for config pull,
   a separate write token for routes, independently revocable). Recommend at
   minimum the scope-bit version — it's a single boolean column and a
   pairing-flow checkbox, costs little, and means a device that only ever
   pulls config never holds write capability at all.
8. **Rotation.** There is currently no rotation path at all — a token is
   minted once at pairing and lives until the `devices` row is deleted or
   re-paired over. Does a write-capable token need one (dashboard "revoke and
   re-pair" button; a max-age with forced re-pair; nothing, matching the
   read-only precedent)? Flag that re-pairing already silently orphans the
   old token live (§4, "Re-pairing") — if a rotation path ships, this is the
   place to decide whether re-pairing *should* revoke the token it replaces,
   which it does not do today.
9. Does a stolen/malicious device write need to be *visible*, i.e. should the
   web UI surface `last_pull_at`-style provenance (`last_push_at`, or a raw
   count of device-authored rows) so an owner has some chance of noticing
   unexpected activity? Low cost, no precedent either way in this codebase.

## 6. Web UI: Routes page

A new per-project page (alongside the existing devices/aircraft-profile
pages), listing all `active` rows for the project split into two sections —
cloud-authored (editable, versioned, "activate this version") and
from-device (read-only origin badge, but see §4 on "Save as cloud route").
Route editing targets the `mp-route/1` schema directly (Appendix B): an
ordered waypoint list, each entry an ident (airport/VOR/NDB/fix) or a
lat/lon user point, `type`/`role` per the brief's enum. Ident entry reuses
pyEfis's FastFind matching rule (FP3, `flightplan/waypoints.py`) so typing an
ident in the web editor and on the panel behave the same way — not because
the Worker imports pyEfis code, but because the *rule* (prefix match, nearest
disambiguation) is re-specified against whatever data source the Worker uses.

**Decisions for Bill**
10. Where do web-UI ident lookups come from? Options: (a) the same published
    navdata packs via R2 (`navdata.aerocommons.org` — public, no auth needed,
    but a multi-hundred-MB SQLite pack is a heavy thing for a Worker to query
    per-keystroke), (b) a small CI-built ident index (ident, lat, lon, name —
    kilobytes, rebuilt on the same cyclical cadence as the packs, uploaded to
    R2 alongside `assets/editor/`), (c) proxy lookups through a device's own
    pull (no — routes must be editable before a device is even paired).
    Recommend (b); it mirrors how `schema.json`/`groups.json` are already
    small CI-built artifacts the Worker reads from R2, and keeps route
    editing possible with zero device involvement.
11. Invert / copy / delete / activate-a-version are named in the spec as
    required verbs — confirm no additional verbs needed (e.g. "duplicate to
    another project" for a multi-aircraft owner) before build.

## 7. Environments

No new decision — routes/user_waypoints follow `docs/environments.md`'s
existing per-env-D1 rule exactly (separate `routes`/`user_waypoints` tables
in the DEV/QA/PROD D1 databases, migrated the same way `0002_...sql` already
was). Flagged as its own section only because the issue's DoD asked for it
explicitly.

**Decision for Bill**
12. Confirm no exception is wanted here (e.g. seeding QA with sample routes
    for demo purposes) — default assumption is none, same as every other
    table.

## 8. Sequence diagrams

**Pull** (boot, or timer if decision 3 lands that way):
```
 pyefis-data (config_pull)          Worker                         D1
 ────────────────────────          ──────                         ──
 GET /device/routes  ─────────────▶
   Authorization: Bearer <token>
                                    lookup device by sha256(token) ──▶ devices
                                    ◀───────────────────────────────
                                    fetch active routes+waypoints  ──▶ routes,
                                    ◀─────────────────────────────── user_waypoints
                                    compute ETag over the set
 ◀───────────────────────────────  200 { routes[], user_waypoints }
                                       (or 304 if If-None-Match matches)
 _snapshot() routes/, user_waypoints.json "managed" section
 atomic-write managed_<slug>.json per route, merge managed waypoints
 (no restart — flight_plan re-reads its catalog on page open)
```

**Push** (device-authored route created/edited locally, then synced):
```
 pyefis-data                        Worker                         D1
 ────────────                       ──────                         ──
 POST /device/routes ─────────────▶
   Authorization: Bearer <token>
   { routes: [...], user_waypoints: {...} }
                                    lookup device by sha256(token) ──▶ devices
                                    ◀───────────────────────────────
                                    [decision 7: check write scope]
                                    validate schema, size, rate       (§4 decision 5)
                                    insert source='device' rows     ──▶ routes,
                                    ◀─────────────────────────────── user_waypoints
 ◀───────────────────────────────  200 { routes:[{slug,version}], ... }
 (web UI now shows the new route under "from device" on next load)
```

**Re-pair to a different project:**
```
 builder (dashboard)     Pi (pyefis-data pair)     Worker              D1
 ────────────────────    ──────────────────────    ──────              ──
 mint claim_code for
 device row in Project B
 ────────────────────▶  pyefis-data pair <code>
                         POST /device/pair ────────▶
                                                     validate code    ──▶ KV
                                                     mint new token
                                                     UPDATE devices    ──▶ devices
                                                       SET device_token_hash=...
                                                       WHERE id=<Project B's device row>
                                                     (Project A's device row
                                                      and its device_token_hash
                                                      are untouched — decision 8)
                         ◀──────────────────────── device_token (new)
                         data.yaml: device_token = <new>
 [next push now lands under Project B; Project A's earlier
  source='device' rows remain in Project A, orphaned but intact]
```

## 9. Open items carried elsewhere

- `docs/system_designer.md` is cited by `configurator/CLAUDE.md:6` and
  `docs/device_deployment.md:8` as the product-vision companion doc, but it
  does not exist on `dev`, `qa`, or `main` — it only exists on the
  `design/system-designer` branch. This design pass cross-links it per the
  issue's DoD; the link is currently dead on every branch that matters. **Flag
  for Bill**: recover it onto `dev` (either merge that branch's version or
  re-derive it), independent of FP9 — noted here so it isn't lost, not fixed
  in this PR (out of scope: docs-recovery, not route sync).
- FP4 (pyEfis#183, the route model + catalog storage that names the
  `mp-route/1`/`mp-userwpt/1` schema authoritatively) is still open at the
  time of this doc. This design pass treats the brief's Appendix B as frozen
  per the plan's own claim ("the JSON schema in Appendix B is written so FP9
  needs no format change") — flagging the dependency is still live in case
  FP4's implementation surfaces a field this doc didn't anticipate.

## Decision list (roll-up, for a single Bill pass)

1. One `routes` table with a `source` column vs two tables.
2. `user_waypoints` whole-file-replace per version — accept, or per-row now.
3. Pull cadence: boot-only vs also on a timer.
4. Does a deactivated cloud route get removed from the device on next pull.
5. Push payload cap / rate limit numbers.
6. Push trigger: timer, device-initiated action, or both.
7. Token scope: unchanged, scope-bit opt-in, or split read/write tokens.
8. Token rotation path, and whether re-pairing should revoke the old token.
9. Whether push activity needs to be visible in the dashboard.
10. Ident-lookup source for the web route editor.
11. Confirm the verb list (invert/copy/delete/activate) is complete.
12. Confirm no environment-specific exception (e.g. QA seed data) is wanted.
