# Terrain cloud build — implementation plan

Move the bulk GLO-30 terrain pyramid **build + publish** off the workstation to
an ephemeral cloud instance. Strategy/why lives in `docs/terrain.md` and the
`terrain-cloud-build` memory; this is the *how*. Verdict recap: **AWS EC2, not
GitHub Actions** — the workload needs 100s of GB of disk and pushes ~150 GB to
R2, both of which fight GHA's ephemeral runners. The win is that the ~150 GB
upload runs cloud→R2 over a fat pipe instead of a home uplink.

## Recommended shape

An **on-demand, self-terminating spot instance** driven by a single bootstrap
script. Terrain is rebuilt rarely (static GLO-30; only when adding regions or
re-deriving), so "launch a box, it builds+publishes+shuts itself down" beats any
always-on service or scheduled pipeline.

```
  launch (spot c7i, user-data = bootstrap.sh, terminate-on-shutdown)
     │
     ├─ pull code (pyEfis + makerplane-data, pinned tag/commit)
     ├─ pull secrets (R2 creds + minisign.sec) from SSM Parameter Store
     ├─ pull source tree  ── Path A: aws s3 sync from R2  terrain-src/glo30hgt
     │                       Path B: fetch_glo30.py + convert_glo30.py (Copernicus)
     ├─ build_terrain_mips.py  (parallel, all cores)      ~20–30 min @ 16 vCPU
     ├─ make-terrain --upload  per region → R2 + re-sign  cloud→R2 fat pipe
     ├─ verify manifest (8/8 pyramid + signature OK)
     └─ shutdown -h now  → instance terminates
```

**Do Path A first** (lift-and-shift — reuses exactly what we proved by hand on
2026-07-09). Graduate to Path B (fully self-contained, no dependency on a
pre-built tree) only if rebuilds become frequent.

## Prerequisites / the few real decisions

| Decision | Recommendation |
|---|---|
| Cloud | **AWS** (Path B pulls Copernicus from AWS Open Data same-cloud). Cheaper-egress alt: a Hetzner/DO box (included bandwidth vs AWS $0.09/GB out) — viable for Path A, loses the same-cloud Copernicus pull. |
| Region | Any US region for Path A. For Path B, co-locate with the Copernicus bucket (verify its region + requester-pays before committing). |
| Instance | **c7i.4xlarge** (16 vCPU) spot for ~$0.20–0.30/h; c7i.8xlarge (32 vCPU) to halve build time. |
| Disk | **gp3, 250 GB** (fast; sidesteps the workstation's slow-disk zip-build phase). Peak use ≈ source 87 + mips 31 + one region pack ~40 = ~160 GB (packs deleted after each upload). |
| Code version | Pin to a tag/commit for reproducibility (e.g. `terrain-2024ed-pyramid`), or track `dev` for latest tooling. |
| Trigger | v1 = launch by hand / one-line AWS CLI wrapper. v2 = GHA `workflow_dispatch` that calls `run-instances`. |

## Phase 1 — Park the source tree in cloud storage (one-time)

Path A needs the `glo30hgt` tree reachable from EC2. Store it in the existing R2
bucket under a `terrain-src/` prefix (single source of truth; R2→EC2 egress is
free, AWS ingress is free):

```bash
# from the workstation, once (~87 GB; minutes at restored gigabit)
aws s3 sync D:/EarthData/glo30hgt s3://makerplane-data/terrain-src/glo30hgt \
    --endpoint-url "$R2_ENDPOINT"      # R2 is S3-compatible
```

(Path B skips this — it derives the tree on the instance from Copernicus.)

## Phase 2 — Secrets & IAM (one-time)

Never bake secrets into an AMI or the script. Put them in **SSM Parameter Store
(SecureString)** and pull at runtime:

- `/terrain/r2_endpoint`, `/terrain/r2_access_key_id`, `/terrain/r2_secret_access_key`
  (from `CloudFlare R2 Bucket Keys.txt`)
- `/terrain/minisign_sec` — the manifest **signing key** (see Security below)

Create an **IAM instance profile** granting: `ssm:GetParameter` on `/terrain/*`,
and (Path B only) `s3:GetObject` on the Copernicus bucket if it's requester-pays.
R2 access is via the env creds above — no IAM needed for R2 (it's Cloudflare).

## Phase 3 — The bootstrap script

Commit as `packtools/cloud/build_terrain_ec2.sh`; pass as EC2 **user-data** (or
run by hand after SSH). Skeleton:

```bash
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/terrain-build.log) 2>&1
REGIONS="us-west us-central us-east us-south alaska canada-west canada-east mexico-central-america"
EDITION=2024ed ; SRC=/data/glo30hgt

dnf install -y python3.13 python3-pip git awscli   # Amazon Linux 2023
pip install numpy boto3

git clone --depth 1 -b dev https://github.com/billmallard/pyEfis        /opt/pyEfis
git clone --depth 1 -b dev https://github.com/billmallard/makerplane-data /opt/mp
cd /opt/mp

# secrets
export R2_ENDPOINT=$(aws ssm get-parameter --name /terrain/r2_endpoint --with-decryption --query Parameter.Value --output text)
export R2_ACCESS_KEY_ID=$(aws ssm get-parameter --name /terrain/r2_access_key_id --with-decryption --query Parameter.Value --output text)
export R2_SECRET_ACCESS_KEY=$(aws ssm get-parameter --name /terrain/r2_secret_access_key --with-decryption --query Parameter.Value --output text)
aws ssm get-parameter --name /terrain/minisign_sec --with-decryption --query Parameter.Value --output text > keys/minisign.sec

# source tree  (Path A)
mkdir -p "$SRC"
aws s3 sync s3://makerplane-data/terrain-src/glo30hgt "$SRC" --endpoint-url "$R2_ENDPOINT"
# (Path B instead: python /opt/pyEfis/tools/fetch_glo30.py … && convert_glo30.py … → $SRC)

# build pyramid (parallel, all cores) + publish per region
PYTHONPATH=/opt/pyEfis/src python /opt/pyEfis/tools/build_terrain_mips.py "$SRC"
for r in $REGIONS; do
  PYTHONPATH=. python -m packtools.cli make-terrain "$SRC" --edition "$EDITION" --only "$r" \
      --url-base https://navdata.aerocommons.org/packs --upload --no-compress \
      --bucket makerplane-data --sec keys/minisign.sec \
    && rm -f work/packs/terrain-"$r"-"$EDITION".pack || echo "FAILED $r"
done

# verify + wipe key + self-terminate
curl -s https://navdata.aerocommons.org/manifest.json > /tmp/m.json
PYTHONPATH=. python -m packtools.cli verify /tmp/m.json --pub keys/minisign.pub
shred -u keys/minisign.sec
shutdown -h now      # launch with InstanceInitiatedShutdownBehavior=terminate
```

Note the manifest race: don't run this while the nightly navdata cron rewrites
the manifest (both upsert it). Pick a window, or add a lock.

## Phase 4 — First run + verify

Launch a spot c7i.4xlarge with the IAM profile, 250 GB gp3, terminate-on-shutdown,
user-data = the script. Watch `/var/log/terrain-build.log` (SSM Session Manager or
CloudWatch Logs). Success = manifest shows 8/8 terrain regions grown + `signature
OK`, exactly the check we ran by hand. Instance self-terminates.

## Phase 5 — One-command launch (ergonomics)

Wrap Phase 4 in `packtools/cloud/launch_terrain_build.sh` (a single `aws ec2
run-instances` with the AMI, instance-profile, spot options, block-device, and
`--user-data file://build_terrain_ec2.sh`). Then a full republish is one command.

## Phase 6 — Later graduations

- **Path B (cloud-native):** replace the Phase-1 tree + the `aws s3 sync` step with
  `fetch_glo30.py` + `convert_glo30.py` pulling Copernicus (`s3://copernicus-dem-30m/`)
  on the instance. Removes the workstation from the loop entirely; more upfront
  scripting to wire the fetch/convert into the job.
- **GHA trigger:** a `workflow_dispatch` job that assumes an AWS role and calls
  `run-instances` — gives the "click a button in GitHub" UX without running the
  bulk work on GHA's tiny runners. Reuses the CLOUDFLARE/AWS token patterns from
  `docs/environments.md`.
- **Overlap build+upload:** `make-terrain` serializes build-zip-then-upload per
  region (network idle during build, disk idle during upload). Pipeline the next
  region's build against the current upload for ~2× wall-clock. (Also applies to
  multipart tuning — boto3 already uses 10-thread multipart, so no single-stream
  cap.)

## Security — the signing key

The manifest signing key (`minisign.sec`) must be on the instance to `--upload`
(publish signs in one shot). Mitigations, in order of the v1 plan: SSM SecureString
(encrypted at rest, IAM-scoped), ephemeral instance, `shred` after use, terminate.
**Stronger option (later):** split `publish()` so EC2 only uploads packs (packs are
content-addressed by sha256 — no key needed) and the manifest sign+publish runs on
a locked-down signer (workstation or a tiny separate step). Keeps the key off the
cloud entirely; costs one small refactor.

## Cost per full-NA rebuild

Compute ~$0.5–1 (spot, 1–2 h) · EBS pennies · **egress EC2→R2 ~152 GB × $0.09 ≈
$14** (R2 ingress free) → **~$15–20**, done in well under an hour regardless of
home link. Incremental (a few regions) far less. Hetzner/DO alternative trades the
$14 egress for near-zero at the cost of the same-cloud Copernicus pull.
