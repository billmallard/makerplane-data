# Build provenance: who built a pack

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** added 2026-10-31 (AER-4346, testing-visibility item TV10). It
takes effect from the first publishing run after it reaches `main`; the
nightly build and the dispatched builds run the workflow files on `main`.

The signed catalog (`manifest.json` + `.minisig`) proves a pack is the one we
published: its signature checks against `keys/minisign.pub` and the pack's
sha256 matches its entry. It does not say *how* the pack was made. A build
provenance attestation adds that, and it does it independently of our key.

## What an attestation says

When a GitHub Actions workflow in this repository publishes a pack, a second
job in the same run signs a [SLSA build provenance](https://slsa.dev/provenance/v1)
statement for it with [`actions/attest-build-provenance`](https://github.com/actions/attest-build-provenance).
The statement names the pack by its sha256 and records:

- the repository and the workflow file that ran (`.github/workflows/roads.yml`, ...);
- the commit and the git ref the run was started from;
- the run itself (its URL), and that it ran on a GitHub-hosted runner.

It is signed with a short-lived certificate that Sigstore issues to that one
workflow run, from GitHub's OIDC token, and logged in Sigstore's public
transparency log. Neither our minisign key nor anyone holding it can produce
one, and an attestation cannot be edited after the run.

What it does **not** say: that the data is correct, current, or fit for any
use. It says which public code, at which commit, produced these bytes. Whether
the pack passed its tests is the test report's job (`docs/pack_report.md`).

## Which packs have one

| Workflow | Pack ids | Attested |
|---|---|---|
| `cyclical.yml` (nightly) | `airports-conus`, `navaids-conus`, `obstacles-conus`, `procedures-conus` | every edition it publishes |
| `roads.yml` (dispatch) | `highways-conus` | every edition it publishes |
| `water.yml` (dispatch) | `water-conus` | every edition it publishes |

Only a pack the publish gate accepted, and that is listed in the signed
catalog that run uploaded, is attested. A refused pack is not.

No attestation exists for:

- **Terrain** (`terrain-*`). It is built on our own build machine from a
  private repository. GitHub issues these attestations from private
  repositories only on its paid Enterprise Cloud plan, so terrain packs carry
  none. Their trust rests on the signed catalog and their test record. Moving
  the build to a public workflow, or a plan change, would lift this.
- **Packs built on a workstation**, not by a workflow: today `water-na` and
  `airports-canada`.
- **Editions published before this change.** An attestation can only be made
  by the run that built the pack; it is not added later.
- **Catalog edits** by `manifest-admin.yml`. It re-signs the catalog and
  builds no pack.

## Check one yourself

You need the [GitHub CLI](https://cli.github.com) (2.49 or newer), signed in
to any GitHub account (`gh auth login`); it fetches the attestation from
GitHub's API.

Download the pack (the catalog's **Download** button, or its URL in
`manifest.json`), then:

```bash
gh attestation verify highways-conus-2026q3r1.pack \
  --repo billmallard/makerplane-data \
  --signer-workflow billmallard/makerplane-data/.github/workflows/roads.yml \
  --deny-self-hosted-runners
```

It hashes the file, looks up attestations for that sha256, checks the
Sigstore signature and certificate chain, and checks that the signer was that
workflow in that repository. It exits nonzero if any of that fails, or if no
attestation exists for the file. Use the workflow from the table above for
the pack you are checking (`cyclical.yml` for airports, navaids, obstacles
and procedures; `water.yml` for `water-conus`).

To see what it recorded, the commit and ref the run started from among it:

```bash
gh attestation verify highways-conus-2026q3r1.pack --repo billmallard/makerplane-data \
  --format json --jq '.[0].verificationResult.signature.certificate
    | {sourceRepositoryRef, sourceRepositoryDigest, runInvocationURI}'
```

`sourceRepositoryDigest` is the commit; `git show <it>` in a clone is the code
that built the pack. The nightly build runs from `main`, so its attestations
carry `refs/heads/main`. A dispatched roads or water build carries whichever
ref it was started from; to require `main`, add `--source-ref refs/heads/main`.

Only the certificate fields and the timestamps are outside the workflow's
control. The statement's `predicate` is written by the run and is only as
trustworthy as the run (`gh help attestation verify` explains this).

## How it is wired

- `run_cyclical --published-checksums FILE` writes one sha256sum line per pack
  it put in the signed catalog, after the catalog is signed and uploaded
  (`packtools/run_cyclical.py`). Roads and water hash their one pack after the
  `build-pack --upload` step succeeds.
- The build job passes those lines out as a job output. A separate `attest`
  job, the only job with `id-token: write` and `attestations: write`, signs
  them (`subject-checksums`). Keeping it separate means the OIDC token that
  can sign an attestation is never in reach of the build steps, which run
  build tools checked out from pyEfis.
- A red cyclical run still attests the packs it did publish: a hard failure on
  one source does not stop the healthy ones shipping (`PipelineFailure`).
- GitHub Actions attestations are free for public repositories.
