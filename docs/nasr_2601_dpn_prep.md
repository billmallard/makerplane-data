# NASR 26-01 DPN prep — pipeline readiness for the 03 Sep 2026 format changes

Status: **implemented 2026-07-05** — both parts built, tested, and committed
(not yet pushed). Analyzed + implemented 2026-07-05.
Source notice: [NASR_26-01_DPN_10.1_Subscriber_Enhancement.pdf](NASR_26-01_DPN_10.1_Subscriber_Enhancement.pdf)
(FAA Data Product Notice, 27 May 2026).

## Implementation status (2026-07-05)

- **§4 SP filter** — done in pyEfis `tools/build_navaid_db.py` on
  `display-changes` (commit `a206609`): `EXCLUDE_AWY_DESIGNATIONS = {"SP"}`,
  skip count always printed, new `tests/tools/` package (7 tests). Verified a
  no-op on the real 2606/2607 cycles (row counts unchanged, skipped 0) and that
  a synthetic SP route is dropped while V/AT routes are kept.
- **§8 schema guard** — done in makerplane-data on `feat/accounts-auth`
  (commit `2aeb480`): `packtools/schema_guard.py`, committed `packtools/schemas/`
  snapshots (seeded from real 2607), wired into `run_cyclical` with a hard
  `PipelineFailure` (exit 1) distinct from the benign fetch WARN, and
  `tests/test_schema_guard.py`. Full suite green (149 tests). Verified
  end-to-end: real 2607 inputs pass; a simulated 03 Sep APT change (PAVEMENT
  CLASSIFICATION + PCN `(3,0)`→`(4,0)`) fires the structure diff and an unknown
  AWY_DESIGNATION fires the enum check.
- **Correction folded in:** the observed `AWY_DESIGNATION` set is the nine
  values in §4.1 (no `Y`/`N` — those are `REGULATORY`); the guard's enum set is
  those nine plus `SP`.
- **Remaining (owner action):** push both branches; confirm a green nightly
  before ~06 Aug; refresh `APT_structure.csv` when the 03 Sep cycle (or FAA CSV
  test files) posts and the airports build correctly goes red.

**Deadline: the change must be live before ~06 Aug 2026**, not 03 Sep.
The daily orchestrator builds *current and next* AIRAC cycles
(`packtools/run_cyclical.py:58-62`), so the first new-format files enter
the pipeline as soon as the FAA posts the 03 Sep cycle — historically up
to four weeks early, i.e. any time after the 06 Aug cycle boundary.

**Where the code change lands:** the CSV parsing lives in **pyEfis**, not
this repo — `cyclical.yml:15-17` checks out `billmallard/pyEfis` @
`display-changes` and runs `tools/build_navaid_db.py` /
`build_airport_db.py` from there (`packtools/build/__init__.py`).
Implement in pyEfis on `display-changes` (or whatever `PYEFIS_REF`
points to when you start); this doc stays here because the pipeline and
its rollout are makerplane-data concerns.

## 1. What the DPN changes

Effective with the 03 September 2026 AIRAC cycle, in both TXT and CSV
subscriber files:

1. **APT** — new PAVEMENT CLASSIFICATION column (PCN vs PCR); PCN/PCR
   number field 3→4 chars. TXT: RMK fields after col 50 shift 5 chars.
2. **AWY/ATS (TXT only)** — new MEA GAP column at end of AWY1/ATS1 records.
3. **AWY/ATS** — new airway designation value `SP` = SPECIAL ROUTE:
   non-regulatory ZK helicopter-air-ambulance RNAV routes. Per the FAA:
   *"They are not included on public charts. You may not file or use
   these routes without approval from FAA Flight Standards."*
4. **FIX** — fixes on SP routes get a new `SPECIAL ENROUTE` charting type.
5. **PFR** — Designator/Description/Aircraft field-size increases.
6. **PFR** — HSD/LSD route types renamed HPD/LPD.

## 2. What this pipeline actually consumes

From `packtools/sources.py` (all **CSV** family, never TXT):

| Upstream zip | Files parsed | Parser |
|---|---|---|
| `*_APT_CSV.zip` | APT_BASE, APT_RWY, APT_RWY_END | pyEfis `tools/build_airport_db.py` |
| `*_NAV_CSV.zip` | NAV_BASE | pyEfis `tools/build_navaid_db.py` |
| `*_FIX_CSV.zip` | FIX_BASE | same |
| `*_AWY_CSV.zip` | AWY_BASE | same |
| DOF, CIFP (deferred) | — | untouched by this DPN |

Both parsers use `csv.DictReader` and read fields **by column name**
with `.get()` defaults — appended columns and widened fields are
inherently harmless to them.

## 3. Impact matrix

| DPN item | Impact on us | Why |
|---|---|---|
| All TXT-format changes (incl. MEA GAP, RMK column shifts) | **None** | TXT files never consumed |
| PFR changes (field sizes, HSD→HPD/LSD→LPD) | **None** | PFR not consumed |
| APT_RWY.csv PAVEMENT CLASSIFICATION column + PCN width | **None structurally** | DictReader by name; the PCN fields are not read at all (`build_airport_db.py:163-174` reads only RWY_LEN/RWY_WIDTH/SURFACE_TYPE_CODE/RWY_LGT_CODE). Prove with a fixture test (§6). |
| FIX_BASE.csv `SPECIAL ENROUTE` charting type | **None structurally; policy decision documented in §5** | Builder ignores the `CHARTS` column entirely (`build_navaid_db.py:64-73`) |
| **AWY_BASE.csv `SP` designation** | **REAL — action required** | See §4 |

The dangerous property of this DPN for us: **nothing will crash.** The
CSV changes are all name-compatible, so without action the pipeline
will silently ingest the new SP routes and ship them to cockpit moving
maps as ordinary airways. The failure mode is wrong data on a chart
display, not a red CI run — which is why this needs a deliberate change
rather than "we'll see what breaks." §8 adds a pipeline schema guard so
the *next* format change — announced or not — interrupts the build
instead of shipping.

## 4. The required change: filter SP airways out of the navaids pack

### 4.1 Current behavior

`build_navaid_db.py load_airways()` (lines 99–110) ingests **every**
AWY_BASE row into `awy_segments`, keyed only on `AWY_ID` +
`AIRWAY_STRING`. The moving map's airways layer
(`src/pyefis/instruments/map/layers/navaids.py`) draws every segment in
the pack. AWY_BASE already carries the relevant column — observed
header (2607 cycle):

```
EFF_DATE, REGULATORY, AWY_DESIGNATION, AWY_LOCATION, AWY_ID,
UPDATE_DATE, REMARK, AIRWAY_STRING
```

Observed `AWY_DESIGNATION` values in the 2607 cycle (counts, verified by
rebuilding from `work/localwork/navaids-conus/2607/extracted/AWY_BASE.csv`):
V 531, RN 490, J 202, AT 183, PA 73, BF 20, PR 9, G 5, R 2 — **nine**
values, all ≤2 chars (the column's `Max Length` is 2). 2606 is the same
set. `SP` will be a **new value** in this existing column, not a new
column.

**Do not confuse `AWY_DESIGNATION` with `REGULATORY`** (an easy slip, since
both are short AWY_BASE codes): `REGULATORY` carries `Y` (1230) / `N` (285)
— those are *not* designation values. The SP filter and the schema guard
key off `AWY_DESIGNATION` only; the known-value set below is exactly the
nine above plus `SP`, with no `Y`/`N`.

Do **not** filter on `REGULATORY` — existing non-regulatory routes
(the AT/PA oceanic ATS routes, `REGULATORY == "N"`) are legitimately
charted and must keep rendering. The distinguishing property of SP
routes is the designation, and the FAA's own description: not on public
charts, unusable without Flight Standards approval.

### 4.2 Change spec (pyEfis `tools/build_navaid_db.py`)

1. Module-level constant with the rationale in one comment line:

   ```python
   # SP = SPECIAL ROUTE (NASR 26-01 DPN): non-public ZK helicopter routes,
   # "not included on public charts" -- never draw them on the map.
   EXCLUDE_AWY_DESIGNATIONS = frozenset({"SP"})
   ```

2. In `load_airways()`, skip excluded rows and count them:

   ```python
   if (r.get("AWY_DESIGNATION") or "").strip() in EXCLUDE_AWY_DESIGNATIONS:
       skipped += 1
       continue
   ```

   Return the count alongside the segments (or track it on a small
   stats object) and include it in the final summary print, so the
   nightly CI log shows e.g.
   `wrote navaids.sqlite: ... 812 airway segments (skipped 14 special routes)`.
   The count line must appear even when zero — its presence is what
   tells a log reader the filter ran.

3. No sqlite schema change, no pyEfis runtime change, no pack `kind`
   change, no manifest change. (Alternative considered and rejected:
   storing the designation in `awy_segments` and filtering at render
   time. That grows every pack to carry rows no layer will ever draw
   and requires a coordinated pyEfis-runtime change; the public-charts
   principle is a build-time data decision.)

### 4.3 Backward safety

The filter is a no-op on every pre-Sept cycle (no SP rows exist — see
the value counts above). Prove it: rebuild the 2607 navaids pack before
and after the change and diff the three table row counts. Extracted
2606/2607 inputs are already on disk under
`work/localwork/navaids-conus/*/extracted/` in this repo.

## 5. Decided: no FIX_BASE filter (v1)

Fixes on SP routes will appear in FIX_BASE with `CHARTS` =
`SPECIAL ENROUTE` (possibly among other values — `CHARTS` is a
comma-joined multi-value field). We considered dropping fixes whose
only charting type is SPECIAL ENROUTE, and decided **against**, because
the fixes table already knowingly ingests every fix unfiltered —
including large populations that are not on public charts today
(2607 cycle: 4,479 fixes charted only as SPECIAL IAP, 4,129 MILITARY
IAP, 2,044 NOT REQUIRED). A new SP-only filter would be inconsistent
with that standing policy while removing only a handful of rows, and
the fixes layer is off by default and range-gated to ≤20 NM
(`layers/navaids.py` header comment) — a named triangle is not an
airway a pilot might try to file. Revisit only if a broader
"public-charts-only fixes" policy is adopted, as one decision for all
special categories.

If that ever happens, one implementation constraint discovered now:
`point_lookup()` (airway geometry resolution) must keep being built
from **all** fixes even if the `fixes` table is filtered, or kept
airways that share a fix with a special route would develop geometry
gaps.

## 6. Tests (new — the build tools currently have none)

Create `tests/tools/test_build_navaid_db.py` and
`tests/tools/test_build_airport_db.py` in pyEfis (new `tests/tools/`
package). Plain pytest + `tmp_path`, no Qt, no network; write small
synthetic CSVs inline and run `build()` / `main()` against them.

Required cases:

1. **Post-DPN AWY fixture**: AWY_BASE with a V route, an AT route
   (`REGULATORY == "N"` — must be kept), and an SP route → SP segments
   absent from `awy_segments`, others present, skip count reported.
2. **Pre-DPN AWY fixture** (no SP rows) → output identical to a build
   without the filter (regression guard for §4.3).
3. **Post-DPN FIX fixture**: a fix with `CHARTS: "SPECIAL ENROUTE"` →
   still present in `fixes` (documents the §5 decision) and still
   usable for kept-airway geometry.
4. **Post-DPN APT_RWY fixture**: APT_RWY.csv with the new
   `PAVEMENT CLASSIFICATION` column appended and a 4-char PCN number →
   `build_airport_db.py` output unchanged vs. the same fixture without
   the column (proves §3's "none structurally" claim instead of
   asserting it).
5. **FAA test files, when available**: the DPN says CSV *test*
   subscriber files will be posted on the 28-Day NASR Subscription page
   ("as soon as they are available"; TXT test files are up now). Add a
   checklist item — not CI — to run both builders against the FAA CSV
   test drop end-to-end once posted. Do not block the code change on
   this; the FAA has no posting date.

## 7. Rollout

1. Implement + tests in pyEfis on the `PYEFIS_REF` branch
   (`display-changes` today — confirm the repo variable in
   makerplane-data GitHub settings hasn't been overridden).
2. Local verification: rebuild navaids-conus for 2607 from
   `work/localwork` inputs; diff row counts pre/post change (§4.3).
3. Push. The nightly `cyclical-packs` run picks up the branch
   automatically — packs are rebuilt only for *new* cycles
   (skip-if-present), so already-published packs are untouched.
4. **Before 06 Aug 2026**: confirm merged + a green nightly run.
5. When the FAA posts the 03 Sep cycle: check that nightly run's log
   for the skip counter; spot-check the built pack
   (`SELECT COUNT(*) FROM awy_segments WHERE awy_id LIKE 'ZK%'` should
   be 0 — ZK is the SP route ident family per the DPN).

The §8 schema guard is a **separate PR in this repo** (no pyEfis
coupling) and should merge first if convenient — it is independent of
the SP filter and protects the same deadline.

## 8. Schema guard — make the next format change interrupt the build

The SP filter (§4) fixes *this* DPN. This section makes the pipeline
fail loudly on the next one, announced or not.

### 8.1 Why unit tests alone can't do this — and what can

Unit tests run against frozen fixtures; the FAA doesn't run our CI, so
no fixture can see their next drop. What catches upstream drift is a
**data-contract gate inside the nightly pipeline**, run against the
live download, that fails the run before anything is signed or
uploaded. Unit tests then prove the gate itself fires (§8.5).

Two facts discovered during this analysis make the gate both necessary
and cheap:

1. **Today a broken build is a green run.** `run_cyclical.py:122-128`
   catches *every* `_build_one` exception as `WARN ... continue`, and
   `main()` (line 189-190) always returns 0. Worse, the nastiest
   failure raises nothing at all: if the FAA renamed `LAT_DECIMAL`,
   `DictReader.get()` returns `""` for every row, `_f()` returns
   `None`, the builders silently skip **all** rows, and a signed,
   manifest-listed, near-empty pack ships to aircraft.
2. **The FAA ships a machine-readable schema in every CSV zip.**
   Each subscriber zip contains a `*_CSV_DATA_STRUCTURE.csv`
   (columns: `CSV File, Column Name, Max Length, Data Type, Nullable`)
   describing every file in that zip — verified present in the
   extracted APT and NAV/FIX/AWY inputs under `work/localwork/`. We
   already download the contract every night; we just never read it.

### 8.2 Architecture

New module `packtools/schema_guard.py` plus committed snapshots under
`packtools/schemas/`. Apache-side, zero pyEfis coupling — the contracts
describe the builders' *inputs and outputs*, keyed by `Source.builder`
(`"airports"`, `"navaids"`); sources with no contract (obstacles, cifp)
pass through untouched.

```python
class SchemaGuardError(RuntimeError):
    """Deterministic contract break needing a human; never transient."""

CONTRACTS: dict[str, Contract] = {
    "airports": Contract(
        structure_file="APT_CSV_DATA_STRUCTURE.csv",
        structure_snapshot="APT_structure.csv",      # in packtools/schemas/
        required_columns={
            "APT_BASE.csv": {"SITE_NO", "ARPT_ID", "ARPT_NAME",
                             "LAT_DECIMAL", "LONG_DECIMAL", "ELEV",
                             "MAG_VARN", "MAG_HEMIS", "STATE_CODE", "CITY"},
            "APT_RWY.csv": {"SITE_NO", "RWY_ID", "RWY_LEN", "RWY_WIDTH",
                            "SURFACE_TYPE_CODE", "RWY_LGT_CODE"},
            "APT_RWY_END.csv": {...},   # every column build_airport_db.py reads
        },
        enum_columns={},
        floors={"airports": ..., "runways": ..., "runway_ends": ...},
    ),
    "navaids": Contract(
        # NAV/FIX/AWY zips each ship their own structure file; the
        # Contract takes a list of (structure_file, snapshot) pairs.
        ...
        enum_columns={
            # SP is pre-added ON PURPOSE: it is known and handled by the
            # §4 filter. The guard polices the UNKNOWN, not the
            # known-and-handled.
            "AWY_BASE.csv": {"AWY_DESIGNATION":
                # the nine values observed in 2606/2607 (see 4.1) plus SP,
                # which is pre-added ON PURPOSE: known and handled by the
                # 4 filter. No Y/N -- those are REGULATORY, not designations.
                {"AT", "BF", "G", "J", "PA", "PR", "R", "RN", "V", "SP"}},
        },
        floors={"navaids": ..., "fixes": ..., "awy_segments": ...},
    ),
}
```

The four checks, in run order:

1. **Structure snapshot diff** (input, pre-build). Parse the downloaded
   `*_CSV_DATA_STRUCTURE.csv`, keep only the rows for CSV files we
   consume (so unrelated churn in APT_ARS/FIX_CHRT/AWY_SEG_ALT never
   blocks us), and compare the row *set* against the committed
   snapshot. Any added/removed column, type change, or width change →
   `SchemaGuardError` with a readable ±diff. A missing structure file
   is itself a failure. This catches renames, additions, and widenings
   — it would have flagged the PAVEMENT CLASSIFICATION column and the
   PCN 3→4 widening with no human reading the DPN.
2. **Required-header assertion** (input, pre-build). Read the first
   line of each consumed CSV; every column the builder reads must be
   present (superset is fine — additions are check 1's job). This is
   the direct guard against the silent empty-pack failure in §8.1,
   independent of the FAA's descriptor being correct.
3. **Enum vocabulary** (input, pre-build). One pass over the declared
   enum columns (AWY_BASE is ~3k rows; cost is nil); an unseen value →
   fail. This is the only check that would have caught NASR 26-01's
   real payload — `SP` arrives as a new *value*, not a new column.
4. **Output floors** (post-build, pre-upload). Open the built sqlite:
   any empty table → fail always; each table must meet its floor.
   Derive floors by building once from the `work/localwork` 2607
   inputs and hardcoding **50% of observed counts**, with the observed
   values and derivation date in a comment. Do not invent numbers.

### 8.3 Hook points and the exit-code fix

In `_build_one` (`run_cyclical.py:73-94`):

- after the fetch loop, before `builder(...)`:
  `guard.check_inputs(source, Path(extracted))`
- after `builder(...)`, before `embed_sqlite`:
  `guard.check_output(source, pack_path)`

The guard callable is a constructor parameter of `CyclicalRunner`
(default `schema_guard.check`), injected exactly like `fetcher` and
`builders` so existing orchestrator tests keep passing with a no-op.

In `run()` and `main()` — the part that makes failures visible:

- Keep the current WARN-and-continue for fetch errors (a
  not-yet-published next cycle is normal and must stay benign).
- `SchemaGuardError` and `build.BuildError` are **collected**, not
  swallowed: log them, `continue` to the next source (one bad source
  must not block DOF), and at the end of `run()` raise/return so
  `main()` exits **1**. A red `cyclical-packs` run is the whole point.
- Failure containment is already right by construction: upload and
  manifest upsert happen after the guard, so a failing source ships
  nothing, its previously published packs keep serving, and
  skip-if-present retries nightly until the contract is refreshed.

### 8.4 Snapshots and the refresh procedure

- Seed `packtools/schemas/*_structure.csv` from the current cycle's
  downloaded structure files (the extracted 2607 inputs on disk are
  fine), filtered to consumed files, verbatim FAA rows. Record the
  source cycle in `packtools/schemas/README.md`.
- **Refreshing is the acknowledgment gate**: when a run goes red on a
  structure diff, a human reads the diff (and the DPN that usually
  announced it), updates the snapshot rows in a PR, and decides whether
  builder code must change too. One line of process, but it converts
  "the FAA changed something under us" from a silent event into a
  reviewed one.
- **One planned firing is already scheduled**: when the FAA posts the
  03 Sep cycle (~Aug 2026), the APT structure diff (PAVEMENT
  CLASSIFICATION + PCN width) will correctly interrupt the airports
  build. Refresh the snapshot then — or earlier, from the FAA CSV
  *test* files (§6 item 5) if they post first. Treat it as the guard's
  production acceptance test.

### 8.5 Unit tests (`tests/test_schema_guard.py`, this repo)

Synthetic fixtures via `tmp_path`, following the existing
injected-fetcher/builder test style:

1. Structure diff: identical → pass; added row, removed row, changed
   `Max Length` → fail with the offending row named; missing structure
   file → fail.
2. Required headers: fixture CSV missing `LAT_DECIMAL` → fail; extra
   unknown column → pass (check 1 owns additions).
3. Enum: AWY fixture with designation `ZZ` → fail; all-known → pass.
4. Floors: below-floor table → fail; empty table → fail even with no
   floor declared; healthy → pass.
5. Orchestrator integration: with a guard that fails one source,
   `run()` still processes the other sources, uploads nothing for the
   failed one, and the run reports failure (`main()` → 1); with the
   no-op guard, behavior is unchanged (regression).
6. Self-consistency: every `required_columns` entry appears in the
   committed snapshot for its file (catches a snapshot/contract typo at
   test time, not 3 a.m.).

### 8.6 Acceptance criteria

- [x] Renamed required column, new column, changed width, unknown
      AWY_DESIGNATION value, empty output table, below-floor table:
      each produces a failing run, proven by unit test.
- [x] A guard failure in one source does not prevent other sources
      from building/uploading, but the run still exits nonzero.
- [x] Rebuild of the 2607 cycle from `work/localwork` inputs passes all
      four checks green (no false positives on current data).
- [x] Snapshots committed with provenance; refresh procedure documented
      in `packtools/schemas/README.md`.
- [x] Existing orchestrator tests pass unmodified (guard injected,
      default no-op in their fixtures only where they predate it).

## 9. Watch items (no action now)

- **PCR population is TBD upstream**: the FAA says the new PAVEMENT
  CLASSIFICATION field may stay unpopulated after deployment and *"an
  updated Data Product Notice will be issued once this new field is
  utilized."* We don't read pavement data at all; nothing to do unless
  a future feature wants runway strength.
- **SP category may expand** ("in the future Special Routes category
  could be expanded"). The designation filter already covers the
  category; if the FAA adds new designation values instead, extend
  `EXCLUDE_AWY_DESIGNATIONS`.
- SP routes update on a 56-day cycle; our packs are 28-day AIRAC.
  Irrelevant once filtered, noted here so nobody puzzles over it later.
