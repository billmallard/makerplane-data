# License audit — AeroCommons avionics stack

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** complete (AC-IP-001 Task 1). Audited 2026-07-04 from primary
sources: LICENSE/COPYING files, per-file license headers, and git history.
**License:** this document CC BY 4.0.

## Headline findings

1. **There is no GPLv2-only code anywhere in the stack.** Every upstream
   MakerPlane-hosted component (pyEfis, FIX-Gateway, pyAvTools,
   can-fix-arduinolib, pyAvMap) ships the GPLv2 text in COPYING/LICENSE, but
   the per-file headers uniformly read "either version 2 of the License, or
   (at your option) any later version" — i.e. **GPL-2.0-or-later**. Derived
   work may therefore be taken at GPLv3, which accepts Apache-2.0 code in one
   direction. The GPLv2-only/Apache-2.0 incompatibility flagged in AC-IP-001
   does not arise.
2. **Provenance:** the core stack is the work of **Phil Birkelbach**
   (first commits: pyEfis 2013-06-26, FIX-Gateway 2014-09-02, canfix-spec
   2018-05-02, pyAvTools 2019-02-06), hosted under the `makerplane` GitHub
   org. He is the primary upstream copyright holder; the CAN-FIX protocol is
   his design.
3. **One derived-work finding inside this repo:** the configurator's
   instrument preview twins in `configurator/public/editor.html` are
   documented ports of pyEfis rendering code (`renderSVS()` is a port of
   `svs.py`/`svs_gl.py`; the widget twins reproduce the GPL widgets'
   drawing logic). A port is a derived work, so that file cannot be
   Apache/AGPL-only — it is licensed **GPL-3.0-or-later** (an upgrade
   permitted by upstream's "or later", chosen for AGPL-3.0 §13
   compatibility with the rest of the configurator).
4. **canfix-spec has no license file** (upstream or fork). The fork
   republishes the spec via GitHub Pages on legally undefined terms.
   Resolution requires the upstream author (Phil) — being handled by Bill
   directly, outside this repo.

## Component classification

| Component | Upstream dependency | Classification | License obligation | License applied |
|---|---|---|---|---|
| `billmallard/pyEfis` fork (SVS, moving map, editor/schema, instruments, tools) | makerplane/pyEfis (GPL-2.0-or-later) | (a) derived | stay GPL-2.0-or-later | **GPL-2.0-or-later** (inherited; house-style headers) |
| `billmallard/fix-gateway` fork (xplane plugin work, schema exporter, future webserver *if in-process*) | makerplane/FIX-Gateway (GPL-2.0-or-later) | (a) derived | stay GPL-2.0-or-later | **GPL-2.0-or-later** (inherited) |
| `pyAvTools`, `can-fix-arduinolib`, `pyAvMap` | upstream (GPL-2.0-or-later) | (a) upstream, minimally touched | stay GPL-2.0-or-later | **GPL-2.0-or-later** (inherited) |
| `canfix-spec` fork (spec + Pages build) | Phil Birkelbach, **no license file** | (a) upstream document | undefined — needs upstream grant | **flagged**; Bill ↔ Phil resolving |
| makerplane-data `packtools/` (pack build pipeline, formats, signing) | none — communicates with pyEfis tools via **subprocess CLI** (`PYEFIS_TOOLS_DIR`), a mere-aggregation boundary | (b)/(c) independent | none flow in | **Apache-2.0** |
| makerplane-data `pyefis_data/` (on-device updater, status.json contract) | none — talks HTTP to R2, writes files pyEfis reads | (b) independent | none | **Apache-2.0** |
| makerplane-data `site/`, `tests/`, `scripts/` | none | (c) greenfield | none | **Apache-2.0** |
| makerplane-data `configurator/` (Cloudflare Worker, auth, D1/R2, dashboard, aircraft params UI) | none — serves HTTP; devices pull over HTTPS | (c) greenfield service | none | **AGPL-3.0-or-later** |
| `configurator/public/editor.html` (instrument twins) | pyEfis widget rendering code, **ported** | (a) derived | GPL family required | **GPL-3.0-or-later** (per-file SPDX) |
| `docs/` here + AC-/MAOS- design docs | n/a | documentation | n/a | **CC BY 4.0** |

Classification key (from AC-IP-001): (a) derived work of GPL upstream;
(b) independent work that merely communicates with GPL code over a
network/CLI/file boundary; (c) fully greenfield.

## Boundary analysis (why the (b) classifications hold)

* **CAN-FIX / netfix TCP:** programs exchanging data over the bus or the
  netfix socket are separate programs, not a combined work. Third-party
  providers and gateway peers may use any license.
* **`PYEFIS_TOOLS_DIR` subprocess:** packtools invokes pyEfis build scripts
  as `python <script> <args>` child processes and consumes their sqlite
  output. Exec-and-exchange-files is the canonical mere-aggregation
  boundary; packtools stays Apache-2.0. (Vendoring or importing those
  scripts would change this — do not.)
* **status.json / config packs:** pyefis_data writes files; pyEfis reads
  them. Data interchange, not linking.
* **The one deliberate crossing:** the editor twins (above). The fidelity
  rule *requires* porting pyEfis drawing logic, so this file is and will
  remain GPL; the SPDX header and NOTICE make the boundary explicit inside
  an otherwise AGPL directory. AGPL-3.0 §13 / GPL-3.0 §13 expressly permit
  conveying the combined service.

## License selection rationale (decided with Bill, 2026-07-04)

* **Apache-2.0 for interfaces, formats, SDKs** (packtools, pyefis_data,
  pack format, provider contracts): the explicit patent grant plus
  patent-retaliation clause is the point — third parties can build
  commercial hardware/providers against these boundaries without copyleft
  exposure, which is how the ecosystem leaves room for others to earn.
* **AGPL-3.0-or-later for the hosted services** (configurator, cloud
  delivery): prevents platform capture — anyone operating a modified copy
  as a service must publish their changes. Third-party commercialization
  happens *against* the Apache interfaces, not by forking the platform.
* **GPL-2.0-or-later where inherited** (never relicensed by us).
* **CC BY 4.0 for documentation** (CC licenses carry no patent language and
  are not suitable for code).
* Inbound = outbound, certified per commit via the **DCO**
  (see CONTRIBUTING.md). No CLA; consequently no dual-licensing of the
  AGPL components once outside contributions land — a deliberate,
  eyes-open trade in favour of community trust.

## Sourced third-party pack *data* (tracked separately from repo code)

The classification above is the repo's own code. Packaged **data** carries
its own terms per source, declared in each pack's `attribution` string
(`packtools/sources.py`); this section is the running record of what's been
checked, per AC-IP-001's remit to catch exactly this class of decision.

| Source | Layer | License | Redistribution? |
|---|---|---|---|
| FAA NASR/DOF/CIFP | airports, obstacles, navaids, cifp | US Government work — no copyright attaches (not a license grant) | Yes — public domain (`license=LicenseRef-us-public-domain`) |
| Copernicus GLO-30 | terrain | Copernicus open data terms | Yes — redistributable |
| OpenStreetMap (via Geofabrik) / Natural Earth | water, highways | ODbL / public domain | Yes — ODbL requires attribution, carried in `attribution` and explicitly in `license`/`license_url` (`ODbL-1.0`) |
| NavCanada VNC | (Canadian charts) | proprietary, user-supplied only | **No** — never redistributed; user-supplied pack slot only |
| **openAIP** (Garrecht Avionik GmbH) | airspace (evaluated, not yet shipped) | **CC BY-NC 4.0**, confirmed live against openAIP's own API metadata 2026-08-23 | **Evaluated: yes, under its own terms.** Free, no-revenue distribution reads as non-commercial under CC's own NC definition; carry-not-relicense via per-pack `license`/`license_url` metadata (AER-546, additive fields on `PackMeta`/`Source`, landed 2026-09-03). Full writeup: [openaip_evaluation.md](openaip_evaluation.md). Awaiting Bill's decision on direction + whether to contact openAIP directly to confirm. |

`PackMeta` (`packtools/packmeta.py`) and `Source` (`packtools/sources.py`) now
carry additive `license`/`license_url` fields alongside `attribution` (AER-546):
`attribution` stays presentation text, `license` is a short machine-checkable
tag (SPDX id, `LicenseRef-*`, or e.g. `ODbL-1.0`), `license_url` the canonical
link. The catalog manifest (`packtools/manifest.py` `PackEntry`) does not yet
carry them through to the Pi — that's for the openAIP builder work to add
alongside the airspace `kind`.

## Enforcement

* Per-file `SPDX-License-Identifier` headers on all source files in this
  repo (applied 2026-07-04; `scripts/spdx_check.py --fix`).
* CI fails on new files without SPDX headers (`ci.yml` → spdx step) and on
  PR commits without DCO sign-off (`ci.yml` → dco job).
* Relicensing note: makerplane-data was MIT until 2026-07-04 with a single
  author (Bill Mallard, 142/142 commits), so the change to
  Apache-2.0/AGPL-3.0 required no third-party consent. Prior MIT-licensed
  snapshots remain MIT for anyone who obtained them — the new licenses
  govern this line of development forward.
