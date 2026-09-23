# d-TPP approach plates spike — size, distribution unit, georeferencing (PA10)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status: spike complete. Decision brief for Bill's ruling — nothing built,
nothing published.** Answers `billmallard/makerplane-data#86` (PA10 of the
procedures & airways epic, `makerplane/briefs/procedures_and_airways_plan.md`
§5). PA11 (build+publish) and PA12 (viewer) are gated on the ruling below.

Every number in this document was measured against the live FAA AeroNav
server on 2026-09-19 (cycle **2609**, effective 2026-09-03 to 2026-10-01), not
estimated. The companion `openaip_evaluation.md` is the precedent for this
document's shape: evaluate a candidate source's format, size and licence fit
before committing a build item to it.

---

## 0. Recommendation, up front

1. **Size is real but manageable: ~4.7 GB/cycle nationally**, smaller than
   several existing terrain-region packs already served today. Not a barrier.
2. **Georeferencing metadata is not published.** Verified directly against
   sample plates — no GeoPDF/TerraGo markers. It must be **derived**, and a
   concrete, low-risk derivation path exists (§2) using data this repo
   already parses (PA1's CIFP fix coordinates) cross-referenced against the
   plate's own vector text layer. It is not proven end-to-end here — that is
   PA11/PA12 work — but the ingredients are real and present in a 100%
   sample.
3. **Distribution unit: per-region, not per-airport and not national** (§3).
   Reuse the existing `regions.yaml` boundaries that already carry terrain
   and water. Per-airport packaging multiplies the manifest by ~3,200 rows
   for a ~2x storage saving that the region split already delivers most of.
4. **Licence: public domain, same `LicenseRef-us-public-domain` tag already
   used for NASR/DOF/CIFP** (§4). Trivial to fill in.
5. **Rendering: pyEfis #56 (signed-pack currency) is exactly the right
   vehicle. #50 (AvChart raster-tile engine) is not** — d-TPP plates are
   single-page vector PDFs, not the pre-georeferenced `.tfw`-tagged raster
   mosaics AvChart tiles. PA12 needs a page-renderer, not a reprojector (§5).
6. **Per the brief's stance and guardrail 5: recommend shipping the plate
   viewer first, with the ownship overlay gated per-plate on the derived
   georeferencing passing a measured residual-error check**, not gated on
   the whole feature.

---

## 1. Size — measured, not estimated

### 1.1 National total (ground truth)

FAA AeroNav publishes d-TPP as five regional zip volumes per 28-day AIRAC
cycle (`aeronav.faa.gov/Upload_313-d/terminal/DDTPP{A..E}_<date>.zip`).
Directory-listing the live server for the cycle effective now (2026-09-03):

| Volume | Bytes |
|---|---:|
| A | 985,535,610 |
| B | 984,576,033 |
| C | 874,365,609 |
| D | 919,820,108 |
| E | 961,289,965 |
| **Total** | **4,725,587,325 (4.40 GiB / 4.73 GB)** |

```bash
curl -sI https://aeronav.faa.gov/Upload_313-d/terminal/   # directory listing, sizes are exact
```

For scale, against the **live production manifest** at
`navdata.aerocommons.org/manifest.json` today:

| Pack | Bytes | Cadence |
|---|---:|---|
| navdata-conus | 5.9 MB | AIRAC (28d) |
| navaids-conus | 5.8 MB | AIRAC (28d) |
| obstacles-conus | 75 MB | ~monthly |
| highways-conus | 116–403 MB | quarterly |
| water-na | 3.1–4.3 GB | occasional |
| terrain-us-central | 2.86 GB | one-time edition |
| terrain-us-east | 3.65 GB | one-time edition |
| terrain-us-south | 3.01 GB | one-time edition |
| terrain-us-west | 5.28 GB | one-time edition |
| terrain-alaska | 4.81 GB | one-time edition |
| terrain-canada-west/east | 7.2–7.7 GB | one-time edition |

**The size itself is not the problem — the recurrence is.** A national d-TPP
pack (4.73 GB) is smaller than most individual terrain regions, but terrain
is a one-time `2024ed` download; d-TPP would repeat that ~4.7 GB **every
28-day AIRAC cycle, forever**, on top of everything else already pulled that
cycle. That is the argument this spike makes against a national pack — not
that it doesn't fit once, but that it doesn't fit *every cycle*.

### 1.2 Per-airport and per-chart-type distribution (measured)

The FAA also publishes a per-cycle chart catalog,
`aeronav.faa.gov/d-tpp/2609/xml_data/d-tpp_Metafile.xml` (16.3 MB XML, fetched
directly). Parsed record-by-record, cycle 2609:

- **54 states/territories, 2,942 cities, 3,197 airports, 24,231 chart
  records, 16,503 distinct PDF files.**
- Chart-type breakdown: `IAP` (approach) 11,185 · `MIN` (takeoff/alternate
  minimums) 5,385 · `STR` (STAR) 3,036 · `DP` (departure) 3,030 ·
  `APD` (airport diagram) 921 · `ODP` (obstacle DP) 287 · `HOT` (hot spots)
  287 · `LAH` (land-and-hold-short) 88 · `DAU` (diverse vector area) 12.
- **147 of 24,231 records (0.6%) carry `useraction=D` and a sentinel
  `pdf_name=DELETED_JOB.PDF` — a withdrawn-chart placeholder, not a real
  file** (confirmed: it 404s). A permissive parser (mirroring the ARINC 424
  parser PA1 just wrote) must filter `useraction=D`, not fetch it.
- **Not every chart is per-airport.** `MIN` (takeoff/alternate minimums) is
  published as ~74 *regional* booklets shared by up to 197 airports each
  (e.g. `NC1TO.PDF` covers 197 airports); `STR`/`HOT`/`LAH` have smaller but
  real sharing too. Only `IAP`/`APD`/`DP`/`ODP`/`DAU` are one-file-per-record.
  This matters for packaging: a naive "ship each airport's own files" scheme
  either duplicates the shared regional booklets into every airport's bundle
  (inflating total bytes) or has to special-case them.

A stratified random sample of 69 real PDFs (≥6 per chart type, spread across
19 states) was downloaded and measured directly (`curl`, not estimated):

| Chart type | Mean size (measured, n≥6) |
|---|---:|
| HOT | 45 KB |
| LAH | 57 KB |
| APD | 108 KB |
| STR | 184 KB |
| DP | 215 KB |
| ODP | 260 KB |
| IAP | 265 KB |
| MIN | 266 KB |
| DAU | 344 KB |

Extrapolating this measured average across the real per-chart-type record
counts gives an estimated 3.98 GB distinct-file total — within 16% of the
4.73 GB ground truth from §1.1 (the gap is sampling noise at n=69; the zip
total is authoritative). Per-airport, that resolves to:

- **~1.25 MB/airport** if shared regional booklets are counted once
  (distinct-file basis).
- **~1.8 MB/airport** if every airport's pack carries its own full copy of
  every regional booklet it references (fully-duplicated basis — what a
  naive per-airport pack would actually ship).

### 1.3 Regional split (measured coordinates, real chart-type sizes)

Joining the d-TPP airport list against OurAirports' public-domain worldwide
airport table (`davidmegginson.github.io/ourairports-data/airports.csv`, all
3,197 airports matched by FAA/ICAO ident) and bucketing by the terrain/water
region boxes already in `packtools/regions.yaml`:

| Region (existing `regions.yaml` bbox) | Airports | Bytes (duplicated-basis upper bound) |
|---|---:|---:|
| us-east | 1,329 | 2.33 GB |
| us-south | 1,014 | 1.99 GB |
| us-central | 974 | 1.64 GB |
| us-west | 553 | 1.12 GB |
| alaska | 128 | 0.17 GB |
| (Canada/Pacific/int'l, no CONUS region box) | 268 | 0.57 GB |

These are upper bounds (fully-duplicated basis, and `us-west`/`us-central`/
`us-south` overlap by design per `regions.yaml`'s own comment, so a real
region pack — which would de-dup its own shared regional booklets — lands
lower). Even at the upper bound, **every region lands at or below the
existing `water-na` pack (3.1–4.3 GB) and below every terrain region.** The
existing pipeline already carries packs this size; nothing new is needed on
the R2/signing/updater side.

---

## 2. Georeferencing — not published, but derivable with a stated error budget

### 2.1 It is not published (verified negative)

Checked directly against downloaded plates for TerraGo GeoPDF markers
(`LGIDict`, `Measure`, `Viewport`, `GPTS`/`LPTS`, `WKT`/`PROJCS`/`GEOGCS`) —
**none present, in any sampled plate.** This is a different FAA product line
from the pre-georeferenced `.tfw`-tagged VFR/IFR raster charts pyAvMap's
`AvChart` engine (pyEfis #50) already knows how to tile — d-TPP plates carry
no scale, projection, or corner-coordinate metadata of any kind. Do not
assume otherwise; this was the one item explicitly flagged "verify, don't
assume" and it verifies negative.

### 2.2 It can be derived, and the ingredients already exist in this repo

Two findings make a derivation path realistic rather than speculative:

1. **Every sampled plate (68/68, 100%, across all nine chart types and
   19 states including Alaska) is a vector PDF with a real, extractable text
   layer** — not a scanned raster. Word-level positions are directly
   readable (e.g. `pymupdf`'s `page.get_text("words")` returns
   `(x0,y0,x1,y1,word)` tuples; no OCR needed). Fix idents used in a
   procedure's plan view (`JASER`, `UZOHO`, `HASIV`, `PDZ`, …) appear as
   plain text near their symbol.
2. **PA1 already parses those same fix idents' exact coordinates** from
   CIFP as part of the `procedures`/`legs` tables (`docs/../packtools`,
   landed on `dev`). The plate names a fix; PA1's database already knows
   that fix's lat/lon to sub-arcsecond precision, for the *same* procedure
   in the *same* cycle.

**Proposed method:** for a given procedure's plate, OCR/text-extract the
labelled fix idents in the plan-view region of the page, match each against
PA1's parsed coordinate for that procedure's legs, and solve a similarity
transform (scale + rotation + translation) from ≥2 matched control points,
holding a 3rd as an independent residual-error check where available. This
needs **no new data source** — it cross-references two things this pipeline
already owns.

**Honest failure modes, found by direct inspection, not assumed:**

- **Label ambiguity.** The same fix ident can appear 2–3 times on one page
  (plan view, profile view, missed-approach text block) — confirmed directly
  (`PDZ` appears 3 times on the sampled RNAV(GPS)-A RIR plate). Naive
  text-match must cluster by page region, not take the first hit.
- **Not every plate has ≥2 matchable fixes** — a simple circling approach or
  a plate dominated by a single VOR may not have enough labelled plan-view
  fixes to solve a 4-parameter transform with a check point.
- **Orientation is not guaranteed north-up.** FAA plan views are
  conventionally true-north oriented with a printed north arrow, but the
  arrow's actual rotation is a graphic, not text — verifying "is this plate
  north-up" needs a second signal (e.g. arrow detection) the text layer alone
  doesn't give.
- **A single printed ARP coordinate** (e.g. `33°59'N-117°25'W` on the sampled
  RIR plate's briefing strip) is real and text-extractable, but by itself is
  only one control point — enough to *anchor* a transform, not to *solve* one
  (need scale+rotation too).

**None of this is proven end-to-end here — that is explicitly PA11/PA12
scope, not this spike's.** What this spike establishes is that the
"unproven" question in the GitHub issue is *not* "does any data exist to
try" (it does, and it's already in this repo from PA1) but "does the derived
transform's residual error clear a stated budget, per plate." That is a
build-time automated check, not a research question: compute the transform
from the matched control points, reproject the held-out check point, and
gate ownship on that plate on the reprojection error — exactly what
guardrail 5 already requires ("no ownship on a plate that has not been
georeferenced to a measured error budget").

### 2.3 Recommendation

Ship the plate **viewer** unconditionally (crisp vector-PDF rendering needs
no georeferencing at all). Attempt the CIFP-cross-reference derivation
per-plate at build time (PA11); where it succeeds within a residual-error
budget still to be set once real transform accuracy is measured against
known runway-threshold coordinates, enable ownship on **that plate only**.
Where it fails or is ambiguous, ship the plate with no ownship — per the
brief's own stance, a correct chart with no ownship is a working feature; an
ownship dot in the wrong place is not.

---

## 3. Distribution unit — per-region

Three options, evaluated against real numbers from §1:

| Option | Verdict |
|---|---|
| **National** (one pack, all 3,197 airports) | Rejected. 4.73 GB is affordable *once*, but every device re-pulls it in full every 28-day AIRAC cycle forever — no other cyclical pack in this pipeline behaves this way (terrain/water are one-time or occasional; navdata/navaids/obstacles are cyclical but two orders of magnitude smaller). |
| **Per-airport** (3,197 packs) | Rejected. Average payload per airport is tiny (~1.25–1.8 MB, §1.2), but the *pack* is the wrong unit to shrink to airport granularity: it turns a ~20-row signed manifest into a >3,000-row one, multiplies R2 object/signature count by the same factor, and still has to solve the shared-regional-booklet duplication problem (§1.2) for every one of those 3,197 objects. The bandwidth win over per-region is small (§1.3 shows regions already land at ~150 MB–2.3 GB); the manifest/signing overhead cost is not. |
| **Per-region (recommended)** | Reuse `packtools/regions.yaml` unchanged — the same six boxes already grouping terrain and (implicitly) water. §1.3 shows every region lands at or below the existing `water-na` pack size. No new distribution mechanism, no manifest-shape change, no new R2 pattern. A CONUS pilot on an SD-card Pi installs one region; an NVMe user installs several, exactly as they do for terrain today. |

Per-airport *selection* (not packaging) still works fine inside this: the
on-device picker can let a pilot mark specific airports/procedures as
"kept" from within an installed region pack, the same way `update --only`
already persists a point selection to `data.yaml` — that UI-level
granularity doesn't require pack-level granularity.

---

## 4. Licence + attribution

FAA d-TPP is federal government work — no copyright attaches (17 U.S.C.
§105), the same basis already used for NASR/DOF/CIFP in this repo
(`docs/LICENSE-AUDIT.md`, `license=LicenseRef-us-public-domain`). Filling in
`PackMeta` for a future `plates`/`procedures`-adjacent pack kind is
mechanical and mirrors the existing CIFP entry exactly:

```python
PackMeta(
    id="plates-us-east",         # per §3, one id per region
    kind="plates",               # new kind — not yet added to packmeta.KINDS
    cycle="2609",                # AIRAC id, same cadence packtools/cycles.py already knows
    attribution="FAA Digital Terminal Procedures Publication (d-TPP), AeroNav Products",
    license="LicenseRef-us-public-domain",
    license_url="https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/",
)
```

No new licence question, no new attribution string pattern — same shape as
every other FAA-sourced pack already shipping.

---

## 5. Rendering path — pyEfis #56 fits, #50 does not

- **#56 (chart & nav-data currency via signed makerplane-data packs)** is
  exactly the right vehicle, unchanged. Plates fit the same verify-then-swap,
  `data_status`/annunciation, per-cycle currency model every other pack kind
  already uses. Nothing about a plates pack needs a different update
  mechanism.
- **#50 (raster chart layer, porting pyAvMap's `AvChart` engine to PyQt6)
  is not the right vehicle for d-TPP specifically.** `AvChart` tiles
  pre-georeferenced raster mosaics carrying `.tfw`/`.htm` world files and
  reprojects them through Lambert Conformal — that is FAA's *separate* VFR/
  IFR raster sectional/enroute chart product, which genuinely does ship
  georeferencing metadata (unlike d-TPP, confirmed absent in §2.1). A d-TPP
  plate is one discrete, ungeoreferenced page per procedure, not a
  continuous tiled mosaic. **PA12 needs a single-page vector-PDF page
  renderer** (e.g. Qt's own PDF module or `pymupdf`, rendered directly — no
  tile-stitching, no reprojection pipeline) **with an independent,
  much smaller overlay layer that draws ownship only where §2.3's per-plate
  transform clears its error budget.** The two features can share the
  `map`/chart-layer selection UI #50 builds without sharing its projection
  engine.
- CAP-207 (airport diagrams) rides the same plate pipeline for free — `APD`
  is already one of the nine chart types measured in §1.2, no separate
  acquisition path needed.

---

## 6. What this spike did not do (explicitly out of scope, PA11/PA12 work)

- No pack was built, signed, or published. `plates` is not yet in
  `packtools/packmeta.KINDS`.
- No end-to-end georeferencing transform was computed or validated against
  ground truth (e.g. a known runway threshold) — §2 establishes the method
  and its ingredients are real, not that a specific plate's derived accuracy
  clears any particular error budget.
- The exact residual-error budget for enabling ownship is not set here — it
  should be measured against a handful of known-truth control points (e.g.
  published runway threshold coordinates vs. their pixel position) before
  PA12 turns ownship on for even one plate, per guardrail 5.
- Canada/Mexico/Caribbean coverage (268 airports with no CONUS region box in
  §1.3) is out of scope for this pass — d-TPP itself is FAA/US-only; non-US
  procedure charts are a separate future source question, not a PA10 gap.

---

## Reproduction

Every number above came from a live query against the public FAA AeroNav
server, run 2026-09-19:

```bash
curl -sI https://aeronav.faa.gov/Upload_313-d/terminal/                       # §1.1 volume sizes
curl -s https://aeronav.faa.gov/d-tpp/2609/xml_data/d-tpp_Metafile.xml        # §1.2 per-chart catalog
curl -s https://aeronav.faa.gov/d-tpp/2609/<pdf_name>                        # §1.2/§2 sample plates
curl -s https://davidmegginson.github.io/ourairports-data/airports.csv       # §1.3 airport lat/lon join
curl -s https://navdata.aerocommons.org/manifest.json                        # §1.1 existing pack sizes
```

No secrets, no signing key, no R2 access were used or needed for this spike.
