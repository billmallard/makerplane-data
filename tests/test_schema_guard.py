# SPDX-License-Identifier: Apache-2.0
"""Schema guard — the pipeline data-contract gate (docs/nasr_2601_dpn_prep.md 8).

Synthetic fixtures via tmp_path (no network, no FAA data), plus a
self-consistency check of the REAL committed contracts and an optional smoke
test against the on-disk 2607 inputs when they are present (gitignored).
"""

import csv
import datetime as dt
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from packtools import signing
from packtools.manifest import Manifest
from packtools.run_cyclical import CyclicalRunner, PipelineFailure
from packtools.schema_guard import (CONTRACTS, DEFAULT, NULL_GUARD, SCHEMAS_DIR,
                                     Contract, SchemaGuard, SchemaGuardError,
                                     _structure_rows)
from packtools.sources import SOURCES
from packtools.upload import LocalStore

REPO = Path(__file__).resolve().parent.parent

# --- a small synthetic contract, independent of the real FAA schemas -------
DEMO = Contract(
    builder="demo",
    required_columns={"DEMO_BASE.csv": frozenset({"ID", "LAT"})},
    structure_snapshots=("DEMO_structure.csv",),
    enum_columns={"DEMO_BASE.csv": {"CODE": frozenset({"A", "B"})}},
    floors={"widgets": 3},
)
DEMO_ROWS = [
    ("DEMO_BASE", "ID", "4", "VARCHAR", "No"),
    ("DEMO_BASE", "CODE", "2", "VARCHAR", "No"),
    ("DEMO_BASE", "LAT", "(10,8)", "NUMBER", "No"),
]
DEMO_SRC = SimpleNamespace(builder="demo", pack_id="demo-conus")


def _write_structure(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["CSV File", "Column Name", "Max Length", "Data Type", "Nullable"])
        w.writerows(rows)


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _guard(tmp_path):
    """A SchemaGuard driving the DEMO contract off a temp snapshot dir."""
    sdir = tmp_path / "schemas"
    sdir.mkdir()
    _write_structure(sdir / "DEMO_structure.csv", DEMO_ROWS)
    return SchemaGuard(contracts={"demo": DEMO}, schemas_dir=sdir)


def _input(tmp_path, *, structure_rows=DEMO_ROWS, header=("ID", "CODE", "LAT"),
           data_rows=(("1", "A", "34.0"), ("2", "B", "35.0")),
           include_structure=True):
    idir = tmp_path / "in"
    idir.mkdir()
    if include_structure:
        _write_structure(idir / "DEMO_CSV_DATA_STRUCTURE.csv", structure_rows)
    _write_csv(idir / "DEMO_BASE.csv", list(header), [list(r) for r in data_rows])
    return idir


def _sqlite(path, tables):
    con = sqlite3.connect(str(path))
    for t, n in tables.items():
        con.execute(f'CREATE TABLE "{t}" (x INTEGER)')
        con.executemany(f'INSERT INTO "{t}" VALUES (?)', [(i,) for i in range(n)])
    con.commit()
    con.close()
    return path


# --- check 1: structure snapshot diff --------------------------------------

def test_structure_identical_passes(tmp_path):
    _guard(tmp_path).check_inputs(DEMO_SRC, _input(tmp_path))


def test_structure_added_column_fails(tmp_path):
    idir = _input(tmp_path, structure_rows=DEMO_ROWS + [
        ("DEMO_BASE", "PAVEMENT_CLASS", "1", "VARCHAR", "Yes")])
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_inputs(DEMO_SRC, idir)
    assert "PAVEMENT_CLASS" in str(e.value)


def test_structure_removed_column_fails(tmp_path):
    idir = _input(tmp_path, structure_rows=[r for r in DEMO_ROWS if r[1] != "LAT"],
                  header=("ID", "CODE"), data_rows=(("1", "A"), ("2", "B")))
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_inputs(DEMO_SRC, idir)
    assert "LAT" in str(e.value)


def test_structure_width_change_fails(tmp_path):
    # PCN 3->4 shape: same column, different Max Length.
    widened = [r if r[1] != "LAT" else ("DEMO_BASE", "LAT", "(11,8)", "NUMBER", "No")
               for r in DEMO_ROWS]
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_inputs(DEMO_SRC, _input(tmp_path, structure_rows=widened))
    assert "LAT" in str(e.value)


def test_structure_missing_file_fails(tmp_path):
    idir = _input(tmp_path, include_structure=False)
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_inputs(DEMO_SRC, idir)
    assert "DATA_STRUCTURE" in str(e.value)


def test_structure_ignores_unconsumed_files(tmp_path):
    # Churn in a file we don't consume must never block us.
    rows = DEMO_ROWS + [("DEMO_OTHER", "WHATEVER", "9", "VARCHAR", "Yes")]
    _guard(tmp_path).check_inputs(DEMO_SRC, _input(tmp_path, structure_rows=rows))


def test_missing_snapshot_fails(tmp_path):
    guard = SchemaGuard(contracts={"demo": DEMO}, schemas_dir=tmp_path / "empty")
    with pytest.raises(SchemaGuardError) as e:
        guard.check_inputs(DEMO_SRC, _input(tmp_path))
    assert "snapshot" in str(e.value).lower()


# --- check 2: required headers ---------------------------------------------

def test_required_header_missing_fails(tmp_path):
    # LAT is in the structure (check 1 passes) but absent from the CSV header.
    idir = _input(tmp_path, header=("ID", "CODE"),
                  data_rows=(("1", "A"), ("2", "B")))
    # keep structure and header consistent for check 1, break only the header:
    _write_structure(idir / "DEMO_CSV_DATA_STRUCTURE.csv", DEMO_ROWS)
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path)._check_headers(DEMO, idir)
    assert "LAT" in str(e.value)


def test_extra_header_column_passes(tmp_path):
    # A superset header is fine; additions are check 1's job, not check 2's.
    idir = _input(tmp_path, header=("ID", "CODE", "LAT", "SURPRISE"),
                  data_rows=(("1", "A", "34.0", "x"),))
    _guard(tmp_path)._check_headers(DEMO, idir)


# --- check 3: enum vocabulary ----------------------------------------------

def test_enum_known_values_pass(tmp_path):
    _guard(tmp_path)._check_enums(DEMO, _input(tmp_path))


def test_enum_unknown_value_fails(tmp_path):
    # 'ZZ' stands in for an unannounced new designation (the SP scenario).
    idir = _input(tmp_path, data_rows=(("1", "A", "34.0"), ("2", "ZZ", "35.0")))
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path)._check_enums(DEMO, idir)
    assert "ZZ" in str(e.value)


# --- check 4: output floors ------------------------------------------------

def test_floor_met_passes(tmp_path):
    DEMO_SRC_OK = SimpleNamespace(builder="demo")
    _guard(tmp_path).check_output(DEMO_SRC_OK, _sqlite(tmp_path / "p.sqlite", {"widgets": 5}))


def test_below_floor_fails(tmp_path):
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_output(DEMO_SRC, _sqlite(tmp_path / "p.sqlite", {"widgets": 2}))
    assert "floor" in str(e.value)


def test_empty_floored_table_fails(tmp_path):
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_output(DEMO_SRC, _sqlite(tmp_path / "p.sqlite", {"widgets": 0}))
    assert "EMPTY" in str(e.value)


def test_empty_unfloored_table_fails(tmp_path):
    # An empty table with no declared floor still fails (silent-parse guard).
    p = _sqlite(tmp_path / "p.sqlite", {"widgets": 5, "extra": 0})
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_output(DEMO_SRC, p)
    assert "extra" in str(e.value)


def test_missing_floored_table_fails(tmp_path):
    p = _sqlite(tmp_path / "p.sqlite", {"other": 5})
    with pytest.raises(SchemaGuardError) as e:
        _guard(tmp_path).check_output(DEMO_SRC, p)
    assert "widgets" in str(e.value)


def test_no_contract_is_noop(tmp_path):
    src = SimpleNamespace(builder="obstacles")
    guard = _guard(tmp_path)
    guard.check_inputs(src, tmp_path)                              # no raise
    guard.check_output(src, _sqlite(tmp_path / "p.sqlite", {"t": 0}))


# --- orchestrator integration ----------------------------------------------

TODAY = dt.date(2026, 6, 14)


def _fake_fetch(url, work_dir, member=None):
    d = Path(work_dir) / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    (d / "input.csv").write_text("dummy")
    return d


def _fake_build(input_dir, out_path):
    con = sqlite3.connect(str(out_path))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    return Path(out_path)


class _FailOne:
    """A guard that fails exactly one builder's inputs; everything else passes."""

    def __init__(self, builder):
        self.builder = builder

    def check_inputs(self, source, input_dir):
        if source.builder == self.builder:
            raise SchemaGuardError(f"synthetic contract break for {source.builder}")

    def check_output(self, source, pack_path):
        pass


def _runner(tmp_path, guard):
    sk, _pub = signing.generate_keypair()
    store = LocalStore(tmp_path / "r2")
    runner = CyclicalRunner(
        store=store, secret=sk, work_dir=tmp_path / "work",
        fetcher=_fake_fetch,
        builders={"airports": _fake_build, "obstacles": _fake_build},
        guard=guard, today=TODAY)
    return runner, store


def test_one_failed_source_still_publishes_others_and_run_fails(tmp_path):
    runner, store = _runner(tmp_path, _FailOne("airports"))
    with pytest.raises(PipelineFailure):
        runner.run([SOURCES["airports-conus"], SOURCES["obstacles-conus"]])

    # obstacles still built + uploaded; airports shipped nothing.
    assert store.exists("packs/obstacles-conus-260611.pack")
    assert not store.exists("packs/airports-conus-2606.pack")
    # the manifest for the healthy source was still signed + uploaded.
    raw = store.get_bytes("manifest.json")
    assert raw is not None
    ids = {p.id for p in Manifest.from_bytes(raw).packs}
    assert "obstacles-conus" in ids and "airports-conus" not in ids


def test_null_guard_run_is_unchanged(tmp_path):
    runner, store = _runner(tmp_path, NULL_GUARD)
    m = runner.run([SOURCES["airports-conus"], SOURCES["obstacles-conus"]])   # no raise
    ids = {p.id for p in m.packs}
    assert {"airports-conus", "obstacles-conus"} <= ids


# --- the real committed contracts ------------------------------------------

def test_real_contracts_self_consistent():
    """Every required/enum column in the real CONTRACTS exists in the committed
    snapshot for its file -- catches a snapshot/contract typo at test time."""
    for contract in CONTRACTS.values():
        cols_by_file: dict[str, set[str]] = {}
        for snap in contract.structure_snapshots:
            for csv_file, col, *_ in _structure_rows(SCHEMAS_DIR / snap):
                cols_by_file.setdefault(csv_file, set()).add(col)
        for fname, required in contract.required_columns.items():
            have = cols_by_file.get(fname[:-4], set())
            assert required <= have, (
                f"{contract.builder}: {fname} required columns not in snapshot: "
                f"{sorted(set(required) - have)}")
        for fname, cols in contract.enum_columns.items():
            have = cols_by_file.get(fname[:-4], set())
            for col in cols:
                assert col in have, f"{contract.builder}: enum col {fname}.{col} not in snapshot"


@pytest.mark.parametrize("builder,rel", [
    ("navaids", "work/localwork/navaids-conus/2607/extracted"),
    ("airports", "work/localwork/airports-conus/2607/extracted"),
])
def test_real_2607_inputs_pass_the_guard(builder, rel):
    """The DEFAULT guard must NOT false-positive on the current real inputs
    (8.6 acceptance). Skipped where the gitignored FAA inputs aren't on disk."""
    idir = REPO / rel
    if not idir.exists():
        pytest.skip(f"local FAA inputs not present: {idir}")
    DEFAULT.check_inputs(SimpleNamespace(builder=builder, pack_id=f"{builder}-conus"), idir)
