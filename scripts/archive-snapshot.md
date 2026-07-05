# Evidence-trail snapshots (Wayback Machine + tags)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Part of the project's defensive-publication posture (AC-IP-001 Task 5):
public, third-party-timestamped copies of what we disclosed and when, so
prior art is easy for anyone — including a patent examiner — to verify.

## After every significant publication

Submit the published page(s) to the Internet Archive's Wayback Machine.
No account needed:

```bash
# one page
curl -s "https://web.archive.org/save/https://navdata.aerocommons.org/" > /dev/null

# the usual set
for u in \
  "https://navdata.aerocommons.org/" \
  "https://navdata.aerocommons.org/manifest.json" \
  "https://pyefis.aerocommons.org/" \
  "https://github.com/billmallard/makerplane-data/blob/main/docs/AC-DP-001-architecture-disclosure.md" \
  ; do curl -s "https://web.archive.org/save/$u" > /dev/null && echo "saved $u"; done
```

Verify at `https://web.archive.org/web/*/<url>`. Do this whenever:

* the defensive disclosure (AC-DP-001) or any AC- design doc changes
  substantively;
* a new pack kind or major capability goes live on the site;
* a release tag is cut.

(A human runs this deliberately — it publishes to a third-party archive.
Keep it out of CI until the disclosure set stabilizes; a CI step using the
SPA API can be added then.)

## Tags

* Release/baseline tags are **annotated** tags on `main` or the active
  release branch; never retagged or force-moved.
* **Signed tags going forward:** once Bill generates a signing key
  (`git config user.signingkey <keyid>`; GPG or SSH signing both fine),
  cut tags with `git tag -s`. GitHub shows them "Verified".
* Baseline precedent: `ip-baseline-20260704` marks the state at licensing
  remediation (AC-IP-001 Tasks 1–2).
* History rewrites on tagged/release branches are prohibited
  (CONTRIBUTING.md) — the git record is part of the evidence trail.
