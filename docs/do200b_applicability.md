# DO-200B / AC 20-153B applicability — research brief

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** research and writing only, 2026-09-07. Answers `AER-699`. Nothing
here commits the project to seeking any FAA acceptance, and no pipeline code
changed as part of this brief — every recommendation below is a proposal for
a separate follow-on issue.

## Bottom line

**DO-200B / AC 20-153B binds us to nothing today, and there is no plausible
path that changes that without this project (or a downstream integrator)
first pursuing a TC, STC, or TSO — a decision this brief does not make and
that belongs to `MAOS-DESIGN/docs/AVIONICS_STACK_ROADMAP.md`.** AC 20-153B is
FAA guidance that exists to help someone *else's* certification requirement
get satisfied; we have no certification requirement for it to attach to.

That said, roughly two-thirds of what DO-200B actually asks for — traceable
origin, integrity end to end, currency/expiry handling, error detection at
ingest — is data hygiene we've substantially already built for our own
reasons (the signed manifest, `schema_guard`, `cycles.py`), and the rest
splits cleanly into one real, cheap gap worth closing (a machine-readable
pedigree label distinguishing federal from community-sourced data — see
§5) and several categories of paperwork that would be pure ceremony for a
one-maintainer open-source project with no certification customer (§4).
**Recommendation: adopt the pedigree label, do nothing else, claim no
compliance.**

## Sources

Reached and read directly:
- Garmin International, *RTCA/DO-200A/B List of Applicable Avionics
  Systems*, doc 190-01999-00 Rev. Q, dated July 2026 — Garmin's own live
  Type 2 LOA disclosure (`avdb.garmin.com`), read in full. Primary vendor
  evidence for the Type 1/Type 2 distinction: it shows Type 2 LOA acceptance
  issued *per database type, per named product family, against enumerated
  TSOA/API part numbers* — i.e., tied to a specific certificated end item,
  not a generic process acceptance.
- Universal Avionics, "Type 2 LOA Status" page (`universalavionics.com`) —
  vendor's own LOA disclosure, read directly. Confirms the same Type 2
  pattern and states plainly that "the end user has the ultimate
  responsibility to ensure the data requirements are met" — the
  supplier/user shared-responsibility principle, from a supplier's own
  mouth.
- FAA's own Advisory Circular index metadata (`faa.gov/regulations_policies`
  search results) — confirms AC 20-153B publication date 2016-04-19,
  superseding AC 20-153A (2010-09-20), which superseded the original AC
  20-153 (2003); LOAs issued under earlier editions remain valid unless
  superseded/withdrawn.
- RTCA's own product page (`rtca.org/products/do-200c-electronic`) —
  confirms **DO-200C** (successor to DO-200B) released 2024-06-27. DO-200B
  itself dates to 2013.
- AFuzion (Vance Hilderman), *DO-200B Synopsis — Short Version* — a named
  author's whitepaper, reviewed per its own byline by two FAA DERs (Don
  Coleman, Tom Roth). Read in full as a PDF. Treated as **secondary**
  interpretation, not the standard itself, but higher-confidence than an
  anonymous blog because of the named-reviewer claim and internal
  consistency with every other source below.
- KEYVAN Aviation (EASA Type 1 DAT provider), EUROCAE ED-76A/ED-77 training
  course outline — read in full as a PDF. Secondary, but independently
  corroborates the same seven data-quality attributes and the
  supplier→processor→user chain shape.

Explicitly **not** reached:
- **RTCA DO-200B / DO-200C full text** — paid RTCA standard, as the issue
  anticipated. Every claim about specific clause wording below is
  triangulated from the secondary sources above, not read from the
  standard.
- **FAA AC 20-153B full PDF** — `faa.gov`'s document server returned HTTP
  403 to every automated fetch attempt in this environment (direct request
  and a web-archive mirror attempt, the latter blocked outright — this
  environment cannot reach `web.archive.org` at all). We have the AC's
  *existence, dates, and supersession chain* from FAA's own index pages,
  and its *content* only via secondary summaries and vendor documents that
  cite it. Nothing below claims to quote AC 20-153B verbatim.
- **EUROCAE ED-76A / ED-77 full text** — paid EUROCAE standards; not read.
  Relied on EUROCAE's public product-page abstracts and the KEYVAN course
  outline.
- **FAA Order 8130.2 / 8110.55 full text** — referenced in search results
  (the E-AB certification chapter, the LOA-issuance order) but not fetched
  in full. The claims below about experimental-amateur-built certification
  not requiring a TSO-equivalent standard come from FAA's own public
  "Amateur-Built Aircraft" regulatory guidance page content, not a verbatim
  read of the Order.
- **No emails, forms, or account creation were used or attempted**, per the
  guardrail — everything above is publicly reachable without contacting
  anyone.

## 1. What DO-200B actually requires, in plain terms

RTCA DO-200B, *Standards for Processing Aeronautical Data* (EUROCAE's
equivalent is ED-76A), sets minimum standards for the entire aeronautical
data chain: **the interface to a data supplier → receipt → processing →
distribution → the interface to a customer/user.** "Aeronautical data" is
deliberately broader than "data that flies" — it covers navigation,
obstacle, terrain, chart, and flight-planning data, including data that
never reaches an aircraft but still affects aviation safety (e.g.
simulators, training). It is ~77 pages, was written by a large,
multi-stakeholder RTCA committee, and is stated by its own commentators to
provide *minimum, recommended* standards, not prescriptive step-by-step
requirements — the operative philosophy, per the AFuzion synopsis, is that
"provable quality systems should outweigh strict process steps," because
the standard has to cover wildly different data types and criticality
levels with one document.

**Data Quality Requirements (DQRs).** Every data element or set is meant to
have stated: **accuracy**, **resolution**, **assurance level** (confidence
the data hasn't been corrupted), **traceability** (to its origin),
**timeliness**, **completeness**, and **format**. These DQRs are meant to
flow from the *use* the data is put to — DO-201/ED-77 ("Industry
Requirements for Aeronautical Information") is the companion standard that
defines what accuracy/resolution a given use case (e.g. a VOR, an approach
waypoint) actually needs; DO-200B is the process standard for preserving
whatever DQR was set.

**Data Process Assurance Level (DPAL).** A risk-tiered rigor scale — more
demanding tiers require more verification, more independent tool
qualification, tighter configuration control. Multiple sources describe
DPAL 1 as highest-rigor and DPAL 3/4 as lowest, with a **DPAL "2B — Basic"**
tier inserted between DPAL 2 and 3 in a later revision. Mixed-DPAL data must
either be uniformly treated at the higher tier or partitioned so the lower-
rigor data can't contaminate the higher-rigor set.

**Data Processing Requirements** — the *how*: a defined processing
procedure, configuration management of both the data and any tool that
touches it, competency management of personnel, and tool qualification
(can a transformation/verification tool itself be trusted — akin to
DO-330). DO-200B (vs. DO-200A) added explicit **data security** and
broadened the tool-qualification and cybersecurity language.

**Quality Management.** An organizational QMS proving the above is actually
followed in practice, not just documented: a Compliance Plan, a DQR
document, a Data Processing Procedures document, Tool Qualification
records, Configuration Management plan + records, a Competency Management
document, and a compliance matrix mapping requirements to evidence.

**Error detection across the chain.** The standard's practical test, at
every step of the data chain, is three questions: *could this step fail to
detect an error already present? could it insert a new error? could it
propagate an error further?* This is asked of every actor, not just the
originator.

**The supplier/user chain.** DO-200B explicitly splits responsibility
between the **data supplier** (originator/processor — accountable for the
stated DQRs and for chain-of-custody/traceability) and the **data
user/integrator** (accountable for verifying the data they receive is
compatible with, and correctly loaded into, their own equipment). Neither
role is "off the hook" for the other's mistakes — the standard is explicit
that integrity is a shared obligation.

**Type 1 vs. Type 2 Letter of Acceptance.** An FAA LOA under AC 20-153B says
the FAA finds an applicant's aeronautical-data **process** meets DO-200B's
objectives — it accepts a process, not a specific dataset's correctness.
- **Type 1 LOA**: a generic acceptance of the supplier's process, with **no
  tie to a specific aircraft, system, or equipment type**, and not required
  to be associated with any TC/STC/TSO project.
- **Type 2 LOA**: additionally demonstrates the delivered data is
  compatible with the Data Quality Requirements of a **named, specific**
  piece of certificated equipment. This is confirmed directly in Garmin's
  own applicability list above: each Type 2 LOA line item is tied to an
  enumerated product family and TSOA/API part number (e.g. G1000, GTN
  6xx/7xx) — a Type 2 LOA cannot exist independent of a certification
  project for a specific end item.

## 2. Is any of it legally required of us?

**No — and this is a precise answer, not a vibe.**

- **We hold no TC, STC, or TSO authorization, and seek none.** AC 20-153B is
  guidance written for parties pursuing FAA acceptance *in support of* a
  design-approval or operating-rule compliance requirement that already
  exists elsewhere — data suppliers to a TC/STC/TSO project, avionics
  manufacturers, or operators demonstrating compliance for certificated
  equipment. It states its own role as "an acceptable means, but not the
  only means" of showing compliance — it is not itself a standalone
  regulatory mandate. No 14 CFR provision requires DO-200B by name; it is
  only ever invoked as *evidence* for a requirement that lives in Part 23,
  Part 25, or a TSO.
- **14 CFR 21.191(g)** places amateur-built aircraft in the experimental
  category precisely because they are *not* shown to meet a type-
  certification airworthiness standard at all. FAA's own public guidance on
  amateur-built certification (and FAA Order 8130.2's E-AB certification
  chapter, per its published summary) confirms there is **no TSO-equivalent
  requirement** for equipment installed on an E-AB aircraft — a builder may
  install non-TSO'd displays, non-TSO'd GPS, or anything else, subject only
  to a DAR/FAA finding that the aircraft is safe to operate, never to a
  database-supplier acceptance chain. There is no certificated end item for
  a Type 2 LOA to attach to, and no certification-project customer for a
  Type 1 LOA to attach to either.
- **The one operating rule that reaches database currency at all — 14 CFR
  91.175(c)** and the AIM's GPS-approach guidance — reaches the
  **installed IFR-approved navigator's own database** when that specific
  unit is used to fly an instrument approach. It doesn't reach whatever
  moving-map/SVS/EFIS data feeds a supplemental display. This project
  already frames its own output that way: `docs/data_manager_strategy.md`
  states "standard experimental-avionics framing applies (MakerPlane's
  existing not-for-primary-navigation posture)... the data manager must
  never silently serve stale data as fresh — absence of an update is
  annunciated, not hidden." If a builder separately carries a certificated
  IFR GPS for actual instrument approaches, *that* unit's database comes
  from its own DO-200B-LOA-holding supplier (Garmin, Jeppesen, etc.) —
  entirely outside this repo's pipeline, and already covered by whatever
  LOA that supplier holds.

**Conclusion, stated plainly per the issue's ask: none of it bites us
today.** The only way any of it would is if a future certified variant
downstream of this data were built — a question this brief deliberately
does not answer, since the experimental/certified boundary is owned by
`MAOS-DESIGN/docs/AVIONICS_STACK_ROADMAP.md`. Flagging for Bill: if that
roadmap ever contemplates a certified consumer of these packs, DO-200B stops
being optional *at that boundary* — worth that roadmap someday pointing back
at this brief, not a reason to act on it now.

## 3. Worth adopting anyway, on engineering merit

| DO-200B concept | Our mechanism | Where | Status |
|---|---|---|---|
| Traceability to origin | `Source.attribution` (+ `license`/`license_url`) carried through `PackMeta` → `PackEntry` into the signed manifest and the on-device picker | `packtools/sources.py`, `packtools/packmeta.py`, `packtools/manifest.py` | **Have**, as prose. **Partial** as a machine-checkable fact — attribution is free text, not a structured origin/process record (see §5) |
| Integrity, end to end | ed25519 minisign chain over the manifest bytes; per-pack sha256 inside the signed manifest; the Pi verifies before trusting/caching, and verifies sha256 before the atomic swap | `packtools/signing.py`, `packtools/manifest.py` (`PackEntry.sha256`), `pyefis_data/core.py` (`Updater.fetch_manifest`, `install_pack`) | **Have**, and arguably beyond what DO-200B textually asks for — DO-200B requires "assurance," not cryptographic signing specifically. This is a place we already exceed the standard's letter |
| Currency / expiry handling | AIRAC (28-day) and DOF (56-day) cycle math; `PackEntry.covers()`/`days_until_expiry()`; on-device `PackStatus` severity (amber/expired), the "DATA" status flag concept | `packtools/cycles.py`, `packtools/manifest.py`, `pyefis_data/core.py` (`PackStatus`, `Updater.status`) | **Have** for cyclical (FAA) sources. Non-cyclical kinds (water/highways/terrain/airspace) are reissue-tagged, not date-gated — a deliberate match to those sources' own update cadence, not a gap |
| Error detection at ingest | Four-layer guard: structure-snapshot diff, required-header presence, enum-vocabulary check, output-row floor — run against the *live* download before anything is signed | `packtools/schema_guard.py` (built for the NASR 26-01 incident, `docs/nasr_2601_dpn_prep.md`) | **Have** for FAA cyclical sources (airports, navaids). **None** for community sources — `make_roads.py`/water builders have only a hard-fail-on-empty-state check (`makerplane-data#17`), not the full four-layer contract |
| Configuration management of a released data set | Manifest `upsert`/`prune_old_cycles`; the `dev → qa → main` release gate and the "new kind must merge before first publish" rule | `packtools/manifest.py`, `docs/release_process.md` | **Partial** — catalog-level control is real, but there's no record of, e.g., which exact Geofabrik snapshot / OSM changeset a given `highways-conus-2026q3r1` pack was built from, retrievable after the fact |
| Tool qualification | — | — | **None**, and not recommended (§4) — `schema_guard` is the practical substitute for the one place it would matter (FAA CSV ingestion); nothing analogous exists or is warranted for the OSM/Copernicus paths |
| Supplier/user shared responsibility | The Pi independently re-verifies a signed manifest rather than trusting the wire or a cache; a bad/interrupted transfer can never disturb live data | `pyefis_data/core.py` (`fetch_manifest`'s verify-before-cache, `install_pack`'s verify-before-swap) | **Have**, structurally — this is arguably the single DO-200B principle baked deepest into the architecture already, just never labeled as such |
| Stated per-element assurance level / pedigree | — | — | **None** — see §5, the one real gap |

## 4. Ceremony with no payoff here

- **Formal DPAL tiering with DO-330-style tool qualification** for every
  builder. `schema_guard`'s could-it-drop/mutate/miss-data test already
  buys the practical benefit; inventing a formal assurance-tier bureaucracy
  for a stack with no certification customer to present it to is process
  for its own sake.
- **A QMS / audit-trail paper program** (a written Compliance Plan,
  Competency Management document, formal QMS records). This exists in
  DO-200B to prove to an *FAA auditor* that a *process* is followed by an
  *organization*. We're inverted from that premise: one maintainer, public
  git history and PR review already are the audit trail. Writing DO-200B-
  flavored paperwork with no auditor on the other end is theater.
- **Pursuing a Type 1 or Type 2 LOA.** No certificated end item exists for
  a Type 2 LOA to attach to. A Type 1 LOA only matters to a *customer* who
  themselves needs FAA acceptance of our process; nothing downstream of us
  currently does, and pursuing one speculatively would cost real effort
  against a benefit nobody has asked for.
- **Formal DO-201/ED-77-style per-field accuracy/resolution spec sheets**
  for every data element we carry. Most of our fields inherit their
  accuracy/resolution directly from the upstream federal or Copernicus
  spec (NASR's own published tolerances, GLO-30's stated vertical
  accuracy). Re-deriving and re-publishing our own DQR document for data we
  don't originate would duplicate documentation FAA/Copernicus/OSM already
  maintain, for no reader who doesn't already have access to it.

## 5. The pedigree finding — this is the part worth acting on

Today, `attribution` (`packtools/sources.py`, `packtools/packmeta.py`) is
presentation text, and `license`/`license_url` (added AER-546/547) encode
*redistribution rights*, not *data-quality pedigree*. Nothing in the
manifest schema distinguishes:

- **FAA-originated** data (navdata, obstacles, navaids, and eventually
  cifp/charts) — a federal source with a published cycle, a real
  origination process, and (per §1) an actual DO-200B-shaped supply chain
  behind it upstream of us, even though we don't hold an LOA ourselves.
- **Community-originated** data (water, highways via OSM/Geofabrik; openAIP
  airspace, `docs/openaip_evaluation.md`, planned per AER-376) — no stated
  accuracy/resolution/assurance level, no traceability to an authoritative
  originator, quality that is whatever volunteers happened to map.

Both kinds ship through the identical sign → verify → atomic-swap pipeline,
which correctly proves **authenticity and freshness** but says nothing
about **confidence in what's inside**. A pilot (or the on-device picker)
currently cannot tell the difference without reading documentation.

**Recommendation:** an additive `origin` field on `PackMeta`/`PackEntry`,
same shape and precedent as the `license`/`license_url` rollout (AER-546/547
— additive, `""`/unspecified-safe default, no `MANIFEST_VERSION` bump). Not
a numeric DPAL score — we have no assurance program to back a number, and a
false-precision score would be worse than none. A coarse enum is enough:
e.g. `federal` / `community` / `user-supplied` (the last bucket already
exists conceptually for NavCanada VNC charts, per `docs/LICENSE-AUDIT.md`).

How far that label should travel is a product decision, not a research
conclusion — three options, ranked by cost:

1. **Manifest + docs only.** Cheapest; closes the "checkable in code" gap
   and lets CI or a future consumer assert on it.
2. **+ on-device picker label.** `pyefis_data/core.py`'s `catalog()` and
   `site/index.html` already render `attribution` per pack (per
   `docs/openaip_evaluation.md`'s own design note) — `origin` rides the
   same existing path, no new plumbing.
3. **+ in-EFIS annunciation.** A bigger, cross-repo lift into pyEfis itself,
   and probably premature before there's evidence a pilot needs the
   distinction in the cockpit rather than reading it once in a doc or a
   picker screen.

## 6. Charts specifically

`charts` is **reserved but never built**: it already appears in
`pyefis_data/core.py`'s `BULK_KINDS` and display-name table (lines 74, 94)
but is **not** in `packtools/packmeta.py`'s `KINDS` tuple — the comment
there (line 31) still reads "open for extension (charts, etc.)". FAA
VFR/IFR raster charts are federal, cycle-published data — the same pedigree
tier as navdata/obstacles, not the OSM tier — so whichever `origin` value
ships per §5 should tag charts `federal` from day one, for free, since the
builder doesn't exist yet to retrofit later.

What DO-200B thinking would add to that pipeline before it's built (not
re-proposing currency windows or stale-chart annunciation, which pyEfis's
`map_layers_roadmap.md` CH-1..CH-4 workstream already anticipates per the
issue):

- **A `schema_guard`-equivalent floor check on the fetched raster** — even
  a minimal "is this a plausible-sized image, not a 404 HTML page or an
  empty tile" check, in the same spirit as `schema_guard`'s output-floor
  and `make_roads.py`'s hard-fail-on-empty-state rule. A silently blank or
  truncated chart raster is a *more* dangerous silent failure than an empty
  CSV table, because a missing runway row is easy for a later reader of the
  data to notice, while a corrupted raster tile is often only noticed by a
  pilot looking at exactly the wrong moment.
- **Anchor traceability to the FAA-published chart edition/effective
  date** — reuse the `cycles.py` shape already built for AIRAC/DOF rather
  than inventing a new currency scheme for charts specifically.
- **Nothing about signing or configuration management needs to change** —
  once `charts` is registered in `packmeta.KINDS` and the "kind must merge
  to `main` before first publish" rule (`docs/release_process.md`) is
  followed, the existing pipeline handles a new federal cyclical kind
  exactly like navdata today.

## Recommendation summary

1. **Do not pursue any FAA LOA or a DO-200B-compliance claim.** No
   regulatory driver exists (§2), and the ceremony wouldn't pay for itself
   even taken on voluntarily (§4).
2. **Adopt, additively:**
   - Open a follow-on issue: add an `origin` pedigree field to
     `PackMeta`/`PackEntry` (federal / community / user-supplied), same
     additive-schema pattern as `license`/`license_url` (AER-546/547).
   - Open a follow-on issue: extend `schema_guard`-style input/output floor
     checks to the OSM-sourced builders (water/highways) — today only
     NASR/navaids have the full four-layer guard.
   - When the charts builder is scoped (a separate issue, not this one):
     register `charts` in `packmeta.KINDS` with a floor/sanity check on the
     fetched raster from day one, tagged `origin: federal`.
3. **Do not build:** DPAL tiering, formal QMS documentation, LOA pursuit,
   per-field DQR sheets (§4).
4. **Decide (open question for Bill):** how far the pedigree label travels
   beyond the manifest — docs-only, picker label, or in-EFIS annunciation
   (§5). Recommend starting at "manifest + docs" and letting real usage
   justify going further.

## Open items for Bill

1. Approve or reject opening the `origin`/pedigree-field follow-on issue
   (§5), and if approved, which surfacing tier to start at.
2. Confirm no objection to a follow-on issue extending `schema_guard`-
   equivalent floor checks to the water/highways builders (§3).
3. No decision on the experimental/certified boundary is requested or made
   here — that stays with `MAOS-DESIGN/docs/AVIONICS_STACK_ROADMAP.md` per
   the issue's guardrail; this brief only flags that DO-200B would become
   load-bearing at that boundary if it's ever crossed.
