# Terrain build via a GitHub Actions self-hosted runner (on the QNAP)

Status: PLAN (2026-07-10). Deferred — pick up when there are cycles. Turns the
`docker compose up` terrain build into a **GitHub-triggered** pipeline that runs
on the **QNAP as a self-hosted runner**. Companion to `docs/terrain.md`,
`docs/terrain_cloud_build_plan.md` (the AWS alternative), and the containerized
build already shipped in `packtools/cloud/`.

## Why this shape

Plain GitHub Actions was ruled out because its cloud runners have ~14 GB disk and
can't hold this workload. A **self-hosted runner runs the job on your hardware**
(the QNAP V1500B, 4c/8t, 32 GB, with the tile tree already local), so that limit
disappears. You keep GitHub's orchestration — a "Run workflow" button, centralized
logs, and **GitHub-managed secrets** (better than the `.env` on the NAS today) —
while the heavy lifting stays free, local, and on your uplink for the push.

It's the same three-step pipeline that `packtools/cloud/entrypoint.sh` runs today
(build mips → `make-terrain --upload` per region → verify), just triggered and
logged through GitHub instead of `docker compose`.

## ⚠️ Decide first: repo visibility (security gate)

**Self-hosted runners on PUBLIC repos are dangerous** — a forked pull request could
run arbitrary code on your NAS (GitHub warns about this explicitly). Before wiring
this up, confirm whether `billmallard/makerplane-data` is public or private:

- **Private repo** → self-hosted runner is safe; proceed normally.
- **Public repo** → lock it down: **`workflow_dispatch`-only** (NO `pull_request`
  trigger), enable "Require approval for all external contributors," and consider a
  dedicated private mirror/repo that holds only the terrain workflow + runner. Never
  let fork PRs reach this runner.

This decision shapes Phase 4's triggers, so settle it first.

## Phases

### Phase 1 — Register the QNAP as a self-hosted runner
- GitHub → repo **Settings → Actions → Runners → New self-hosted runner** → yields a
  registration token + `./config.sh` command.
- Run the runner **as a container in Container Station** (fits the existing setup).
  Options: the community `myoung34/github-runner` image, or GitHub's official runner
  image. Key config (env): `REPO_URL`, `RUNNER_TOKEN` (or an `ACCESS_TOKEN` PAT for
  self-renewing registration), `RUNNER_NAME=qnap-terrain`, `LABELS=qnap,terrain`,
  restart policy `unless-stopped` so it survives reboots.
- **No Docker-in-Docker needed** — the runner runs the Python pipeline directly, so
  it doesn't need the docker socket. It only needs python3 + `numpy boto3 PyNaCl
  PyYAML` (install per-job, or bake a small custom image `FROM myoung34/github-runner`).
- **Mount the tile tree** into the runner container, e.g.
  `/share/ZFS22_DATA/pyEfisData/EarthData:/data` (native pool path — same gotcha as
  `packtools/cloud/README.md`). The workflow points `TILE_ROOT` at `/data/glo30hgt`.

### Phase 2 — Store secrets in GitHub
Add as **repo Actions secrets** (Settings → Secrets and variables → Actions):
`R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (from *CloudFlare R2 Bucket
Keys.txt*) and `MINISIGN_SECRET_KEY` (base64 from `keys/minisign.sec`). These replace
the on-NAS `.env` entirely. (Note: the existing `CLOUDFLARE_API_TOKEN` secret is for
Worker deploys — the R2 terrain creds are separate.)

### Phase 3 — Replace the `terrain.yml` stub with a real workflow
`.github/workflows/terrain.yml` is currently a deliberate no-op. Rewrite it:

```yaml
name: terrain-packs
on:
  workflow_dispatch:
    inputs:
      edition: { default: "2024ed" }
      regions: { default: "us-west us-central us-east us-south alaska canada-west canada-east mexico-central-america" }
      jobs:    { default: "8" }
jobs:
  build:
    runs-on: [self-hosted, qnap]         # the NAS runner, not a GitHub cloud runner
    steps:
      - uses: actions/checkout@v4        # makerplane-data
      - uses: actions/checkout@v4
        with: { repository: billmallard/pyEfis, ref: dev, path: pyEfis }
      - run: pip install --quiet numpy boto3 "PyNaCl>=1.5" "PyYAML>=6.0"
      - run: python pyEfis/tools/build_terrain_mips.py "$TILE_ROOT" -j "${{ inputs.jobs }}"
      - run: |
          for r in ${{ inputs.regions }}; do
            python -m packtools.cli make-terrain "$TILE_ROOT" --edition "${{ inputs.edition }}" \
              --only "$r" --out "$RUNNER_TEMP/packs" --no-compress --upload \
              --url-base https://navdata.aerocommons.org/packs --bucket makerplane-data \
              && rm -f "$RUNNER_TEMP/packs/packs/terrain-$r-${{ inputs.edition }}.pack"
          done
      - run: |
          curl -fsS https://navdata.aerocommons.org/manifest.json -o /tmp/m.json
          python -m packtools.cli verify /tmp/m.json --pub keys/minisign.pub
    env:
      TILE_ROOT: /data/glo30hgt
      R2_ENDPOINT:          ${{ secrets.R2_ENDPOINT }}
      R2_ACCESS_KEY_ID:     ${{ secrets.R2_ACCESS_KEY_ID }}
      R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
      MINISIGN_SECRET_KEY:  ${{ secrets.MINISIGN_SECRET_KEY }}
```

Inputs let you re-publish a subset of regions or a different edition from the UI.
`build_terrain_mips` is idempotent, so the mip step is fast on re-runs (pyramid
persists in the mounted tree).

### Phase 4 — First dispatch + verify
Actions tab → **terrain-packs → Run workflow**. Watch the live log; success = the
verify step prints `signature OK` and all 8 `terrain-*` entries updated. Same check
as the manual run, now in GitHub.

### Phase 5 — Ops
- Runner auto-starts via the container's `unless-stopped` policy; it shows Idle/Active
  in Settings → Actions → Runners.
- Keep the runner image/agent updated periodically (GitHub deprecates old agents).
- **Don't** trigger while the nightly navdata cron publishes (both rewrite the
  manifest) — pick a window, or add a lock.
- Optional later: a `schedule:` trigger, or `on: push` filtered to terrain-relevant
  paths, once you trust the flow.

## Relationship to the other plans
This is the **cheaper alternative** to `terrain_cloud_build_plan.md` Phase 6 (which
had GHA *launch an EC2 box*). Here GHA just *dispatches to the QNAP runner* — no cloud
compute, no egress bill, uses hardware you own. Reuses the exact `packtools`/
`build_terrain_mips` pipeline. If you ever outgrow the NAS (speed, or wanting the push
off your home uplink), the EC2 plan is the escalation; the workflow steps are nearly
identical, only `runs-on` changes.
