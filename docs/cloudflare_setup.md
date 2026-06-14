# Cloudflare + CI setup — reproduce-from-nothing runbook

Everything needed to stand up (or rebuild) the distribution side of
`makerplane-data` from a blank Cloudflare account. If the bucket, the
keys, or the whole account were lost tomorrow, following this top-to-bottom
recreates a working daily pipeline.

**Audience:** a MakerPlane maintainer with admin on the Cloudflare account
and admin on the `makerplane-data` GitHub repo. No prior context required.

**What this sets up**

```
GitHub Actions (cyclical.yml)  --S3 API-->  Cloudflare R2 bucket
   builds + signs packs                     makerplane-data
                                              |  public serving
                                              v
                              data.makerplane.org  (Pi / browser downloads)
```

Cloudflare provides two things here:
1. **R2** — object storage for the packs + manifest. Zero egress fees; this
   is the load-bearing piece.
2. *(later, optional)* **Pages** — the static dashboard/region-picker site
   (Phase E). Not required for the pipeline or the Pi updater.

The signing key is independent of Cloudflare but is documented here because
it is part of the same one-time go-live and the same GitHub secrets.

---

## Current state (as built 2026-06-14)

So a reader knows what is already done vs. what a from-nothing rebuild redoes:

- [x] R2 enabled on the account; bucket **`makerplane-data`** created (region **ENAM**).
- [x] R2 API token (Object Read & Write, scoped to the bucket) created.
- [x] Production signing key minted (key id `178caefeabc5afb1`); public key
      committed at [`keys/minisign.pub`](../keys/minisign.pub).
- [x] GitHub Actions secrets set: `MINISIGN_SECRET_KEY`, `R2_ENDPOINT`,
      `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
- [x] `cyclical.yml` ran live: airports (current+next) + obstacles + signed
      manifest uploaded; idempotent on re-run. Daily cron (09:20 UTC) active.
- [x] **Public serving** via custom domain **`navdata.aerocommons.org`**
      (zone `aerocommons.org`, already on this account). Trust chain verified
      end-to-end over HTTPS. The `r2.dev` dev URL also remains enabled.
- [x] **Production URL base** wired into the pipeline default
      (`https://navdata.aerocommons.org/packs`); the orchestrator re-roots all
      manifest pack URLs onto it.
- [ ] **Pages site** (Phase E): not built yet.

> Ownership note: the account today is Bill Mallard's personal Cloudflare
> account; the data domain is `navdata.aerocommons.org` (AeroCommons, the
> initiative this serves). The intent (per `data_manager_strategy.md`) is for
> this to live on a **MakerPlane**/AeroCommons org account eventually. See
> *Transfer / ownership* below — the runbook is written so the destination
> account does not matter.

---

## Prerequisites

- A Cloudflare account with a payment method (R2 activation requires one even
  though our usage is within or near the free tier — see *Cost*).
- Admin on the GitHub repo `makerplane-data` (to set Actions secrets).
- Locally: the GitHub CLI `gh` (authenticated), Python 3.10+, and this repo
  checked out and pip-installed (`pip install -e .`).
- Optional but recommended for scripted/repeatable setup: Cloudflare
  **Wrangler** CLI (`npm i -g wrangler`, then `wrangler login`). Every R2 step
  below has both a dashboard path and a Wrangler path.

---

## Step 1 — Enable R2 on the account

R2 is opt-in per account (one time).

- **Dashboard:** Cloudflare dashboard → **R2** in the left sidebar → **Enable R2**
  / **Purchase R2 Plan** → add/confirm a payment method.
- There is no API/Wrangler way to do the initial activation; it must be done
  in the dashboard once. (Symptom if skipped: API calls return
  `10042: Please enable R2 through the Cloudflare Dashboard`.)

Record your **Account ID** now — you will need it for the S3 endpoint. It is
shown on the R2 overview page and on the account home page
(`Account ID: <32 hex chars>`).

---

## Step 2 — Create the bucket

Name it **`makerplane-data`** (the pipeline defaults to this; overridable via
the `R2_BUCKET` repo variable).

- **Dashboard:** R2 → **Create bucket** → name `makerplane-data` → choose a
  location hint near most users (**ENAM** = Eastern North America was used) →
  Standard storage class → Create.
- **Wrangler:**
  ```bash
  wrangler r2 bucket create makerplane-data
  ```

Leave the bucket **private** for now. Public read access is configured
separately in Step 7 (and is not needed to run the pipeline).

---

## Step 3 — Create the S3 API token (upload credentials)

The pipeline uploads via the S3-compatible API, which needs an Access Key ID
+ Secret Access Key. These are shown **once** — capture them immediately.

- **Dashboard:** R2 → **Manage R2 API Tokens** → **Create API Token**:
  - **Permissions:** *Object Read & Write* (not Admin — least privilege).
  - **Specify bucket(s):** scope to **`makerplane-data`** only.
  - (Optional) TTL: leave as no-expiry for an unattended pipeline, or set a
    rotation reminder.
  - **Create**. The result screen gives three things:
    1. **Access Key ID**
    2. **Secret Access Key**  ← shown once; copy now
    3. The **S3 API endpoint** — `https://<account-id>.r2.cloudflarestorage.com`
       (jurisdiction-neutral form; use this one).

The endpoint host is just `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. The
bucket name is **not** part of the endpoint — boto3 addresses the bucket
separately (the pipeline passes `R2_BUCKET`).

> There is currently no Cloudflare MCP/Wrangler command that returns the
> Secret Access Key for you — token creation is a dashboard action by design.

---

## Step 4 — The signing key (root of trust)

The manifest is ed25519-signed; the Pi trusts the committed public key and
nothing else. This is independent of Cloudflare but part of go-live.

Generate the keypair with this repo's tool (minisign wire-format; pure
PyNaCl, no `minisign` binary needed):

```bash
python -m packtools.cli genkey --out keys \
  --comment "makerplane-data production signing key"
# writes keys/minisign.pub (commit) and keys/minisign.sec (NEVER commit)
```

Then:

1. **Commit the public key** (`keys/minisign.pub`). The Pi updater embeds it.
2. **Back up `keys/minisign.sec` offline** (password manager / encrypted USB).
   It is the only thing that can sign a manifest; losing it means minting a
   new key and re-committing the public key (and the Pi must be updated). Do
   **not** commit it — `*.sec` is gitignored.
3. Put the secret into CI as `MINISIGN_SECRET_KEY` (Step 5).

The secret format is base64 of `key_id(8 bytes) ‖ ed25519_seed(32 bytes)` —
exactly the one line in `keys/minisign.sec`. See `packtools/signing.py`.

> Custody: ideally more than one maintainer holds the offline backup. Rotation
> is cheap (new `genkey`, recommit pub, reset the secret, push a pyEfis update
> that ships the new pub) — see *Rotation* below.

---

## Step 5 — GitHub Actions secrets + variables

Four secrets drive `cyclical.yml`. Set them on the repo (admin required):

```bash
R=billmallard/makerplane-data        # or makerplane/makerplane-data after transfer

# Signing key (from keys/minisign.sec — stays off your terminal history via <)
gh secret set MINISIGN_SECRET_KEY  --repo "$R" < keys/minisign.sec

# R2 credentials (from Step 3)
gh secret set R2_ENDPOINT          --repo "$R" --body "https://<account-id>.r2.cloudflarestorage.com"
gh secret set R2_ACCESS_KEY_ID     --repo "$R" --body "<access-key-id>"
gh secret set R2_SECRET_ACCESS_KEY --repo "$R" --body "<secret-access-key>"

gh secret list --repo "$R"          # expect all four
```

Optional **repository variables** (not secrets) — only if you need to override
defaults:

```bash
# bucket name (defaults to makerplane-data inside the workflow)
gh variable set R2_BUCKET   --repo "$R" --body "makerplane-data"
# where the FAA build tools are checked out from (interim tool-sharing shim)
gh variable set PYEFIS_REPO --repo "$R" --body "billmallard/pyEfis"
gh variable set PYEFIS_REF  --repo "$R" --body "svs-renderer"
```

`PYEFIS_REPO`/`PYEFIS_REF` exist because the FAA→sqlite build tools currently
live in pyEfis (`tools/build_airport_db.py`, `build_obstacle_db.py`). When
those move into a standalone `pyefis-tools` package, point these at it or drop
them. See `packtools/build/__init__.py`.

---

## Step 6 — First run + verification

```bash
gh workflow run cyclical.yml --repo "$R"          # manual trigger (also runs daily 09:20 UTC)
```

Watch it: `gh run watch <id> --repo "$R" --exit-status`. A green run logs:

```
built+uploaded airports-conus 2606 sha256=…  5,861,376 B
built+uploaded airports-conus 2607 sha256=…  5,890,048 B
built+uploaded obstacles-conus 260611 sha256=…  75,075,584 B
manifest: 3 pack(s), 3 new, signed + uploaded
```

**Independent confirmation that R2 actually has the objects:** run it a second
time. The `HEAD`-to-skip check reads R2, so a clean second run logs
`skip … (present)` for every pack and `0 new`. That round-trips R2 reads and
proves the objects exist — no separate tooling needed.

You can also verify locally without R2 at all:

```bash
python -m packtools.run_cyclical --dry-run        # no secrets, no network
python -m pytest                                   # 60 tests
```

---

## Step 7 — Public serving (so devices can download)

The pipeline only needs the S3 API (private). For a Pi or browser to *download*
packs, the bucket needs a public read URL. Two options:

**Option A — `r2.dev` managed URL (fastest, ugly URL).**
- Dashboard: R2 → `makerplane-data` → **Settings** → **Public access** →
  **Allow Access** under "R2.dev subdomain". You get
  `https://pub-<hash>.r2.dev/<key>`.
- Set the pipeline's `--url-base` / the Pi's manifest base to that origin.
- Good for testing; rate-limited and not custom-branded.

**Option B — custom domain `navdata.aerocommons.org` (production, in use).**
- Requires the domain's zone to be on this Cloudflare account (it is —
  `aerocommons.org`). Objects become `https://navdata.aerocommons.org/<key>`
  (e.g. `…/manifest.json`, `…/packs/airports-conus-2606.pack`), edge-cached,
  zero egress. This is the `--url-base` default the pipeline now uses.
- **Dashboard:** R2 → `makerplane-data` → **Settings** → **Custom Domains** →
  **Add** → `navdata.aerocommons.org` → **Connect Domain**. Cloudflare adds the
  proxied CNAME and provisions the cert (Initializing → Active in minutes).
- **REST API** (how it was actually done — needs a token with *Workers R2
  Storage:Edit* + *DNS:Edit* + *Zone:Read* on the zone):
  ```bash
  curl -X POST -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
    "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/r2/buckets/makerplane-data/domains/custom" \
    -d '{"domain":"navdata.aerocommons.org","zoneId":"'"$ZONE_ID"'","enabled":true,"minTLS":"1.2"}'
  ```
  (`$ZONE_ID` from `GET /zones?name=aerocommons.org`.) Note: a resource-scoped
  token returns `1000 Invalid API Token` from `/user/tokens/verify` yet still
  authorizes the scoped calls — verify by the call succeeding, not by that
  endpoint. Revoke the token afterward.

**CORS (only if the Pages site fetches the manifest cross-origin).** If the
site is on a different hostname than the data (recommended: `manager.…` for the
site, `data.…` for objects), allow GET from the site origin:
- Dashboard: R2 → bucket → **Settings** → **CORS policy**:
  ```json
  [{ "AllowedOrigins": ["https://manager.makerplane.org"],
     "AllowedMethods": ["GET", "HEAD"],
     "AllowedHeaders": ["*"] }]
  ```
- Not needed for the Pi (the CLI is not a browser; no CORS).

After enabling public serving, confirm:
```bash
curl -fsSI https://navdata.aerocommons.org/manifest.json | head -5
curl -fsS  https://navdata.aerocommons.org/manifest.json.minisig
```

---

## Cost

- **Storage:** ~$0.015/GB/month. Current footprint (airports ×2, obstacles,
  manifest) ≈ 160 MB ≈ a few cents. Adding terrain (regional GLO-30 packs)
  brings the total to a few hundred GB ≈ **$5–8/month**.
- **Egress:** **$0** (R2's defining feature) — downloads, however popular, add
  nothing.
- **Class A ops** (writes/lists): negligible at one daily build.
- **GitHub Actions / Pages:** free for public repos.

---

## Rotation, teardown, transfer

**Rotate the S3 token** (suspected leak, or routine): create a new Object
Read & Write token (Step 3), update the three `R2_*` secrets (Step 5), then
revoke the old token in the dashboard. No code change; next run uses the new
creds.

**Rotate the signing key:** `genkey` a new pair, commit the new
`keys/minisign.pub`, reset `MINISIGN_SECRET_KEY`, and ship the new public key
to devices (a pyEfis update — the Pi embeds the pub). Old manifests stay valid
only under the old key, so re-sign on the next build. Keep the old `.sec` until
all devices have the new pub.

**Tear down:** delete the bucket (`wrangler r2 bucket delete makerplane-data`
or dashboard), revoke the API token, and delete the GitHub secrets. The repo
and code are unaffected.

**Transfer to a MakerPlane account/org:** the runbook is account-agnostic.
Steps: (1) on the destination account, do Steps 1–3 (enable R2, create bucket,
create token); (2) re-point the four GitHub secrets (Step 5) at the new
endpoint/creds; (3) move the repo to the `makerplane` org and update `$R`;
(4) re-attach `data.makerplane.org` to the new bucket (Step 7). Re-run the
workflow — it rebuilds and re-uploads from scratch (idempotent). Nothing in
the packs or manifest is account-specific.

---

## Quick reference

| Item | Value |
|---|---|
| Bucket | `makerplane-data` (region ENAM) |
| S3 endpoint | `https://<account-id>.r2.cloudflarestorage.com` |
| Object keys | `manifest.json`, `manifest.json.minisig`, `packs/<id>-<cycle>.pack` |
| Public base | `https://navdata.aerocommons.org/` (custom domain; `r2.dev` dev URL also live) |
| GH secrets | `MINISIGN_SECRET_KEY`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| GH variables (optional) | `R2_BUCKET`, `PYEFIS_REPO`, `PYEFIS_REF` |
| Pipeline trigger | `cyclical.yml` — daily 09:20 UTC + `workflow_dispatch` |
| Signing key id | `178caefeabc5afb1` (public key in `keys/minisign.pub`) |

Related: [data_manager_strategy.md](data_manager_strategy.md),
[data_manager_implementation.md](data_manager_implementation.md),
the workflows in [`.github/workflows/`](../.github/workflows/).
