<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# `FAACIFP18` airspace fixture (AER-4445 / AIRSP1)

250 real section-U records, extracted verbatim (no fields altered), plus the
five header lines, from the live FAA CIFP cycle **2611** (effective
2026-10-29), downloaded from
`https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip` on 2026-10-31.
FAA CIFP data is a US Government work (public domain); see
`docs/LICENSE-AUDIT.md`.

## What is in it

| Selection (record prefix) | Records | Why |
|---|---:|---|
| `HDR01`-`HDR05` | 5 | file-cycle header |
| KSBA Class C (`SUSAUCK2AKSBA`) | 5 | the issue's spot check: a 5 NM circle (`CE`) and an outer shelf with clockwise and counter-clockwise arcs |
| KLAX Class B (`SUSAUCK2TKLAX`) | 88 | the issue's many-sector Class B: 14 sectors, areas A to N |
| KBGR Class C (`SUSAUCK6AKBGR`) | 18 | sector A disagrees with the FAA's ADDS data (see `docs/airspace_cifp.md`); kept so the parse of that record is pinned |
| KOKC Class C (`SUSAUCK4AKOKC`) | 84 | a source out-and-back spike (sector A, seq 70-100) the builder removes |
| PAEI Class D (`SCANUCPAZPAEI`) | 1 | Alaska, filed under area code `CAN`; a single-record circle |
| DELTA 2 MOA (`SCANURPAMDELTA2`) | 7 | an Alaska MOA under area `CAN`, with a `C` (controlling agency) continuation |
| R-7201A (`SPACURPGR7201A`) | 3 | Guam, area `PAC`; a flight-level ceiling, and a zero-height (GND to 0 AGL) sector B that marks a hole |
| R-2508 (`SUSAURK2R2508`) | 29 | an `UNLTD` ceiling and rhumb-line (`H`) edges |
| ROBERTS MOA (`SUSAURK2MROBERTS`) | 15 | a CONUS MOA with a continuation and a zero-height sector |

## Regenerating or extending

Download the cycle, unzip `FAACIFP18`, and keep the header lines plus every
line starting with one of the prefixes above (each prefix is the record's
first 13 to 18 columns, padded with spaces where the designation is shorter
than its field).
