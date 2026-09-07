# Seattle Avionics compatibility — research brief

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status: research and recommendation only. Nothing built, nothing contacted,
nothing changed in the pipeline.** Answers AER-700. Public-sources-only
research, done 2026-09-07; every figure below is dated and sourced. No emails,
forms, demo requests, downloads, or account creation were used — see
"Guardrails observed" at the end.

## Verdict up front

**Path A (become a Seattle Avionics data licensee) does not clear the bar.**
The one public data point on their licensing posture — the EULA of PilotOne,
a sibling product from the same parent company — flatly prohibits
redistribution, sublicensing, and reverse engineering, and restricts use to
"internal business purposes" under personal, non-transferable credentials.
That is a closed-commercial-OEM shape, structurally incompatible with
shipping the result as a signed pack any pyEfis instance can download and any
GPL/Apache-licensed builder can read the source of — independent of price.
Nothing in Seattle Avionics' public materials suggests a different posture for
ChartData, and their whole go-to-market for OEM integration routes through a
sales conversation with no public developer program to check against.

**Path B (bring-your-own-subscription interoperability) also does not clear
the bar**, for a narrower reason: no documented data format exists for any of
ChartData's per-platform outputs, so reading it would require reverse
engineering — which the issue's guardrails and this brief's author both
decline to do. Stop there, per instructions.

**Against CAP-206: this is a distraction, not a complement, for the chart
case CAP-206 is actually about** (US VFR sectionals + IFR approach plates from
free federal data, georeferenced by us). Seattle Avionics' own history
confirms the arbitrage CAP-206 is built on — they georeference and repackage
the same FAA source data we would, for the same $99-$199/yr price band the
issue names, for a market of avionics OEMs. That is independent validation
the model works, not a reason to buy it instead of building it: the value
they charge for (georeferencing + packaging + currency-tracking free federal
data) is exactly the value CH-1..CH-4 already plans to build, and the signed
pack pipeline in this repo already generalizes that shape (terrain, water,
highways, navdata). There is one narrower place a license might earn its keep
— non-US coverage and SafeTaxi-class airport-diagram fidelity — flagged below,
but it doesn't change the verdict for the case CAP-206 is scoped to.

## Who they are, verified 2026-09-07

- **Seattle Avionics, Inc.**, founded 2002, originally shipped **Voyager**
  (Windows flight-planning software).
- Acquired by **AFV Partners LLC** on 2020-09-03
  ([businesswire.com, 2020-09-14](https://www.businesswire.com/news/home/20200914005606/en/AFV-Partners-Strengthens-its-Aviation-Vertical-with-Unique-Data-Technologies-Via-Seattle-Avionics-Acquisition);
  [generalaviationnews.com, 2020-09-17](https://generalaviationnews.com/2020/09/17/new-owner-for-seattle-avionics/)).
  Today it operates as **"Seattle Avionics, an APG company"**
  ([linkedin.com/company/seattle-avionics-software](https://www.linkedin.com/company/seattle-avionics-software),
  checked 2026-09-07) — **APG = Aircraft Performance Group, LLC**
  (`flyapg.com`), whose own site now carries the ChartData product page
  (`flyapg.com/products/chartdata`, checked 2026-09-07). Seattle Avionics is
  not an independent company anymore; it's a data/software line inside APG.
- **Two product lines that matter here:**
  - **ChartData** — the data brand: geo-referenced sectionals, IFR low/high
    charts, approach plates, SIDs/STARs, and airport diagrams, sold both
    direct to pilots and wholesale to avionics OEMs. Per APG's own product
    page, it covers **170 countries, 111k+ plates/diagrams, 10,400+ airports
    worldwide**, and is licensed into "nearly a dozen in-panel avionics and
    mobile applications," naming **Honeywell, Aspen Avionics, Dynon, and the
    U.S. Navy** as users
    ([flyapg.com/products/chartdata](https://flyapg.com/products/chartdata),
    checked 2026-09-07). Seattle Avionics' own company page independently
    confirms the OEM list: "more than 20 major aviation companies worldwide
    like Bendix-King, Aspen, Dynon, and more"
    ([seattleavionics.com/company.aspx](https://www.seattleavionics.com/company.aspx),
    checked 2026-09-07).
  - **FlyQ EFB** — the consumer app (with AOPA as a development partner
    historically), an iPad/iOS electronic flight bag. Current published
    subscription tiers: **$69/yr VFR-only**, **$139/yr VFR+IFR**
    ([seattleavionics.com/FlyQPocketFaq.aspx](https://seattleavionics.com/FlyQPocketFaq.aspx),
    checked 2026-09-07). FlyQ Pocket (a lighter companion app) is free.
- **2024: Seattle Avionics/ChartData earned FAA DO-200B certification**,
  reported as only the third company in the world to do so
  ([seattleavionics.wordpress.com, 2024-08-02](https://seattleavionics.wordpress.com/2024/08/02/august-8-2024-chartdata-released/);
  restated on both the Seattle Avionics and APG product pages, checked
  2026-09-07). DO-200B (RTCA/FAA AC 20-153B) is a data-quality/traceability
  process standard for aeronautical data suppliers — it speaks to data
  provenance and process rigor, not to redistribution rights. It's relevant
  context (it's a real quality signal) but not evidence either way on the
  licensing question this brief turns on. It also isn't a bar this project
  needs to clear: nothing we ship is a certified installation, and the
  Pi/bench hardware this repo targets is explicitly non-certified
  (`CLAUDE.md`: "mid-flight patching is explicitly OK — it's a debug bench,
  not a certified article").
- **Confirmed reseller pattern**, directly on point for Path A: Dynon
  distributes ChartData through a **"Dynon-subsidized program"** at
  **$119/yr flat** (VFR+IFR combined) for SkyView
  ([dynonavionics.com/skyview-charts-us.php](https://dynonavionics.com/skyview-charts-us.php),
  checked 2026-09-07), and Advanced Flight Systems resells the same data at
  **$99/yr** for the AF-5000 series, again through a Dynon-subsidized
  arrangement
  ([advancedflightsystems.com/af-5000-data.php](https://www.advancedflightsystems.com/af-5000-data.php),
  checked 2026-09-07). Both land inside the **$99-$199/yr** band the issue
  cites for CAP-206 — good independent confirmation that figure is current,
  not stale.

## Path A — a licensed data relationship

### What they license, at what granularity

Bundled, not à la carte, as far as any public page shows: "ChartData"
subscriptions sell VFR and/or VFR+IFR as a pair, covering sectionals,
IFR low/high en-route charts, approach plates, SIDs/STARs, and airport
diagrams together — there is no public price list for, say, "just obstacle
data" or "just airport diagrams" as an independently licensable slice.
Obstacle data for the US is actually **not** a Seattle Avionics product in
this ecosystem: on the Advanced Flight Systems data page, the "US
Aviation/Obstacles Database" is listed as **free**, sourced from the FAA's
own 28-day AIRAC cycle, separate from the paid Seattle Avionics chart line
([advancedflightsystems.com/af-5000-data.php](https://www.advancedflightsystems.com/af-5000-data.php)) —
i.e., obstacles are exactly the free-federal-data case this repo already
builds (`packtools`' own `obstacles` kind from FAA DOF), not something
Seattle Avionics would be selling us. Terrain is similarly not a ChartData
line item on that same page — AFS lists terrain as "free downloads" from a
different pipeline entirely.

International reach is the one place their granularity genuinely exceeds
ours: **170 countries** of plates/diagrams, per APG's own figures. That is
real, and it's the one area free FAA data structurally cannot reach (see
`docs/europe_coverage.md`'s finding that Europe has no FAA-equivalent public
domain source).

### Developer/OEM programme — none found public

No SDK, integration spec, or developer portal was found at any URL reachable
from `seattleavionics.com` or `flyapg.com` (checked 2026-09-07: company page,
products page, ChartData page, support page, ChartData support page). Every
OEM integration page (`flyapg.com/products/chartdata`, the AFS and Dynon
reseller pages) routes to a sales contact form or a named human contact
(`sales@seattleavionics.com`), not a self-serve technical spec. This matches
the issue's framing exactly: "what is public, and what sits behind a sales
conversation" — the answer here is *everything technical* sits behind the
conversation. There is nothing to evaluate as a spec because nothing
spec-shaped is published.

### Delivery, currency, and entitlement

Delivery is a **Windows desktop tool** ("ChartData Manager" / "Data Manager")
that downloads chart data and writes it to a **USB memory stick prepared per
display** — Dynon's own instructions specify an exact-case folder name
(`SkyViewUS` / `SkyViewEU`) on the stick
([dynonavionics.com/skyview-databases-usb-prep.php](https://dynonavionics.com/skyview-databases-usb-prep.php),
checked 2026-09-07). That stick must stay plugged into the display in flight.
Licensing is **per-airplane**, not per-device or per-account: "only one
subscription is needed per airplane" (both the Dynon and AFS pages state this
identically). On the AFS side there's a separate, additional entitlement
layer — a **Mapping Software license key** activated per display and checked
on the unit's ABOUT page, independent of the ChartData subscription itself
([advancedflightsystems.com/af-5000-data.php](https://www.advancedflightsystems.com/af-5000-data.php)).
No cryptographic DRM scheme is documented publicly; the practical gate is "no
subscription → the Data Manager won't produce current files for you," which
is a services/updates gate rather than the crypto-signed, verify-before-trust
model this repo already runs (`manifest.json.minisig` + per-pack sha256). We
could not determine from public sources whether the on-stick files themselves
carry any tamper/authenticity check.

### The licensing question that decides it

This is the one that matters, and it's the one place research came up short
on a **ChartData-specific** primary source: no public EULA or terms-of-use
document was found for ChartData or FlyQ specifically, despite checking the
obvious paths (company site footer, ChartData/FlyQ support and FAQ pages,
search for `seattleavionics.com` + "terms"/"EULA"). The only public license
agreement traceable to the same parent company is **APG's PilotOne EULA**
(`flyapg.com/pilotone-eula`, checked 2026-09-07) — a different product, so
this is evidence of APG's general legal posture, not a confirmed statement of
ChartData's specific terms. With that caveat stated plainly, what it says is
unambiguous and exactly the shape that would block us:

- **No redistribution, in any form**: "sell, resell, rent, lease, lend,
  timeshare, sublicense, disclose, publish, assign, market, distribute,
  transfer, transmit, broadcast, or otherwise make available the Service or
  any portion thereof to any third party" (§4.3(i)).
- **No reverse engineering**: "reverse engineer, disassemble, decompile, or
  otherwise attempt to derive the source code, underlying ideas, algorithms,
  structure, or organization of the Service or any portion thereof" (§4.3(iii)).
- **Personal, non-transferable credentials**, use limited to "internal
  business purposes," explicitly excluding "service bureau or outsourcing
  arrangements for third-party benefit" (§4.2).

Every one of those clauses is incompatible with what this project would need
to do with licensed data: build a pack, sign it, and let it flow — verified,
but otherwise unrestricted — to any pyEfis instance a builder chooses to run,
under Apache-2.0/GPL terms that guarantee the recipient the right to inspect,
modify, and redistribute the *code* that reads it. An OEM contract written
for closed avionics boxes (Dynon SkyView, Aspen, Honeywell — all closed,
certified or quasi-certified commercial products) has no reason to carry
open-source-compatible redistribution language, and nothing found here
suggests Seattle Avionics/APG has ever granted it. This is precisely the
structural mismatch the issue asked to be named if found: **an OEM data
licence written for a closed commercial product, independent of price.**

**Verdict: Path A does not work as this project is currently licensed and
distributed**, without a bespoke exception from APG that nothing in their
public materials indicates they've ever granted to an open-source consumer.
That exception is not something research can settle — it's the vendor
conversation named below.

## Path B — interoperability without a business relationship

### Is the format documented?

No. Every technical description found describes *inputs to a Windows tool
and outputs onto vendor-specific media* (a USB stick with a case-sensitive
folder name for SkyView; `.AFM`/`.dup`-extension files plus an activation key
for the AF-5000 series — per
[advancedflightsystems.com/af-5000-data.php](https://www.advancedflightsystems.com/af-5000-data.php),
checked 2026-09-07). No file-format specification, schema document, or SDK
was found for any of these outputs. The `.AFM`/`.dup` extensions on the AFS
page are the closest thing to a format name surfaced anywhere in this
research, and they're proprietary — Seattle Avionics/APG's own, not a
published open format like AIXM or the OpenAIR text format this repo already
knows how to talk about (see `docs/openaip_evaluation.md`'s OpenAIR/GeoJSON
discussion for the contrast with a source that *does* publish its schema).

**If reverse engineering is the only route, this brief stops there**, per
the issue's own instruction and this project's general posture (no
reverse-engineering of any protection or format, ever, in this repo). No
attempt was made to open, parse, or otherwise inspect any Seattle
Avionics-produced file as part of this research.

### Would "bring your own subscription" even be allowed?

Independent of the reverse-engineering blocker, the same PilotOne-EULA
evidence from Path A cuts against this too: prohibiting the user from making
the Service (or "any portion thereof") available to a third party reads as
covering "feed the data into pyEfis" just as much as it covers commercial
resale — a builder's own paid subscription doesn't obviously buy them the
right to point unrelated third-party software at the exported files, even
for personal, non-commercial use. This is again a **ChartData-terms**
question, not a PilotOne-terms question, and it's exactly the kind of
narrow factual question a direct answer from the vendor would resolve
cheaply — but nothing here should be taken as green-lighting an attempt in
the meantime.

**Verdict: Path B does not work today.** No documented format to build a
reader against, and secondary evidence suggesting the licence wouldn't
permit it even if a format were available. Both threads point at asking the
vendor, not at engineering around them.

## Against CAP-206: complement, replace, or distract?

**Distract, for the case CAP-206 is actually scoped to** (georeferenced US
VFR sectionals + IFR approach plates from free FAA source data). The
evidence gathered here is, if anything, a stronger endorsement of CAP-206's
own thesis than a challenge to it:

- Seattle Avionics' obstacle and terrain data for the platforms surveyed
  here is **not their own product** — it's free FAA/other-source data passed
  through, exactly like `packtools`' existing `obstacles`/`terrain` kinds.
  They only charge for the chart/plate/diagram layer — the georeferencing
  and packaging labor CAP-206 identifies as the arbitrage.
- Their price band ($99-$139/yr direct, $99-$119/yr OEM-subsidized) matches
  the issue's cited $99-$199/yr almost exactly, for a US-centric product —
  independent confirmation the CAP-206 framing is current and correctly
  priced, not stale.
- A real OEM (Dynon, AFS, Aspen, Honeywell) chooses to **license this rather
  than build it themselves**, which says the georeferencing/packaging work is
  genuinely nontrivial — consistent with CH-1..CH-4 being a real roadmap item
  and not a "how hard can it be" underestimate. It does not say the free
  federal source data underneath is unavailable or that our pipeline can't
  do the same work; it says other commercial avionics vendors made the
  buy-vs-build call in the other direction, for their own commercial reasons
  (their businesses don't want to be in the data-currency business at all;
  ours already is, structurally, for every other layer we ship).

**Where the counter-case is real, and worth naming plainly**: the two things
found here that free federal sources genuinely cannot replicate are (1)
**non-US coverage** — 170 countries of plates/diagrams is categorically
outside what FAA data provides, and `docs/europe_coverage.md` already
documents why Europe alone is a hard, fragmented, mostly-non-public-domain
problem; and (2) **SafeTaxi-class airport-diagram fidelity**, if Seattle
Avionics' diagrams meaningfully exceed a from-scratch build off FAA source
PDFs (this research did not compare rendered output quality — that would
need actual samples, which sit behind the same paywall as everything else
here). Both are real "where a licence might earn its keep" cases per the
issue's own framing. Neither changes the verdict, though: Path A's licensing
wall applies exactly as much to international coverage as to US coverage —
there's no evidence APG licenses ChartData any more permissively outside the
US — so even the strongest version of the counter-case is blocked by the
same clause, not a reason to revisit the buy-vs-build call until that clause
is resolved.

**Recommendation: do not pursue Seattle Avionics integration now.** Keep
CAP-206 and CH-1..CH-4 as the primary chart-data path. Revisit only if Bill
gets a materially different answer from APG than what public materials
suggest (see next section) — and even then, treat it as a possible
*addition* for non-US coverage or SafeTaxi-class diagrams specifically, not
a replacement for the free-federal-data pipeline that already works for
everything CAP-206 is actually about.

## Questions only Bill can answer (a vendor conversation, not research)

None of the below were asked — outbound contact is explicitly out of scope
for this brief. If Bill wants to test whether the verdict above would change,
this is the shortest list that would settle it:

1. **Does ChartData's OEM/commercial licence have any provision for a free,
   non-commercial, open-source redistributor** — the same question this
   project already asked openAIP successfully (`docs/openaip_evaluation.md`)?
   Seattle Avionics/APG's business (selling data into closed commercial
   avionics) makes a "yes" much less likely than openAIP's community-project
   posture did, but the honest answer is "unverified without asking."
2. **Is there a published ChartData EULA or OEM contract Bill could read** —
   this brief could only find APG's PilotOne EULA, a different product, so
   confirming or correcting the Path A verdict against the actual ChartData
   terms would remove the one real caveat in this research.
3. **Is any ChartData output format ever documented for integrators** —
   even under NDA — that would change the Path B format-availability
   question (though not the licence question, which is the harder blocker).
4. If the answer to (1) is genuinely no: **would a paid, standard OEM
   licence — accepting its closed terms as a cost of business rather than
   trying to redistribute — make sense for a narrow, specific gap** (e.g.
   non-US chart coverage, sold as an optional paid add-on outside the
   Apache/AGPL-licensed core pipeline)? That's a business-model question
   for Bill, not a research one, and it's a real fork in strategy this brief
   flags but does not resolve.

## Proposed pyEfis-side follow-on issues (not started)

Written up as design-only proposals per the issue's scope note — pyEfis is
not in this working directory and nothing here should be read as scoping
actual implementation work:

- **None recommended at this time.** Given the Path A/B verdicts above,
  there is no pyEfis-side reader, importer, or format-shim work to propose —
  building one would require either a licence this research found no
  evidence of, or reverse engineering this project declines to do. If Bill's
  answers to the questions above change the licensing picture, the natural
  follow-on would be a pyEfis issue for **a ChartData-sourced chart/plate
  reader analogous to the existing SVS/`highway_db_path` pattern** (read a
  vendor-format file from a configured path, same "external DB, internal
  reader" shape as `docs/roads.md`'s `highway_db_path` today) — but that
  issue should only be opened once Path A actually clears, not before.

## Guardrails observed

- Public sources only. No emails, contact forms, sales/demo enquiries,
  account creation, or trial-software downloads.
- No reverse engineering, decompilation, or circumvention of any protection
  on any data format — none was attempted, and Path B's verdict stops
  exactly where that line would have to be crossed.
- No pipeline changes, no new pack kinds, no code — this document is the
  entire deliverable.
- `CAPABILITIES.md` (maos-workspace) is not this repo; no edits attempted
  there. The proposed framing above (distract, not complement or replace,
  for CAP-206 as scoped) is offered for Bill/AVIONICS to carry into that
  document if they agree with it.
- Every figure above is dated to when it was checked (2026-09-07 unless a
  different date is cited alongside a specific source) and links its source
  directly, per the issue's instruction that pricing and terms move.

## Related

- [openaip_evaluation.md](openaip_evaluation.md) — the precedent for
  evaluating a third-party data source's license posture before building
  anything against it; contrast case where the licence *did* clear.
- [europe_coverage.md](europe_coverage.md) — why non-US aeronautical data is
  structurally harder than the FAA case, independent of Seattle Avionics.
- [roads.md](roads.md) / [water.md](water.md) — the existing free-federal/OSM
  pipeline shape CAP-206 and CH-1..CH-4 would extend to charts.
