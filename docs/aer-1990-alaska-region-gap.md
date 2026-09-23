<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-1990: the `alaska` region's `lon_min` gap

PA11 (AER-1610) found that Adak Island (PADK) — a real FAA airport with real
d-TPP approach plates — sits west of `packtools/regions.yaml`'s `alaska`
region `lon_min` (`-170`), so it matched no region at all. That was pinned as
a test, deliberately not patched, in PR #97 (`test_airport_region_map_real_
adak_falls_outside_the_alaska_region`), because `regions.yaml` is shared by
terrain, water and highways — widening it changes what every one of those
packs contains and how large it is, for every device that pulls them. This
doc is the measurement AER-1990 asked for before making that call.

Everything below is measured against **live sources** on 2026-09-23, not
estimated: the real FAA d-TPP cycle-2609 metafile, the real OurAirports
worldwide airport table, the real Copernicus GLO-30 S3 bucket (object sizes,
not modeled), and the real production manifest at
`https://navdata.aerocommons.org/manifest.json`.

## 1. How many airports are affected

Parsed the live cycle-2609 `d-tpp_Metafile.xml` (3,197 distinct airport
idents, matching `docs/dtpp_plates_spike.md`'s figure exactly) and joined
every one against OurAirports' live `airports.csv` by ident/GPS-code/local-
code — **100% match rate**, all 3,197 resolved to real coordinates.

Bucketed against the five plate-relevant `regions.yaml` boxes
(`us-west`/`us-central`/`us-east`/`us-south`/`alaska`, the same set
`packtools/make_plates.py._PLATE_REGIONS` uses):

| | Count |
|---|---:|
| Matched to at least one region (current `regions.yaml`) | 3,152 |
| **Unmatched** (no region box reaches them) | **45** |

**This is a materially different number from the "268 Canada/Pacific/int'l"
figure `docs/dtpp_plates_spike.md` §1.3 and `docs/plates.md` cite** — flagging
the discrepancy rather than silently overriding the older doc. My join has a
100% OurAirports coordinate-match rate on today's cycle; I don't have
visibility into PA10's exact join method to explain the gap, but 45 is what a
live rerun produces today and every one of the 45 idents below is a real,
resolved coordinate, not a join failure.

### The 45 split cleanly into "reachable by widening `alaska`" and "not"

**5 reachable** by moving `alaska`'s `lon_min` from `-170` to `-177` — all
real central-Aleutian / Bering-Sea Alaska airports:

| Ident | Name | lon |
|---|---|---:|
| PADK | Adak Airport | -176.643 |
| PAAK | Atka Airport | -174.206 |
| PAGM | Gambell Airport | -171.733 |
| PASA | Savoonga Airport | -170.493 |
| PASN | St. Paul Island Airport | -170.223 |

**40 are not reachable by any reasonable single-box widen** — they're
distinct, far-flung clusters, each of which would need its own new region if
this repo ever decides to cover them, not a stretch of an existing box:

| Cluster | Count | e.g. |
|---|---:|---|
| Hawaii | 15 | PHNL (Honolulu), PHOG (Kahului), PHTO (Hilo)... |
| Puerto Rico / US Virgin Islands | 8 | TJSJ (San Juan), TIST (St. Thomas)... |
| Guam / N. Mariana / Palau / Marshall Is. / Micronesia | 14 | PGUM (Guam), PTKK (Chuuk)... |
| Midway Atoll | 1 | PMDY |
| American Samoa | 1 | NSTU (Pago Pago) — southern hemisphere |
| **Eareckson Air Station (Shemya), Alaska** | 1 | **PASY**, +174.114 lon |

PASY is the interesting one: it's genuinely Alaska (Near Islands, Aleutian
chain) and has real d-TPP plates, but it sits at **positive** longitude
(east of the antimeridian) while every other Aleutian airport is negative.
`packtools/regions.py`'s `Region.contains()` is a plain
`lon_min <= lon < lon_max` check with no dateline wraparound, so **no bbox
widen, however far west, can ever reach PASY** — it would need either a
second, wraparound-aware region or a code change to `Region`. One airport;
flagged here rather than silently dropped, not fixed in this pass.

## 2. What widening `alaska` costs — terrain

Terrain is the only one of the three pack kinds this issue named where
`alaska` currently produces anything at all (see §3). Measured real
Copernicus GLO-30 COG tile sizes from `https://copernicus-dem-30m.s3.
amazonaws.com` (the actual upstream `make_terrain.py` tiles are sourced
from) for every 1-degree cell in the newly-added longitude band, `lat`
51–71 (the `alaska` `lat` range):

| Widen | New tiles | Raw COG bytes added | vs. live `terrain-alaska` pack (4,814,888,751 B) |
|---|---:|---:|---:|
| `lon_min` -170 → **-177** (reaches all 5 airports above) | 50 | 356.0 MB | **≈ +3.3–4.1%** (compressed estimate, see below) |
| `lon_min` -170 → -180 (all the way to the dateline) | 70 | 577.6 MB | ≈ +5.4–6.0% |

The compressed-pack estimate applies `docs/terrain.md`'s own already-measured
DEFLATE-6 compression range on real GLO-30 tiles from this tree (45–71%
reduction, "blended NA ≈ ~50% smaller") to the raw byte counts above — not a
fresh guess, the repo's own prior real measurement applied to today's real
source bytes.

**Going all the way to -180 is not just bigger, it's wrong.** Of the extra
221.6 MB (-180 vs -177), the overwhelming majority is **20 tiles at lat
65–71, lon -178 to -180** — real, sizeable land tiles (several 15–22 MB each,
the same order of magnitude as a full CONUS land tile). At that latitude
band, longitude -178 to -180 is Chukotka Peninsula, Russia, not Alaska. A
rectangular bbox can't hug the Aleutian chain without also spanning that
latitude band's full width — widening far enough to reach -180 pulls a slice
of Russian Far East terrain into a pack labeled "Alaska." `-177` avoids this
entirely (its northernmost real tiles at that longitude are still Alaska —
St. Lawrence Island, Nunivak-area coast) while still catching every named
airport.

## 3. What widening `alaska` costs — water and highways

**Nothing. There is no `alaska` water or highways pack today, at any
longitude.** Checked the live production manifest:

- `water-na`'s `regions` list is `conus, us-west, us-central, us-south,
  us-east, canada-west, canada-east` — no `alaska`.
- `highways-conus`'s `regions` list is `conus` only.
- `packtools/make_roads.py`'s `CONUS_STATES` (the Geofabrik per-state fetch
  list `build_highways`/`build_water_db` consume) never included Alaska —
  it's a CONUS-only list by name and by content.

So Alaska — all of it, PADK included, both before and after this widen —
gets no water or highways coverage from this repo at all. Widening
`regions.yaml`'s `alaska` bbox changes zero bytes downloaded or built for
either kind, because neither kind reads that box today. Building a real
`water-alaska`/`highways-alaska` pack is a separate, much larger gap (all of
Alaska, not just the Aleutians west of the old `lon_min`) that predates and
is out of scope for this issue — noting it so it isn't lost, not proposing to
fix it here.

## 4. Recommendation

**Widen `alaska`'s `lon_min` from `-170` to `-177`.** Reasoning:

- Catches all 5 real, named FAA airports with published plates that the old
  box missed, for a measured, small terrain cost (+3.3–4.1% on one existing
  pack) and a real zero cost on water/highways (neither builds Alaska at
  all today).
- Explicitly **not** widened to `-180` — the extra reach is proportionally
  larger (nearly double the added cost) and a meaningful fraction of it is
  land in Russian territory, not Alaska. `-177` is the point where "widen
  to reach real US airports" stops and "grab everything up to the dateline"
  starts pulling in the wrong country.

**Accept and document the other 40** (Hawaii, Puerto Rico/USVI, Guam/
Micronesia/Marshall Is., Midway, American Samoa, and PASY). None of them is
reachable by widening any existing box — each is its own geographically
distinct cluster, thousands of miles from the nearest current region, and
would need a dedicated new region (and, for water/highways, an entirely new
non-CONUS Geofabrik/OSM source list that doesn't exist in this repo today).
That's a materially bigger decision than this issue's Adak-scoped ask — new
region(s), new coverage, new device download footprint for every one of
those clusters — and belongs as its own proposal if/when there's demand for
US-territory coverage beyond CONUS/Alaska/Canada/Mexico, not bundled into
this fix.

PASY (Shemya) is called out separately above because it's the one item in
the "other 40" that actually *is* Alaska — it just can't be reached by any
bbox widen, only a dateline-aware region shape or a second micro-region.
Low priority (one airport), named here rather than silently dropped.

**Not done here (explicitly out of scope per the issue):** publishing. This
regions.yaml change ships no pack; the next terrain-alaska build (whenever
that's next dispatched) is what actually picks up the wider tile set, and
per `CLAUDE.md`'s merge-before-publish rule that build must happen after
this change reaches `dev`, never before.
