<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Airspace from FAA CIFP (`airspace-conus`)

AER-4445 / AIRSP1, makerplane-data#134. Builder:
`packtools/build/airspace_cifp.py`; parser: `arinc424.iter_airspace_records`.

## What it is

FAACIFP18 section U carries the US controlled airspace (`UC`: Class B, C
and D) and restrictive airspace (`UR`: restricted, prohibited, MOA,
warning, alert, and an ARINC "unspecified" type the FAA uses for SFRAs,
flight-free zones and the like). It is public domain and arrives in the zip
`procedures-conus` is already built from, on the same AIRAC cycle.

Cycle 2611, as built:

| Section | Records | Sectors (= pack rows) |
|---|---:|---:|
| `UC` Class B (type `T`) | 7,150 | 367 |
| `UC` Class C (type `A`) | 3,620 | 340 |
| `UC` Class D (type `Z`) | 2,365 | 574 |
| `UR` (primary records; plus 1,176 controlling-agency continuations) | 29,360 | 1,586 |
| **Total** | 42,495 | **2,867** |

The issue's census (cycle 2605) had 13,223 `UC` and 30,130 `UR` records;
2611 has 13,135 and 30,536. The counts move each cycle, so the gate
reconciles against the file the pack was built from (ASP-003), not against
a fixed number.

The pack writes the `airspaces` table the openAIP builder
(`packtools/build/airspace.py`) defines, so one pyEfis reader can serve both.
Codes are openAIP's own (its airspace schema, fetched 2026-10-31); the
mapping and its limits are in the builder's module docstring. In short:
`icao_class` 1/2/3 for Class B/C/D and 8 for special-use; `type` only where
openAIP has an exact equivalent (restricted 1, prohibited 3, alert 17,
warning 18), otherwise 0 with the ARINC type in `properties_json`; limits
as value + unit (1 ft, 6 FL) + reference (0 GND/AGL, 1 MSL, 2 STD), and
`UNLTD` as a bare value. Every source record is kept verbatim in
`properties_json.records`.

The area code is kept, not used as a filter: the FAA files Alaska under
`CAN`, Hawaii and Guam under `PAC`, Puerto Rico and the USVI under `LAM`.
The build keeps any record whose ICAO region is a US one and fails on any
other. In 2611 that covers every record.

## Conventions in the source that a reader has to know

These come from the data, not the builder. Each was found by comparing the
whole 2611 build against the FAA's ADDS data (next section):

1. **"Up to but not including" is written one foot below.** A Class D
   "surface to but not including 3,000 MSL" is `02999`; "above 1,000 MSL" is
   `01001`. MOA ceilings are `17999`. Stored as written.
2. **Sectors overlap, and the lowest floor wins.** ADDS draws a Class B
   outer ring as an annulus (a polygon with a hole). CIFP gives the full disk
   and relies on the inner sectors, which have lower floors, sitting on top of
   it. KMCI area D is the clearest case: identical outline, IoU 0.49,
   because ADDS's polygon has the hole. The floor at a point is the lowest
   floor of all the sectors that cover it.
3. **Zero-height sectors mark a hole.** 16 sectors in 2611 run from `GND` to
   `00000A`. R-7201A's sector B is a 3 NM circle that ADDS draws as the
   interior ring of R-7201A. ROBERTS MOA has another. The builder sets
   `properties_json.zero_height`. A reader should treat these rows as
   exclusions, not draw them as airspace.
4. **Exclusions can also be keyholes.** YUKON 2 MOA sector A traces its
   outer edge, runs in to wrap around the excluded areas, and comes back out.
   ADDS keeps those areas as separate exclusion features.
5. **Out-and-back spikes.** Seven sectors (KOKC Class C A-D, KSTL Class B
   B-C, KTIK D) retrace a vertex a few metres out and back, e.g. KOKC
   35.37719 / 35.37716 / 35.37719 N on one meridian. A ring like that is not
   simple. The builder drops the spike from the geometry, keeps it in the
   records and counts it in `properties_json.spikes_removed`.

## Differential against FAA ADDS

ADDS (`adds-faa.opendata.arcgis.com`, the `Class_Airspace` and
`Special_Use_Airspace` feature services) publishes the same airspace as
sector polygons with limits. It is built from NASR, not CIFP: same agency,
different product and pipeline. So it can catch our parse and densify
errors, but not an error both FAA products share. Script:
`scripts/airspace_adds_diff.py`. Results, images and the commands that made
them: `docs/images/aer-4445/`.

Each pack row is matched to the ADDS sector(s) of the same airspace with the
same floor and ceiling (within 1 ft, see convention 1). The match is scored by
area overlap (IoU) and by the Hausdorff distance between the outlines.

| | Rows | Matched | IoU >= 0.99 | IoU >= 0.95 | Median IoU | Median / p95 Hausdorff |
|---|---:|---:|---:|---:|---:|---:|
| `UC` | 1,281 | 1,281 | 1,239 | 1,273 | 0.9969 | 0.019 / 0.048 NM |
| `UR` | 1,586 | 1,523 | 1,447 | 1,513 | 0.9991 | 0.011 / 0.133 NM |

Not matched: the 56 `UR` rows of ARINC type `U` (ADDS's special-use layer
does not carry SFRAs and flight-free zones), and 7 `UR` rows whose limits
differ from every ADDS sector of that name. Two of those 7 are the
zero-height holes.

The issue's spot checks:

| Airspace | Rows | IoU | Hausdorff |
|---|---|---|---|
| KSBA Class C | A (surface, 5 NM), B (1,500 shelf, 10 NM) | 0.9964, 0.9918 | 0.022, 0.052 NM |
| KLAX Class B | 14 sectors, areas A to N | 0.9986 to 0.9998 | 0.001 to 0.011 NM |
| BIRCH MOA | A, B (500 AGL), C (3,001 MSL) | 0.9975, 0.9987, 0.9997 | 0.031, 0.018, 0.001 NM |

### Outliers, and what they are

| Row | IoU | What it is |
|---|---:|---|
| KBGR Class C A | 0.00 | **The CIFP record disagrees with ADDS.** Sectors A (surface) and B (700 MSL) use the same vertices. A's arc is coded `L` (counter-clockwise), so it sweeps 31 degrees and A comes out as the same small notch as B. ADDS has the surface area as the 5 NM circle minus that notch, which is what A's arc would give if it were coded `R`. KBGR B, C and D match ADDS at IoU 0.99 or better, and the R/L convention is confirmed by every other arc, so this looks like a source error. It is **not corrected** here: the pack carries what CIFP says. |
| KSAN Class B P, V | 0.00 | Shares an exact edge with the ADDS sector of the same limits, on the other side. The two products split this part of San Diego into sectors differently. |
| KMCI Class B B, D | 0.66, 0.49 | ADDS annulus against CIFP full disk (convention 2). |
| YUKON 1, 2, 4 MOA A | 0.85 to 0.94 | Keyhole exclusions (convention 4). |
| R-7201A A | 0.93 | ADDS draws the hole as an interior ring; CIFP uses a zero-height sector B (convention 3). |
| P-56 B, P-73, P-67, R-5801 | 0.84 to 0.94 | Small areas with a Hausdorff under 0.04 NM, so a small absolute difference costs a lot of IoU. |

## Gate checks

`ASP-001` (every geometry a closed, simple polygon), `ASP-002` (no floor above
its ceiling) and `ASP-003` (one row per source sector) are in
`packtools/report.py`; see `docs/pack_report.md`. All three pass on 2611. The
airspace kind stays in `report.UNGATED` until QA accepts these as a gate.
Separately, `ACC-001` has no airspace probe, because pyEfis has no airspace
reader yet (CAP-204). So a build record of this kind cannot clear the gate
yet, and `airspace-conus` is registered with `implemented=False`. See the
comment in `packtools/sources.py`.

## Not done here

- Rendering (CAP-204, pyEfis), and NOTAM / TFR airspace.
- Any correction of source data (KBGR A above).
- The first publish. That needs the kind on `main`, QA's acceptance of
  ASP-001..003, a decision on ACC-001 for a kind no reader consumes yet, and
  an approved flip of `implemented`.
