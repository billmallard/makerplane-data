# The signing key: custody, rotation, revocation, compromise

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** added 2026-11-18 (AER-5994, CERT16). Safety and Compliance
reviews this procedure. The setting in step 1 below is not yet in place;
until it is, the key is still a repository secret that any branch can read.

One ed25519 key, in minisign format, signs `manifest.json` and
`advisories.json`. A device trusts a pack because that key signed the catalog
that lists the pack's sha256. Whoever holds the key can make every device
install anything. Everything below follows from that.

## Who holds it

| Copy | Where | Who can use it |
|---|---|---|
| Offline original, `keys/minisign.sec` | Bill Mallard's own storage and his offline backup | Bill |
| CI copy, `MINISIGN_SECRET_KEY` | the `signing` Environment of `billmallard/makerplane-data` | the jobs listed below, and only when run from `main` |

No agent, reviewer, or other maintainer holds a copy. The secret is never
printed, committed, or pasted into an issue. One custodian is a single point of
failure: if Bill's copy and its backup are lost, recovery is the same as a
compromise (below). A second offline holder is recommended but has not been
appointed.

The jobs that read the CI copy are `cyclical.yml` build, `roads.yml` build,
`water.yml` build, `manifest-admin.yml` admin, and `advisories.yml` publish.
Each names `environment: signing`, and `tests/test_signing_environment.py`
fails if a job reads the secret without naming it, or if a new signing
workflow appears.

## The keys a device trusts

The device trusts a small set, `PUBLIC_KEYS` in `pyefis_data/cli.py`, and
accepts a catalog signed by any key in it. The signature names its key id, so
the device picks the key by id. A signature by a key outside the set is
refused, and nothing installs (`tests/test_key_set.py`).

| Slot | Key id | Public key file | State |
|---|---|---|---|
| current | `178caefeabc5afb1` | `keys/minisign.pub` | signs today |
| next | none yet | `keys/minisign-next.pub` (when made) | not generated |

The public key is also copied, for people checking by hand, into
`site/assets/inspect-core.js` (the pack inspector) and `packtools/reportsite.py`
(the verification page). A rotation updates all four places.

## Step 1: put the key in the Environment (once, a repository admin)

An agent's token cannot create Environments (HTTP 403), so this step is
Bill's. The first three commands are safe at any time: a job that does not
name the Environment keeps using the repository secret. Delete the repository
secret only once the workflows that name `signing` are on `main` and a run has
passed. If the Environment does not exist when a job first names it, GitHub
creates one with no branch rule, and the key still comes from the repository
secret.

```bash
R=billmallard/makerplane-data
gh api -X PUT repos/$R/environments/signing --input - <<'EOF'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
EOF
gh api -X POST repos/$R/environments/signing/deployment-branch-policies -f name=main -f type=branch
gh secret set MINISIGN_SECRET_KEY --repo $R --env signing < keys/minisign.sec
# After the next scheduled cyclical run on main succeeds with the Environment:
gh secret delete MINISIGN_SECRET_KEY --repo $R
```

Check: `gh api repos/$R/environments/signing/deployment-branch-policies`
lists only `main`; `gh secret list --repo $R` no longer lists the key;
`gh secret list --repo $R --env signing` does.

What it then holds: a run on any other branch that names the Environment is
refused before its first step, and a job that does not name it gets an empty
secret. What it does not hold:

- Anyone who can push to `main` can still change a signing workflow.
- The signing job checks out pyEfis at `PYEFIS_REF` (`dev`) and runs its
  builders in the same job that holds the key. A change on pyEfis `dev` can
  read the key. Splitting signing into its own job is a later change.
- The R2 write credentials are still repository secrets. They cannot sign,
  so a device refuses what they alone can write, but they can delete packs.

A consequence: `water.yml` and `manifest-admin.yml` have been dispatched from
`dev` before. After step 1 they run from `main` only, as do the dry runs of
`advisories.yml` and the states test-runs of `roads.yml`. Try a change to a
signing workflow locally with `packtool`, or on `main` with publishing off.

## Rotation (planned)

1. Bill generates the next pair offline:
   `packtool genkey --out keys-next --comment "makerplane-data signing key (next)"`,
   and backs up its `.sec` as he did the current one.
2. A PR commits `keys/minisign-next.pub`, adds it as the second entry of
   `PUBLIC_KEYS`, and fills in the key id in the table above. Release it.
3. Wait until the devices that matter carry that release. We cannot see device
   versions; the wait is Bill's call.
4. Cut over: `gh secret set MINISIGN_SECRET_KEY --repo $R --env signing < keys-next/minisign.sec`,
   then re-sign (the next cyclical run, or `manifest-admin.yml`). A device
   that has the new release accepts it. One that does not refuses it, keeps
   its last verified catalog, and its data goes stale. It never installs
   something it cannot verify.
5. In a later release, next becomes current (`keys/minisign.pub`), the old key
   leaves `PUBLIC_KEYS`, and a new next is generated. Destroy the old `.sec`
   once no device needs the old key.

## Revocation, and the response to a compromise

Revocation is removing a key from `PUBLIC_KEYS` and releasing `pyefis_data`.
It reaches a device only when that device's software is updated. That update
is outside the signed chain, so until it happens the device still trusts the
revoked key.

If the key may be compromised (a leaked log, a suspect workflow run, a lost
backup):

1. **Stop our own signing.** Delete the Environment secret. That stops our
   pipeline publishing with the key; it does not stop whoever else has it.
2. **Switch keys.** If next exists and was not exposed, do rotation step 4
   now. If there is no next, or it was exposed too, generate a new pair and
   release it at once. Every device then needs a software update before it
   trusts anything new.
3. **Revoke.** Release `pyefis_data` with the compromised key removed from
   `PUBLIC_KEYS`, and tell builders to update. Do not rely on the advisory
   feed to tell them: it is signed with the same key.
4. **Close the other doors.** Rotate the R2 credentials
   (`docs/cloudflare_setup.md`), because a signature alone does nothing
   without a way to serve it.
5. **Find what was signed.** Every pack a workflow here built and published
   has a build provenance attestation (`docs/provenance.md`), signed by
   Sigstore, not by our key; a staged water publish is the one exception. A
   catalog entry whose pack has no attestation from our workflow, or whose
   `build_run_url` is not a run of this repository, is suspect.
6. Write up what happened in the issue, with the exposure window.

The real limit is step 3: a revocation that depends on each device's
software update. A self-certifying key chain (each new key set signed by the
old one, as The Update Framework's root role does) would let keys rotate and
be revoked through the signed catalog itself. That is a proposal, not built.

## Who built each signed pack

Each catalog entry names, beside its sha256: `build_commit`, the
makerplane-data commit that built it; `build_run_url`, the workflow run that
built and signed it (the run id is the last segment); and `pyefis_commit`, the
pyEfis commit whose tools built it (CERT5a). All three come from the pack's
test record, which the signature binds by `report_sha256`. Entries published
before a field existed leave it empty.
