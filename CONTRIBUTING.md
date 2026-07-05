# Contributing

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Thanks for helping build open avionics. This repo holds the AeroCommons
data tools and the pyEfis configurator; the display itself lives in
[pyEfis](https://github.com/makerplane/pyEfis) and the data hub in
[FIX-Gateway](https://github.com/makerplane/FIX-Gateway).

## Licensing of contributions (read once, it matters)

**Inbound = outbound.** Your contribution is accepted under the license of
the file(s) you touch — this repo is multi-licensed by component
(Apache-2.0 for the data tools and interfaces, AGPL-3.0-or-later for the
configurator service, GPL-3.0-or-later for the pyEfis-derived preview
code). The `SPDX-License-Identifier` header at the top of each file is
authoritative; [docs/LICENSE-AUDIT.md](docs/LICENSE-AUDIT.md) explains the
map. New source files must carry the correct SPDX header — CI rejects
files without one (`python scripts/spdx_check.py --fix` adds them).

**Every commit must be signed off** (Developer Certificate of Origin —
the Linux kernel model, text in [DCO](DCO)). Sign-off certifies you have
the right to submit the work under the file's license, which is what
keeps the project's patent and copyright posture clean without CLA
paperwork:

```bash
git commit -s -m "your message"        # adds: Signed-off-by: Your Name <you@example.com>
```

Use your real name and a reachable email. Forgot one? `git commit --amend -s`
or `git rebase --signoff <base>` before pushing. CI fails PRs whose
commits lack a `Signed-off-by:` line.

## History and release integrity

* **No history rewrites on `main` or release branches.** Force-pushes to
  those branches are never acceptable; the commit record is part of the
  project's public prior-art evidence trail.
* Release tags are annotated (and, going forward, signed) — see
  [scripts/archive-snapshot.md](scripts/archive-snapshot.md) for the
  publication-snapshot practice.

## Getting started

1. Pick an issue labeled **`good-first-issue`** (or open an issue to
   discuss something bigger before you build it).
2. Comment on the issue so it's yours; nobody likes duplicate work.
3. Branch from `main`, keep commits focused (one logical change per
   commit — this project values commit-history granularity), sign off,
   open a PR.
4. `pip install -e .[dev]` then `python -m pytest` must pass; the
   configurator deploys with `npx wrangler deploy` (maintainers do
   production deploys).

Conventions: no emojis in code or commit messages; match the surrounding
code's style; tests accompany behavior changes.

## The provider model is the on-ramp

The stack is deliberately extensible at stable, permissively-licensed
boundaries — you can add real value without touching the core:

* **Data packs**: add a source/region in `packtools/` (see
  `packtools/ourairports.py` for the pattern: one builder, one `Source`
  registration, tests against fixtures). The navaids pack
  (`packtools/sources.py`, multi-archive) is a worked example.
* **pyEfis map layers / instruments**: the moving map's `MapLayer`
  registry and the instrument registry in `screenbuilder_factory.py`
  (pyEfis repo, GPL) take self-contained additions well.
* **Hardware / gateway providers**: anything that speaks CAN-FIX or the
  netfix TCP protocol is an independent program — your license, your
  product. The interfaces are the contract; see
  [docs/AC-DP-001-architecture-disclosure.md](docs/AC-DP-001-architecture-disclosure.md).

## Questions

Open a GitHub issue, or for MakerPlane-wide topics use the MakerPlane
community forum.
