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
`APT_structure.csv` was **refreshed for AIRAC 2609** (effective 2026-09-03) on
2026-08-08 — see the acknowledgement note below.

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

**The expected firing FIRED (AIRAC 2609, refreshed 2026-08-08).** When the FAA
posted the 03 Sep 2026 cycle, the airports structure diff interrupted the build
for three consecutive nights (runs on 2026-08-06/-07/-08) with exactly the
predicted drift in `APT_RWY`:

```
  - REMOVED  APT_RWY.PCN                     ((3,0) NUMBER Yes)
  + ADDED    APT_RWY.PAVEMENT_CLASSIFICATION (3 VARCHAR Yes)
  + ADDED    APT_RWY.PCN_PCR_NUMBER          ((4,0) NUMBER Yes)
```

`PCN` was renamed to `PCN_PCR_NUMBER` and widened `(3,0)` -> `(4,0)`, and a new
`PAVEMENT CLASSIFICATION` (PCN-vs-PCR indicator) column was added. None of the
three is a column the airports builder reads, so this was a **snapshot refresh
only** — `APT_structure.csv` updated to the 2609 `APT_RWY` structure, no builder
change. The row values above are verbatim from the guard's diff (which reads the
downloaded FAA `APT_CSV_DATA_STRUCTURE.csv`); the guard compares by row *set*, so
the two new rows' position in this file is cosmetic. Tracking: issue #31. This
was the guard's production acceptance test — it passed.
