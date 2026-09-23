# SPDX-License-Identifier: Apache-2.0
"""ARINC 424 CIFP -> sqlite ``procedures`` pack builder (AER-1600 / PA1)."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from packtools.build import BUILDERS
from packtools.build.procedures import ProceduresBuildError, build_procedures
from packtools.packmeta import KINDS

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cifp" / "FAACIFP18"


@pytest.fixture()
def input_dir(tmp_path):
    d = tmp_path / "input"
    d.mkdir()
    shutil.copy(FIXTURE, d / "FAACIFP18")
    return d


def test_registered_in_builders():
    assert BUILDERS["procedures"] is build_procedures


def test_procedures_is_a_known_pack_kind():
    assert "procedures" in KINDS


def test_build_procedures_writes_all_tables(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out" / "procedures-conus.pack")
    con = sqlite3.connect(str(out))
    try:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("airways", "airway_legs", "procedures",
                            "transitions", "legs")}
    finally:
        con.close()
    assert counts == {
        "airways": 2, "airway_legs": 17,
        "procedures": 5, "transitions": 22, "legs": 76,
    }


def test_build_procedures_airway_rows(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    row = con.execute("SELECT ident, cycle FROM airways WHERE ident='A315'").fetchone()
    con.close()
    assert row == ("A315", "2609")


def test_build_procedures_derives_runway_and_approach_type(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT ident, runway, approach_type, rnp FROM procedures WHERE airport='KSBA'")}
    con.close()
    assert rows["I07"] == ("07", "I", None)
    assert rows["FLOUT5"] == (None, None, None)  # a SID, not an approach


def test_build_procedures_rnp_approach_captures_rnp(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT runway, approach_type, rnp FROM procedures "
        "WHERE airport='KABQ' AND ident='H21-Y'").fetchone()
    con.close()
    assert row == ("21", "H", pytest.approx(0.1))


def test_build_procedures_circling_approach_has_no_runway(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT runway, approach_type FROM procedures "
        "WHERE airport='09J' AND ident='VOR-A'").fetchone()
    con.close()
    assert row == (None, "VOR")


def test_build_procedures_transition_roles(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    roles = {r[0] for r in con.execute(
        "SELECT DISTINCT role FROM transitions t JOIN procedures p ON t.proc_id = p.id "
        "WHERE p.ident = 'FLOUT5'")}
    con.close()
    assert roles == {"runway", "enroute"}


def test_build_procedures_rf_arc_has_resolved_centre_and_radius(input_dir, tmp_path):
    """AER-1700: RF (radius-to-fix) legs carry a resolved centre fix and a
    derived radius -- a specific number, not merely non-NULL, so a fixture
    regression (e.g. the centre-fix waypoint records going missing again)
    can't hide behind a weaker assertion."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT l.fix_id, l.centre_fix, l.centre_lat, l.centre_lon, l.arc_radius_nm "
        "FROM legs l JOIN transitions t ON l.transition_id = t.id "
        "JOIN procedures p ON t.proc_id = p.id "
        "WHERE p.airport = 'KABQ' AND p.ident = 'H21-Y' AND t.ident = 'FOXRR' "
        "AND l.seq = 30").fetchone()
    con.close()
    fix_id, centre_fix, centre_lat, centre_lon, arc_radius_nm = row
    assert (fix_id, centre_fix) == ("KEIFR", "CFDXH")
    assert centre_lat == pytest.approx(35.08599722, abs=1e-6)
    assert centre_lon == pytest.approx(-106.62291667, abs=1e-6)
    assert arc_radius_nm == pytest.approx(2.8028, abs=1e-3)


def test_build_procedures_af_legs_have_no_centre_fix(input_dir, tmp_path):
    """AF (DME arc) legs are already complete via recd_navaid/theta/rho --
    the new centre-fix columns must stay NULL for them, not silently pick
    up an unrelated field."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT recd_navaid, theta, rho, centre_fix, arc_radius_nm FROM legs "
        "WHERE path_term = 'AF'").fetchall()
    con.close()
    assert rows  # the fixture has at least one AF leg (09J VOR-A)
    for recd_navaid, theta, rho, centre_fix, arc_radius_nm in rows:
        assert recd_navaid is not None and theta is not None and rho is not None
        assert centre_fix is None and arc_radius_nm is None


def test_build_procedures_no_leg_left_unresolved(input_dir, tmp_path):
    """Every leg in the golden fixture has a matching fix record on purpose
    (see tests/fixtures/cifp/README.md) -- an unresolved fix here means the
    fixture or the resolver has drifted, not a real-world data gap."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    n_bad_legs = con.execute(
        "SELECT COUNT(*) FROM legs WHERE fix_id IS NOT NULL AND fix_lat IS NULL"
    ).fetchone()[0]
    n_bad_awy = con.execute(
        "SELECT COUNT(*) FROM airway_legs WHERE fix_lat IS NULL"
    ).fetchone()[0]
    n_bad_rf = con.execute(
        "SELECT COUNT(*) FROM legs WHERE path_term = 'RF' "
        "AND (centre_fix IS NULL OR centre_lat IS NULL OR centre_lon IS NULL)"
    ).fetchone()[0]
    con.close()
    assert (n_bad_legs, n_bad_awy, n_bad_rf) == (0, 0, 0)


def test_build_procedures_raises_when_no_cifp_file(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ProceduresBuildError, match="no FAACIFP18 file found"):
        build_procedures(empty, tmp_path / "out.pack")


def test_build_procedures_raises_on_empty_input(tmp_path):
    empty_input = tmp_path / "input"
    empty_input.mkdir()
    (empty_input / "FAACIFP18").write_text(
        "HDR01FAACIFP18      001P013203968112609  12-AUG-202618:29:21"
        "  U.S.A. DOT FAA                                                FBB95AD8\n"
        "HDR04                                 CODED INSTRUMENT FLIGHT PROCEDURES "
        "VOLUME 2609  EFFECTIVE 03 SEP 2026                         \n"
    )
    with pytest.raises(ProceduresBuildError, match="no airway legs"):
        build_procedures(empty_input, tmp_path / "out.pack")
