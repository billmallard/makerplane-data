# SPDX-License-Identifier: Apache-2.0
"""Grid MORA (CIFP ``AS``) parsing, building, schema version and publish
gate (AER-4447). Fixture: real 2611 records, tests/fixtures/cifp/grid_mora."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from packtools import arinc424, packmeta
from packtools.build.procedures import ProceduresBuildError, build_procedures
from packtools.packmeta import PackMeta

CIFP = Path(__file__).resolve().parent / "fixtures" / "cifp"
MORA_FIXTURE = CIFP / "grid_mora" / "FAACIFP18"


def _cells(path=MORA_FIXTURE):
    out = {}
    for band in arinc424.iter_grid_mora(path):
        for lat, lon, ft, raw in band.cells():
            out[(lat, lon)] = (ft, raw)
    return out


def _input_with_grid(tmp_path, extra=()):
    """The golden procedure fixture with the AS records appended, so the
    builder has airways and legs to build alongside the grid."""
    d = tmp_path / "input"
    d.mkdir()
    golden = (CIFP / "FAACIFP18").read_text(encoding="latin-1")
    grid = [ln for ln in MORA_FIXTURE.read_text(encoding="latin-1").splitlines()
            if ln[4:6] == "AS"]
    (d / "FAACIFP18").write_text(
        golden + "\n".join(grid + list(extra)) + "\n", encoding="latin-1")
    return d


# --- parser -----------------------------------------------------------------

def test_every_fixture_band_parses_with_30_cells():
    bands = list(arinc424.iter_grid_mora(MORA_FIXTURE))
    assert len(bands) == 13
    assert all(len(b.moras) == len(b.raw_moras) == 30 for b in bands)
    assert all(b.raw[4:6] == "AS" and len(b.raw) == 132 for b in bands)


def test_band_corner_is_signed():
    corners = {(b.lat_sw, b.lon_sw) for b in arinc424.iter_grid_mora(MORA_FIXTURE)}
    assert {(4, 150), (-14, -180), (36, -120), (63, -180)} <= corners


def test_fields_are_stored_as_read():
    band = next(b for b in arinc424.iter_grid_mora(MORA_FIXTURE)
                if (b.lat_sw, b.lon_sw) == (36, -120))
    assert band.raw_moras[:3] == ["121", "168", "135"]
    assert band.moras[:3] == [12100, 16800, 13500]
    assert band.cycle == band.raw[128:132]


def test_unk_is_none_and_never_filled():
    band = next(b for b in arinc424.iter_grid_mora(MORA_FIXTURE)
                if (b.lat_sw, b.lon_sw) == (4, 150))
    assert band.raw_moras == ["UNK"] * 30
    assert band.moras == [None] * 30


@pytest.mark.parametrize("name, cell, mora_ft", [
    ("Mt Whitney 14,505 ft, 36.58N 118.29W", (36, -119), 16800),
    ("Mt Rainier 14,411 ft, 46.85N 121.76W", (46, -122), 16800),
    ("Denali 20,310 ft, 63.07N 151.01W", (63, -152), 22700),
])
def test_summit_lands_in_the_highest_cell_of_its_neighbourhood(name, cell, mora_ft):
    # Pins the SW-corner / eastward-step convention: read any other way, the
    # summit's value would land in a neighbouring cell.
    cells = _cells()
    lat, lon = cell
    assert cells[cell][0] == mora_ft
    around = [cells[(lat + dl, lon + dn)][0] for dl in (-1, 0, 1) for dn in (-1, 0, 1)]
    assert max(around) == mora_ft


def test_over_water_and_aerostat_cells():
    cells = _cells()
    assert cells[(27, -93)] == (1400, "014")     # open Gulf of Mexico
    assert cells[(24, -82)] == (15200, "152")    # Florida Keys: Cudjoe Key aerostat


def test_undecodable_corner_is_skipped_not_placed():
    good = next(ln for ln in MORA_FIXTURE.read_text(encoding="latin-1").splitlines()
                if ln[4:6] == "AS")
    bad = good[:13] + "X04" + good[16:]
    assert arinc424._decode_band_corner(good[13:16], good[16:20]) == (4, 150)
    assert arinc424._decode_band_corner(bad[13:16], bad[16:20]) is None


# --- builder ----------------------------------------------------------------

def test_builder_writes_grid_mora(tmp_path):
    out = build_procedures(_input_with_grid(tmp_path), tmp_path / "p.pack")
    con = sqlite3.connect(out)
    try:
        assert con.execute("SELECT COUNT(*) FROM grid_mora_bands").fetchone()[0] == 13
        assert con.execute("SELECT COUNT(*) FROM grid_mora").fetchone()[0] == 13 * 30
        assert con.execute("SELECT mora_ft, raw FROM grid_mora WHERE lat_sw=46 "
                           "AND lon_sw=-122").fetchone() == (16800, "168")
        assert con.execute("SELECT COUNT(*) FROM grid_mora WHERE mora_ft IS NULL "
                           "AND raw <> 'UNK'").fetchone()[0] == 0
        raw = con.execute("SELECT raw FROM grid_mora_bands WHERE lat_sw=-14").fetchone()[0]
        assert raw.startswith("S   AS       S14W180") and len(raw) == 132
    finally:
        con.close()
    assert packmeta.detect_procedures_schema_version(out) == 3


def test_builder_refuses_two_bands_claiming_one_cell(tmp_path):
    grid = [ln for ln in MORA_FIXTURE.read_text(encoding="latin-1").splitlines()
            if ln[4:6] == "AS"]
    with pytest.raises(ProceduresBuildError, match="same cell"):
        build_procedures(_input_with_grid(tmp_path, extra=[grid[0]]), tmp_path / "p.pack")


# --- schema version -----------------------------------------------------------

def test_procedures_schema_version_follows_the_table_set(tmp_path):
    from packtools.build.procedures import _SCHEMA
    v3 = tmp_path / "v3.pack"
    con = sqlite3.connect(v3)
    con.executescript(_SCHEMA)
    con.close()
    assert packmeta.detect_content_schema_version("procedures", v3) == 3
    v2 = tmp_path / "v2.pack"
    con = sqlite3.connect(v2)
    con.executescript(_SCHEMA)
    con.executescript("DROP TABLE grid_mora; DROP TABLE grid_mora_bands;")
    con.close()
    assert packmeta.detect_content_schema_version("procedures", v2) == 2
    v1 = tmp_path / "v1.pack"
    con = sqlite3.connect(v1)
    con.executescript(_SCHEMA)
    con.executescript("DROP TABLE grid_mora; DROP TABLE grid_mora_bands; DROP TABLE msa;")
    con.close()
    packmeta.embed_sqlite(v1, PackMeta(id="procedures-conus", kind="procedures",
                                       cycle="2611"))
    assert packmeta.detect_content_schema_version("procedures", v1) == 1   # pack_meta ignored
    con = sqlite3.connect(v1)
    con.execute("CREATE TABLE surprise (x)")
    con.close()
    with pytest.raises(ValueError, match="PROCEDURES_TABLE_SCHEMAS"):
        packmeta.detect_content_schema_version("procedures", v1)


def test_kinds_without_a_detector_keep_the_package_default(tmp_path):
    assert packmeta.detect_content_schema_version("obstacles", tmp_path / "x") is None
