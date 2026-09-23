# regions.yaml coverage gap: Alaska (Aleutians) and Hawaii (AER-1988)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Answers `billmallard/makerplane-data` issue AER-1988, filed against PA11's
d-TPP plate work (`AER-1610`, `docs/dtpp_plates_spike.md`,
`packtools/make_plates.py`'s `airport_region_map`): **Adak Island (PADK)**
sits west of `regions.yaml`'s `alaska` region `lon_min` (-170) and matches no
plate region. Pinned as a regression
(`tests/test_make_plates.py::test_airport_region_map_real_adak_falls_outside_the_alaska_region`)
rather than silently patched, because `regions.yaml` is shared by terrain and
water — widening a box has a size consequence those packs pay for, which is a
board call, not a build fix. This document supplies the three things AER-1988
asked for, in order: a live count, a measured size cost, and a recommendation.
It does not change `regions.yaml`.

**Everything below is measured against live data on 2026-09-23** — the real
d-TPP metafile for the currently-effective AIRAC cycle (2609, fetched from
`aeronav.faa.gov`), the real OurAirports `airports.csv` (fetched from
`davidmegginson.github.io`), and real Copernicus GLO-30 tiles (fetched from
the public `copernicus-dem-30m` S3 bucket) run through the exact conversion
+ compression the pipeline ships (`packtools/make_terrain.py`: 3601×3601
big-endian `int16` `.hgt`, `ZIP_DEFLATED` level 6). Nothing here is
extrapolated from the PA10 spike's numbers; they were re-measured because
they have since moved (see "A larger, separate anomaly" below).

## 1. How many US airports fall outside every region box, today

Two different questions, both asked in the issue, both answered from the
same live join `airport_region_map()` already performs:

### 1a. Among airports with real d-TPP plates (PA11's actual candidate set)

Parsing the live cycle-2609 metafile gives 3,197 distinct airports (matches
`docs/dtpp_plates_spike.md`'s count exactly). Joining against live
OurAirports and running them through `airport_region_map()` unchanged:

- **21 of those are real, `iso_country == "US"` airports that match no
  `regions.yaml` plate region** (`us-west`/`us-central`/`us-east`/
  `us-south`/`alaska`):
  - **15 in Hawaii**: PHNL (Honolulu Intl), PHOG (Kahului), PHKO (Kona),
    PHLI (Lihue), PHTO (Hilo), PHBK, PHHI, PHHN, PHJR, PHLU, PHMK, PHMU,
    PHNG, PHNY, PHSF.
  - **6 in the far-western Aleutians / Bering Sea**: PADK (Adak — the
    airport the issue names), PAAK (Atka), PAGM (Gambell), PASA (Savoonga),
    PASN (St Paul Island), PASY (Eareckson AFS / Shemya).

Hawaii is **not** a boundary miss like Adak — it is an entire US archipelago
with no `regions.yaml` box of its own at any distance from any existing
box's edge. Widening `alaska` cannot touch it; see §3.

### 1b. Separately, across all of OurAirports (not gated on having a plate)

Same join, no d-TPP filter, `iso_country == "US"`:

| Filter | US airports | ...outside every region box |
|---|---:|---:|
| Real airport types only (`large`/`medium`/`small_airport`, matching `packtools/ourairports.py`'s own filter) | 16,136 | **38** |
| Every OurAirports type (heliports, closed fields, ultralight strips, balloonports, seaplane bases, …) | 32,667 | 150 |

The 38 (real-airport-type) breakdown: **29 Hawaii + 7 Alaska/Aleutians**
(the 6 above plus PAAT, Casco Cove CG Station/Attu, which has no current
d-TPP plate) **+ 2 that are not a `regions.yaml` problem at all**:

- `58MN` (Northwest Angle Airport, Minnesota) sits at 49.35°N — a real,
  tiny US exclave a third of a degree north of `us-central`'s `lat_max: 49`.
  Genuine, but a one-airport, sub-degree sliver, not remotely comparable in
  size to Adak or Hawaii.
- `US-12961` ("Foger flavors", Missouri) carries `longitude_deg = 94.5786`
  in live OurAirports data — **positive**, i.e. the eastern hemisphere. That
  is an OurAirports data-entry error (should almost certainly be -94.5786),
  not a `regions.yaml` gap. Flagged for awareness, not actionable here.

### A larger, separate anomaly, flagged but not investigated here

Re-running `airport_region_map()`/`make_plates.run()`'s exact matching logic
(`region_map.get(icao_ident) or region_map.get(apt_ident)`) against today's
live data finds **834 of 3,197 d-TPP airports (26%) match no plate region**
— not the 268 (8%) `docs/dtpp_plates_spike.md` §1.3 measured. Of those 834,
**788 don't match any OurAirports row at all**, by either ident (the
remaining 46 split into the 21 US ones above and 25 genuinely non-US/foreign
ones — Puerto Rico, Guam, N. Mariana Islands, American Samoa, US Virgin
Islands, US Minor Outlying Islands, and three actually-foreign
Compact-of-Free-Association states, FM/MH/PW).

This is very likely an ident-matching problem (`ident` vs. OurAirports'
`local_code` field for small US fields — e.g. `05K`/`79J`/`0R1` never
resolve against `ident`) rather than a new geography question, and it is
**bigger than the Adak gap this issue is about**. It is called out here
because it surfaced from the same measurement, not because this document
answers it. Recommend a separate issue.

## 2. Measured size cost of closing the Aleutian gap

Terrain is the only one of the two shared packs `regions.yaml` currently
region-splits by these boxes — see §2c.

### 2a. Method

`regions.yaml`'s tiles are assigned by 1°×1° SW-corner (`packtools/regions.py`).
For each candidate widened footprint, every possible tile cell was checked
against the real Copernicus GLO-30 bucket (HEAD request; GLO-30 only
publishes tiles that cover land — pure open ocean has no tile at all, which
turns out to matter a lot). Every tile that exists was downloaded, resampled
to the exact 3601×3601 grid the pipeline's `.hgt` format uses, written
big-endian `int16`, and zipped at `ZIP_DEFLATED` level 6 — the same
transform `packtools/make_terrain.py` applies and `docs/terrain.md`'s own
"Measured DEFLATE-6" table uses. Mip-pyramid overhead is added using
`docs/terrain.md`'s own measured average (2.78 MB/tile compressed).

### 2b. Results

| Option | New 1° cells checked | Real tiles found | Native, compressed | + mip pyramid (measured avg) | **Total added** | Closes |
|---|---:|---:|---:|---:|---:|---|
| Widen `alaska` `lon_min` -170 → **-177** | 147 | 41 | 96.4 MB | +114 MB | **~210 MB** | Adak, Atka, Gambell, Savoonga, St Paul (5 of 6) |
| Widen `alaska` `lon_min` -170 → **-180** | 210 | 61 | 158.8 MB | +170 MB | **~328 MB** | same 5 — **not** Shemya/Attu (see §2d) |

For scale, `regions.yaml`'s own header comment sizes each terrain region at
"~8-15 GB". Either option is a **~1.4%–4% growth** of the existing
`terrain-alaska` pack — measured, not a rounding-error guess, and not the
kind of size event the "shared bbox" caution in `docs/plates.md` was
bracing for. Full per-tile numbers and the fetch/convert script are
reproducible from this doc's method; raw data isn't checked in (routine
build inputs, not committed per `CLAUDE.md`).

### 2c. Water: unaffected today

`docs/regionalize_bulk_packs.md` — `water` still ships as a single
continental `water-na` monolith; it is not yet region-split by
`regions.yaml`'s boxes at all (that regionalization is planned, unstarted
work). **A `regions.yaml` bbox change has zero build-time effect on the
water pack right now.** If/when water regionalizes, the added area here is
almost entirely open ocean and a handful of small islands, so the added
coastline-geometry cost would likely be similarly small — but that's a
prediction about a mechanism that doesn't exist yet, not a measurement.

### 2d. The antimeridian problem — widening alone cannot close all 6

Two of the six non-Hawaii airports, PASY (Eareckson AFS / Shemya) and PAAT
(Casco Cove / Attu, the westernmost point in the United States), carry
**positive** longitude in OurAirports (+174.11°, +173.17°) — they're just
east of the 180° meridian in absolute terms, i.e. properly ≈ -186° to -187°
in a continuous frame. `packtools/regions.py`'s `Region.contains()` is a
plain `lon_min <= lon < lon_max` check with **no antimeridian/wraparound
handling**. No value of `lon_min`, however far west, ever matches a
positive-longitude point sitting on the other side of the dateline — this
needs a code change to `Region`/`regions.yaml`'s schema (a `wraps: true`
flag, or a second box), not a config edit. Scoping that is a separate,
smaller follow-up than either number above; flagged, not sized here.

## 3. Recommendation

1. **Adak-class gap (6 airports, 5 closeable by geometry alone):** the
   measured cost is small enough (~210-328 MB against an 8-15 GB region)
   that size is not the blocking question — it never really was, once
   measured. The real decision is **where the fix lands**:
   - **Add a new `aleutians` region** (same lat range as `alaska`, 51-72;
     `lon_min`/`lon_max` roughly [-180, -170]) rather than widening `alaska`
     in place. Widening `alaska` silently grows the download for every
     device already tracking that region at the next terrain edition; a new
     region is opt-in — existing `alaska` subscribers see no change, and the
     ~210 MB only lands on a device that asks for it. Given the size is
     trivial either way, this is a subscriber-expectations argument, not a
     cost one, but it costs nothing to take the opt-in path.
   - Do **not** attempt to fix Shemya/Attu by widening a box (§2d) — track
     it as separate, small antimeridian-support work if it's ever wanted.
2. **Hawaii (15 d-TPP / 29 real-airport gap, bigger than Adak's):** out of
   this issue's literal scope (it names Alaska/Aleutians), but it surfaced
   from the same measurement and is a materially bigger hole — Honolulu
   International among them. Recommend a follow-up issue to size a
   dedicated `hawaii` region the same way this document sized `aleutians`.
3. **The 788/3,197 unmatched-ident anomaly (§1, "a larger, separate
   anomaly"):** recommend a follow-up issue — likely a `local_code` vs.
   `ident` matching gap in `airport_region_map()`, unrelated to
   `regions.yaml` geometry, and bigger by airport count than everything
   else in this document combined.
4. **This document does not change `regions.yaml`.** Per the issue: the
   sizes are now on the table; the region-set decision is the board's.

## Decision (2026-09-23)

Bill chose **widen `alaska` in place** over this document's opt-in
`aleutians` recommendation, via AER-1988's decision card. `regions.yaml` was
already being independently widened to `lon_min: -177` under a
near-duplicate issue, AER-1990 (filed separately by Elon off the same PA11
finding) — see `docs/aer-1990-alaska-region-gap.md` and PR
https://github.com/billmallard/makerplane-data/pull/100 for the
implementation, test, and full write-up of the chosen approach. No further
`regions.yaml` change is tracked under AER-1988; §3.1's `aleutians`-region
option was not taken.
