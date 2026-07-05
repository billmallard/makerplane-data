<!-- SPDX-License-Identifier: Apache-2.0 -->
# Committed NASR CSV structure snapshots

These are the reference input-schema snapshots for the pipeline schema guard
(`packtools/schema_guard.py`, check 1). Each is a FAA
`*_CSV_DATA_STRUCTURE.csv` reduced to only the rows for the CSV files this
pipeline consumes, kept verbatim (column, max length, data type, nullable).

| Snapshot | From | Rows kept (consumed files) |
|---|---|---|
| `APT_structure.csv` | `APT_CSV_DATA_STRUCTURE.csv` | APT_BASE, APT_RWY, APT_RWY_END |
| `NAV_structure.csv` | `NAV_CSV_DATA_STRUCTURE.csv` | NAV_BASE |
| `FIX_structure.csv` | `FIX_CSV_DATA_STRUCTURE.csv` | FIX_BASE |
| `AWY_structure.csv` | `AWY_CSV_DATA_STRUCTURE.csv` | AWY_BASE |

**Source cycle: AIRAC 2607** (effective 2026-07-09), seeded 2026-07-05 from the
extracted inputs under `work/localwork/{airports,navaids}-conus/2607/extracted/`.

The guard normalizes (strips) each field on load, so the FAA's incidental
whitespace quirks (e.g. `FIX_BASE` ships `COUNTRY_CODE  `) do not read as a
change; a real rename, retype, or width change does.

## Refreshing (the acknowledgement gate)

When a nightly run goes red on a **structure diff**, that is by design: the FAA
changed an input schema under us. The refresh is the human acknowledgement:

1. Read the guard's `±` diff and, usually, the FAA Data Product Notice that
   announced it (see `docs/nasr_2601_dpn_prep.md`).
2. Decide whether the builder must change too (a new column we now want to
   read; a rename we must follow). If so, fix the builder (in pyEfis) first.
3. Re-seed the affected snapshot from the new cycle's downloaded structure
   file, filtered to the consumed rows (the one-liner the seed used), and
   commit it in a focused PR that references the DPN.

**One firing is already expected:** when the FAA posts the 03 Sep 2026 cycle,
`APT_RWY` gains a `PAVEMENT CLASSIFICATION` column and its `PCN` widens from
`(3,0)` to `(4,0)` (NASR 26-01 DPN). The airports structure diff will correctly
interrupt the build; refresh `APT_structure.csv` then (or earlier from the FAA
CSV *test* files, if they post first). We read no pavement data, so no builder
change is needed -- just the snapshot refresh. Treat it as the guard's
production acceptance test.
