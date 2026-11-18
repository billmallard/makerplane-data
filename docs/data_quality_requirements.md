# Data quality requirements per pack kind

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

CERT10b (AER-5978). For each pack kind, the seven DO-200B data quality
attributes, what we state for each, and which check in the publish gate's
catalogue (`packtools/report.py`, [pack_report.md](pack_report.md)) protects
it. **This is not a compliance claim.** DO-200B binds nothing here
([do200b_applicability.md](do200b_applicability.md) section 2); the
standard's own text was not read (section "Sources" there). The table is a
map of where our checks are and where they are not.

## The attributes

The seven attributes, as the secondary sources in
[do200b_applicability.md](do200b_applicability.md) section 1 describe them:

| Attribute | What it asks of the data |
|---|---|
| accuracy | how close a value is to the true value |
| resolution | the smallest unit a value is stored or published to |
| assurance | confidence the data was not corrupted or changed on the way |
| traceability | the data can be traced back to its origin |
| timeliness | the data is current for the time it is used |
| completeness | everything that should be there is there |
| format | the data is in the structure its consumer expects |

## Which check protects which attribute

Every check in every kind's catalogue names the attributes its failure
would be evidence against: `PROTECTS` in `packtools/report.py`, printed by
`packtool report --list-checks` on the `protects:` line. The table below is
`report.dq_matrix()`; `tests/test_dq_attributes.py` fails if the two
disagree, so a check added to a catalogue without an attribute, or a table
edited by hand, does not land quietly. `none` is an attribute no check in
that kind's catalogue covers.

| Kind | Accuracy | Resolution | Assurance | Traceability | Timeliness | Completeness | Format |
|---|---|---|---|---|---|---|---|
| airports | `GEO-001`, `XS-001` | none | `SQL-001`, `UNQ-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003` |
| airspace | `ASP-002` | none | `SQL-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `ASP-003` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `ASP-001` |
| cifp | none | none | `SQL-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003` |
| highways | none | none | `SQL-001`, `IDX-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `OSM-001`, `OSM-002`, `OSM-006`, `OSM-005` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `OSM-004`, `OSM-003` |
| navaids | `GEO-001`, `GEO-002`, `GLD-001` | none | `SQL-001`, `UNQ-001`, `UNQ-002` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `SG-002`, `SG-004` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `SG-001`, `SG-002`, `SG-003` |
| navdata | `GEO-001`, `GEO-002`, `XS-001`, `GLD-001` | none | `SQL-001`, `UNQ-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `SG-002`, `SG-004` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `SG-001`, `SG-002`, `SG-003` |
| obstacles | `GEO-001`, `GEO-002`, `XS-002`, `GLD-001` | none | `SQL-001`, `UNQ-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `OBS-001` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003` |
| plates | none | none | none | `PACK-002`, `PACK-003`, `PACK-004` | none | none | `PACK-001`, `ACC-001` |
| procedures | `GEO-001`, `GEO-002`, `PROC-MSA-002`, `PROC-MSA-003`, `PROC-MORA-003`, `PROC-GLD-001`, `PROC-DIFF-001` | none | `SQL-001`, `UNQ-001`, `PROC-AWY-001`, `PROC-AWY-002`, `PROC-FAS-002`, `PROC-MORA-002` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `PROC-TBL-001`, `PROC-FIX-001`, `PROC-MSA-001`, `PROC-MSA-004`, `PROC-FAS-001`, `PROC-FAS-003`, `PROC-MORA-001`, `PROC-MORA-004` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `PROC-TBL-001` |
| rivers | none | none | `SQL-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `OSM-001`, `OSM-002`, `OSM-006`, `OSM-005` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `OSM-004` |
| terrain | `TER-002`, `TER-004`, `TER-005` | none | none | `PACK-002`, `PACK-003`, `PACK-004` | none | `TER-006`, `TER-003`, `TER-007`, `TER-008` | `PACK-001`, `ACC-001`, `TER-001` |
| water | none | none | `SQL-001`, `IDX-001` | `PACK-002`, `PACK-003`, `PACK-004` | none | `ROW-001`, `ROW-002`, `ROW-003`, `OSM-001`, `OSM-002`, `OSM-006`, `OSM-005` | `PACK-001`, `ACC-001`, `SQL-001`, `ROW-003`, `OSM-004` |

A check listed under accuracy is evidence *against* a wrong value of the
kind it looks for; none of them shows a value is right. Each check's
`cannot_catch` line says what it misses.

## What protects an attribute outside the catalogue

Two attributes have no check in any catalogue. That is partly by design:
they are carried by the pipeline, not by the record.

- **Timeliness.** Cyclical kinds (navdata, navaids, obstacles, procedures)
  carry an `effective`/`expires` window from the AIRAC (28-day) and DOF
  (56-day) cycle math (`packtools/cycles.py`), and the device annunciates a
  pack past its window (`pyefis_data/core.py` `PackStatus`, the `DATA`
  flag). The other kinds are edition-tagged and never expire. No check
  compares a pack's cycle with the source edition it was built from;
  `PACK-004` records the source edition but does not judge it.
- **Assurance, end to end.** The signed manifest carries each pack's
  sha256, and the device verifies both before it installs
  ([CLAUDE.md](../CLAUDE.md), "Safety contract"). That is why terrain and
  plates, which have no assurance check of their own, are not unprotected
  on the way to the aircraft. The catalogue's assurance checks look for
  corruption the build itself introduced, before signing.
- **Resolution.** Nothing, in or out of the catalogue. See the gaps below.

## What we state per kind

What each kind's requirement is, where one is stated at all. We
originate none of this data, so accuracy and resolution are the source's;
we do not restate or re-derive them (do200b_applicability.md section 4).
Where the pack drops the source's own statement, that is a gap and is
listed below.

| Kind | Source | `origin` label | Accuracy and resolution | Timeliness |
|---|---|---|---|---|
| navdata | FAA NASR | federal (default) | as NASR publishes; no per-element accuracy in the pack | AIRAC 28-day window |
| navaids | FAA NASR | federal (default) | as NASR publishes; no per-element accuracy in the pack | AIRAC 28-day window |
| procedures, cifp, airspace | FAA CIFP | federal (default) | as CIFP publishes (ARINC 424 field resolution) | AIRAC 28-day window |
| obstacles | FAA DOF | federal (default) | DOF assigns each verified obstacle a horizontal and a vertical accuracy code (for example 1 = 20 ft horizontal, A = 3 ft vertical; [DOF README](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dof/media/DOF_README_11-20-2017.pdf)). The pack does not carry them | DOF 56-day window |
| plates | FAA d-TPP | federal (default) | the plate PDFs as published | AIRAC 28-day window |
| airports | OurAirports (Canada) | community (default) | none stated by the source | edition-tagged |
| water, highways, rivers | OpenStreetMap | community (default) | none stated by the source | edition-tagged (quarterly) |
| terrain | Copernicus DEM GLO-30 | none by default; every live pack is labelled `federal` (below) | absolute vertical under 4 m LE90, absolute horizontal under 6 m CE90, 1 arc-second latitude spacing ([Copernicus DEM product handbook](https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf), figures as quoted in search abstracts, not re-read in full) | edition-tagged (`2024edr<N>`) |

Completeness and format are the builder's: the tables and members the
builder writes (format) and the row floors and edition-to-edition diffs in
the catalogue (completeness).

## The GLO-30 `origin: federal` label

**Checked 2026-11-18: every live terrain pack is labelled `federal`, and by
the label's own definition it should not be.**

- The live manifest (generated 2026-11-17) lists 16 terrain entries, the
  eight regions plus the mosaic at `2024edr3` and `2024edr4`. All 16 carry
  `origin: federal`. The `terrain-us-west` `2024edr4` record's `PACK-003`
  reads `federal (passed with --origin)`.
- `report.ORIGIN_BY_KIND` gives terrain no default, on purpose ("Terrain
  (Copernicus) is neither federal nor community: pass --origin"), so the
  label was a person's choice at each publish. The cloud stage preview
  (`packtools/cloud/entrypoint.sh`, the publish-gate preview) passes
  `--origin federal` literally.
- do200b_applicability.md section 5 defines `federal` as FAA-originated
  data with a published cycle. Copernicus DEM is a European Union product
  built from the TanDEM-X mission; it is not federal and has no cycle. It is
  not `community` either (it has an authoritative originator and stated
  accuracy), nor `user-supplied`. None of the three values fits.
- `PACK-003` cannot catch this, and says so: its `cannot_catch` is
  "Whether the label matches where the data really came from".

Nothing is changed here. Fixing it is a label decision, and reaching the
live packs is a publish: the label is bound into each pack's signed record,
so a correction lands only with the next terrain edition. The options are
for Bill on AER-5978:

1. Add a fourth value for agency-originated data with a stated accuracy
   that is not FAA (for example `agency`). It is build-side only
   (`manifest.ORIGINS`; the device does not read `origin`), and the stage
   preview and terrain default would move with it.
2. Keep three values and widen `federal` to mean any government
   originator. Cheapest, but a pilot reading `federal` on European data is
   misled, which is the opposite of what the label is for.
3. Relabel terrain `community`. Wrong for the reasons above.

## Gaps this table shows

Proposals, not changes. A new or redefined gate check is QA's to accept
(AER-4102); none is added here.

1. **Resolution has no check in any kind.** For terrain, `TER-001` accepts
   any square int16 grid, so a native tile at 1201 x 1201 (3 arc-second)
   would pass; a check that native tiles are 3601 x 3601 would close it.
2. **The obstacles pack drops DOF's accuracy codes.** pyEfis
   `tools/build_obstacle_db.py` reads position, AMSL, AGL, type, quantity,
   lighting, marking, state and city, and no accuracy or verification
   column. The source's per-obstacle accuracy statement does not reach the
   device. Carrying it is a pack schema change in pyEfis.
3. **Accuracy has no check for the OSM kinds or cifp.** OSM states no
   accuracy, so there is nothing to compare against; this is expected, and
   is what the `community` label tells the reader.
4. **No check ties a pack's cycle to its source edition** (timeliness,
   above).
5. **Plates have only the common checks**, and stay ungated
   (`report.UNGATED`).
