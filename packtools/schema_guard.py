# SPDX-License-Identifier: Apache-2.0
"""Pipeline data-contract guard (see docs/nasr_2601_dpn_prep.md, section 8).

The daily orchestrator ingests FAA CSVs by *column name* with ``.get()``
defaults, so an upstream format change never crashes the build -- it silently
ships wrong or near-empty data (a green run that lands bad data on aircraft).
Unit tests can't catch that: the FAA doesn't run our CI, so no frozen fixture
sees their next drop. This guard runs inside the nightly pipeline against the
LIVE download, before anything is signed or uploaded, and fails the run loudly
when a contract breaks -- announced (like NASR 26-01) or not.

Four checks, in run order:

  1. Structure snapshot diff  (input, pre-build)  -- an added / removed /
     retyped / widened column vs the committed snapshot. Catches the
     NASR 26-01 PAVEMENT CLASSIFICATION column and the PCN 3->4 widening
     with no human reading the DPN.
  2. Required headers          (input, pre-build)  -- every column a builder
     actually reads is present. The direct guard against the silent
     empty-pack failure, independent of the FAA's descriptor being correct.
  3. Enum vocabulary           (input, pre-build)  -- an unseen value in a
     policed column. The only check that catches NASR 26-01's real payload:
     ``SP`` arrives as a new *value* in AWY_DESIGNATION, not a new column.
  4. Output floors             (post-build)        -- an empty table always
     fails; each table must clear a floor (50% of an observed build).

Contracts are keyed by ``Source.builder`` ("airports", "navaids"). A source
with no contract (obstacles, cifp) passes through untouched. Snapshots live in
``packtools/schemas/`` and are refreshed by a human when a run goes red -- that
refresh is the acknowledgement gate (docs/nasr_2601_dpn_prep.md 8.4).

Apache-side, zero pyEfis coupling: the contracts describe the builders' CSV
*inputs* and sqlite *outputs*, not any pyEfis internals.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

# The FAA ships one of these in every CSV subscriber zip (columns:
# CSV File, Column Name, Max Length, Data Type, Nullable).
_STRUCTURE_GLOB = "*_CSV_DATA_STRUCTURE.csv"


class SchemaGuardError(RuntimeError):
    """A deterministic upstream-contract break that needs a human -- never
    transient. Retrying will not fix it; a human reads the diff (and usually
    the DPN that announced it), refreshes the snapshot, and decides whether
    builder code must change too (docs/nasr_2601_dpn_prep.md 8.4)."""


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _prefix(csv_name: str) -> str:
    """'APT_RWY.csv' -> 'APT_RWY' (the structure file's 'CSV File' value)."""
    return csv_name[:-4] if csv_name.lower().endswith(".csv") else csv_name


def _structure_rows(path: Path) -> set[tuple[str, str, str, str, str]]:
    """Parse a FAA *_CSV_DATA_STRUCTURE.csv into a set of normalized 5-tuples
    (csv_file, column, max_length, data_type, nullable). Fields are stripped so
    an incidental whitespace quirk (FIX_BASE ships 'COUNTRY_CODE  ') never reads
    as a schema change, while a real rename / retype / rewidth still does."""
    rows: set[tuple[str, str, str, str, str]] = set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.add((_norm(r.get("CSV File")), _norm(r.get("Column Name")),
                      _norm(r.get("Max Length")), _norm(r.get("Data Type")),
                      _norm(r.get("Nullable"))))
    return rows


def _find_csv(root: Path, name: str) -> Path | None:
    hits = sorted(Path(root).rglob(name))
    return hits[0] if hits else None


def _header(path: Path) -> list[str]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            return [_norm(c) for c in row]
    return []


def _diff_message(builder: str, added, removed) -> str:
    lines = [f"{builder}: input CSV structure has drifted from the committed "
             f"snapshot (packtools/schemas/). A human must review this and "
             f"refresh the snapshot (docs/nasr_2601_dpn_prep.md 8.4):"]
    for f, c, ml, dt, nu in removed:
        lines.append(f"  - REMOVED/changed  {f}.{c}  ({ml} {dt} {nu})")
    for f, c, ml, dt, nu in added:
        lines.append(f"  + ADDED/changed    {f}.{c}  ({ml} {dt} {nu})")
    return "\n".join(lines)


@dataclass(frozen=True)
class Contract:
    """The input/output contract for one builder. ``required_columns`` keys are
    the consumed CSV filenames (with '.csv'); everything else is keyed off them.
    """
    builder: str
    required_columns: dict[str, frozenset[str]]
    structure_snapshots: tuple[str, ...]
    enum_columns: dict[str, dict[str, frozenset[str]]] = field(default_factory=dict)
    floors: dict[str, int] = field(default_factory=dict)

    @property
    def consumed_files(self) -> set[str]:
        return set(self.required_columns)

    @property
    def consumed_prefixes(self) -> set[str]:
        return {_prefix(f) for f in self.required_columns}


# --- The contracts -------------------------------------------------------
# required_columns: every column the pyEfis builder actually reads (verified
#   against tools/build_airport_db.py / build_navaid_db.py, 2026-07-05).
# floors: 50% of the 2607 build, derived 2026-07-05 from the inputs under
#   work/localwork/. Airports: 19436 / 23208 / 23731. Navaids: 1629 / 70031 /
#   17307. Do NOT invent or tighten these without re-deriving from a real build.

CONTRACTS: dict[str, Contract] = {
    "airports": Contract(
        builder="airports",
        required_columns={
            "APT_BASE.csv": frozenset({
                "SITE_NO", "ARPT_ID", "ARPT_NAME", "LAT_DECIMAL", "LONG_DECIMAL",
                "ELEV", "MAG_VARN", "MAG_HEMIS", "STATE_CODE", "CITY"}),
            "APT_RWY.csv": frozenset({
                "SITE_NO", "RWY_ID", "RWY_LEN", "RWY_WIDTH",
                "SURFACE_TYPE_CODE", "RWY_LGT_CODE"}),
            "APT_RWY_END.csv": frozenset({
                "SITE_NO", "RWY_ID", "RWY_END_ID", "TRUE_ALIGNMENT",
                "LAT_DECIMAL", "LONG_DECIMAL", "RWY_END_ELEV",
                "LAT_DISPLACED_THR_DECIMAL", "LONG_DISPLACED_THR_DECIMAL",
                "DISPLACED_THR_LEN", "TDZ_ELEV", "RWY_MARKING_TYPE_CODE",
                "APCH_LGT_SYSTEM_CODE", "RWY_END_LGTS_FLAG",
                "CNTRLN_LGTS_AVBL_FLAG", "TDZ_LGT_AVBL_FLAG"}),
        },
        structure_snapshots=("APT_structure.csv",),
        floors={"airports": 9700, "runways": 11600, "runway_ends": 11800},
    ),
    "navaids": Contract(
        builder="navaids",
        required_columns={
            "NAV_BASE.csv": frozenset({
                "NAV_ID", "NAV_TYPE", "NAME", "FREQ", "ELEV",
                "LAT_DECIMAL", "LONG_DECIMAL"}),
            "FIX_BASE.csv": frozenset({
                "FIX_ID", "FIX_USE_CODE", "LAT_DECIMAL", "LONG_DECIMAL"}),
            # AWY_DESIGNATION is read by the SP filter (docs section 4); it is
            # present today and policed by the enum check below.
            "AWY_BASE.csv": frozenset({
                "AWY_ID", "AIRWAY_STRING", "AWY_DESIGNATION"}),
        },
        structure_snapshots=("NAV_structure.csv", "FIX_structure.csv",
                             "AWY_structure.csv"),
        enum_columns={
            # The nine designations observed in 2606 and 2607 plus SP, which is
            # pre-added ON PURPOSE: it is known and handled by the pyEfis SP
            # filter. The guard polices the UNKNOWN, not the known-and-handled.
            # No Y/N here -- those are REGULATORY values, not designations.
            "AWY_BASE.csv": {"AWY_DESIGNATION": frozenset({
                "AT", "BF", "G", "J", "PA", "PR", "R", "RN", "V", "SP"})},
        },
        floors={"navaids": 800, "fixes": 35000, "awy_segments": 8600},
    ),
}


class SchemaGuard:
    """The default guard, driven by :data:`CONTRACTS`. Constructed with a custom
    ``contracts`` map + ``schemas_dir`` in tests. ``check_inputs`` runs the three
    pre-build checks; ``check_output`` runs the post-build floor check. Both are
    no-ops for a builder with no contract."""

    def __init__(self, contracts: dict[str, Contract] | None = None,
                 schemas_dir: Path | str = SCHEMAS_DIR):
        self.contracts = CONTRACTS if contracts is None else contracts
        self.schemas_dir = Path(schemas_dir)

    def _contract(self, source) -> Contract | None:
        return self.contracts.get(source.builder)

    # -- check 1 -----------------------------------------------------------
    def _check_structure(self, contract: Contract, input_dir: Path) -> None:
        keep = contract.consumed_prefixes
        expected: set[tuple[str, str, str, str, str]] = set()
        for snap in contract.structure_snapshots:
            p = self.schemas_dir / snap
            if not p.exists():
                raise SchemaGuardError(
                    f"{contract.builder}: committed schema snapshot missing: {p}")
            expected |= {row for row in _structure_rows(p) if row[0] in keep}

        struct_files = sorted(input_dir.rglob(_STRUCTURE_GLOB))
        if not struct_files:
            raise SchemaGuardError(
                f"{contract.builder}: no {_STRUCTURE_GLOB} found under {input_dir} "
                f"-- the FAA ships one in every CSV zip; its absence is itself a "
                f"contract break (or a broken download).")
        downloaded: set[tuple[str, str, str, str, str]] = set()
        for sf in struct_files:
            downloaded |= {row for row in _structure_rows(sf) if row[0] in keep}

        if downloaded != expected:
            raise SchemaGuardError(_diff_message(
                contract.builder,
                added=sorted(downloaded - expected),
                removed=sorted(expected - downloaded)))

    # -- check 2 -----------------------------------------------------------
    def _check_headers(self, contract: Contract, input_dir: Path) -> None:
        for fname, required in contract.required_columns.items():
            path = _find_csv(input_dir, fname)
            if path is None:
                raise SchemaGuardError(
                    f"{contract.builder}: required input {fname} not found under "
                    f"{input_dir}")
            present = set(_header(path))
            missing = set(required) - present
            if missing:
                raise SchemaGuardError(
                    f"{contract.builder}: {fname} is missing column(s) the builder "
                    f"reads: {', '.join(sorted(missing))} "
                    f"(a rename here silently empties the pack).")

    # -- check 3 -----------------------------------------------------------
    def _check_enums(self, contract: Contract, input_dir: Path) -> None:
        for fname, cols in contract.enum_columns.items():
            path = _find_csv(input_dir, fname)
            if path is None:
                raise SchemaGuardError(
                    f"{contract.builder}: {fname} not found for the enum check "
                    f"under {input_dir}")
            seen: dict[str, set[str]] = {col: set() for col in cols}
            with open(path, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    for col in cols:
                        v = _norm(r.get(col))
                        if v:
                            seen[col].add(v)
            for col, allowed in cols.items():
                unknown = seen[col] - set(allowed)
                if unknown:
                    raise SchemaGuardError(
                        f"{contract.builder}: {fname}.{col} has unknown value(s) "
                        f"{sorted(unknown)}, not in the known set {sorted(allowed)}. "
                        f"If this is a real new category, handle it (as the SP "
                        f"filter handles SP) then add it here.")

    def check_inputs(self, source, input_dir) -> None:
        contract = self._contract(source)
        if contract is None:
            return
        input_dir = Path(input_dir)
        self._check_structure(contract, input_dir)
        self._check_headers(contract, input_dir)
        self._check_enums(contract, input_dir)

    # -- check 4 -----------------------------------------------------------
    def check_output(self, source, pack_path) -> None:
        contract = self._contract(source)
        if contract is None:
            return
        name = Path(pack_path).name
        con = sqlite3.connect(str(pack_path))
        try:
            tables = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
            counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                      for t in tables}
            # (a) ANY empty table is a red flag -- a silent parse failure ships
            #     near-empty data. Fails even for a table with no declared floor.
            for t in tables:
                if counts[t] == 0:
                    raise SchemaGuardError(
                        f"{contract.builder}: table '{t}' is EMPTY in {name} -- a "
                        f"silent parse failure ships near-empty data; refusing.")
            # (b) each floored table must exist and clear its floor.
            for table, floor in contract.floors.items():
                if table not in counts:
                    raise SchemaGuardError(
                        f"{contract.builder}: built pack {name} has no '{table}' "
                        f"table (output schema changed or the build failed).")
                if counts[table] < floor:
                    raise SchemaGuardError(
                        f"{contract.builder}: table '{table}' has {counts[table]} "
                        f"rows in {name}, below the floor of {floor} -- suspect a "
                        f"truncated upstream file or a parse break.")
        finally:
            con.close()


class _NullGuard:
    """A do-nothing guard for callers/tests that don't exercise real FAA data
    (e.g. the orchestrator tests, which inject fake builders + fake inputs)."""

    def check_inputs(self, source, input_dir) -> None:
        pass

    def check_output(self, source, pack_path) -> None:
        pass


#: the guard the pipeline uses by default (real contracts + committed snapshots)
DEFAULT = SchemaGuard()
#: inject this where there is no real FAA data to check
NULL_GUARD = _NullGuard()
