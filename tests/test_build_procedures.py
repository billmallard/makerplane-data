# SPDX-License-Identifier: Apache-2.0
"""ARINC 424 CIFP -> sqlite ``procedures`` pack builder (AER-1600 / PA1)."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from packtools.build import BUILDERS
from packtools.build.procedures import (
    KNOWN_SOURCE_GAPS, ProceduresBuildError, build_procedures)
from packtools.packmeta import KINDS, detect_procedures_schema_version

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
                            "transitions", "legs", "msa", "fas_blocks")}
    finally:
        con.close()
    assert counts == {
        "airways": 3, "airway_legs": 20,
        "procedures": 11, "transitions": 37, "legs": 134, "msa": 21, "fas_blocks": 3,
    }


def test_build_procedures_airway_rows(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    row = con.execute("SELECT ident, cycle FROM airways WHERE ident='A315'").fetchone()
    con.close()
    assert row == ("A315", "2609")


def test_build_procedures_airway_legs_carry_min_alt2_ft(input_dir, tmp_path):
    # AER-5460: the pack carries the reverse-direction MEA (real 2611 V1
    # INLET/PLANN rows) so pyEfis can publish 5,000 ft, not 2,100, flying
    # PLANN->INLET. NULL where the record leaves it blank.
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT al.fix_id, al.min_alt_ft, al.min_alt2_ft, al.max_alt_ft FROM airway_legs al "
        "JOIN airways a ON a.id = al.airway_id WHERE a.ident='V1' ORDER BY al.seq").fetchall()
    blank = con.execute(
        "SELECT COUNT(*) FROM airway_legs al JOIN airways a ON a.id = al.airway_id "
        "WHERE a.ident IN ('A315', 'A509') AND al.min_alt2_ft IS NOT NULL").fetchone()[0]
    con.close()
    assert rows == [("KIMMY", 5000, None, 17500), ("INLET", 2100, 5000, 17500),
                    ("PLANN", 2100, 5000, 17500)]
    assert blank == 0


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


def _star_fixes(con, airport, ident, transition):
    """The fixes a STAR flies via *transition*, assembled the way pyEfis's
    ``proc_load.assemble`` does: the en-route transition, then the common
    route, with a leading ``IF`` at the fix the previous part ended on
    merged into it."""
    def legs(role, t_ident=None):
        q = ("SELECT l.path_term, l.fix_id FROM legs l "
             "JOIN transitions t ON l.transition_id = t.id "
             "JOIN procedures p ON t.proc_id = p.id "
             "WHERE p.airport = ? AND p.ident = ? AND t.role = ?")
        args = [airport, ident, role]
        if t_ident is not None:
            q += " AND t.ident = ?"
            args.append(t_ident)
        return con.execute(q + " ORDER BY t.id, l.seq", args).fetchall()
    fixes = []
    for path_term, fix_id in legs("enroute", transition) + legs("common"):
        if path_term == "IF" and fixes and fixes[-1] == fix_id:
            continue
        fixes.append(fix_id)
    return fixes


def test_build_procedures_star_common_route_coded_all_is_common(input_dir, tmp_path):
    """AER-5611: KSMO FERN7 codes FIM-VNY-DARTS as Route Type 2 (STAR
    common route) under transition ident ``ALL``. Classified by ident it
    was filed as an en-route transition named ALL, so a pilot picking OHIGH
    got OHIGH CANYN FIM and the STAR silently stopped at FIM."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    try:
        roles = dict(con.execute(
            "SELECT t.ident, t.role FROM transitions t JOIN procedures p ON t.proc_id = p.id "
            "WHERE p.airport = 'KSMO' AND p.ident = 'FERN7'"))
        fixes = _star_fixes(con, "KSMO", "FERN7", "OHIGH")
    finally:
        con.close()
    assert roles == {"AVE": "enroute", "DERBB": "enroute", "FLW": "enroute",
                     "OHIGH": "enroute", "ALL": "common"}
    assert fixes == ["OHIGH", "CANYN", "FIM", "VNY", "DARTS"]


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


def _er_line(area, ident, seq, fix):
    """A synthetic Enroute Airways (``ER``) record, byte-exact except for
    the fields under test -- built from a real A315 record (see
    tests/fixtures/cifp/README.md) so the continuation flag, altitudes and
    every other column stay valid ARINC 424, and only area/ident/seq/fix
    vary."""
    template = list(
        "SUSAER       A315        0100ZBV  MYD 0V    O                         "
        "13540185     05000     60000                         557332605")
    template[1:4] = area.ljust(3)
    template[13:18] = ident.ljust(5)
    template[25:29] = seq.rjust(4, "0")
    template[29:34] = fix.ljust(5)
    line = "".join(template)
    assert len(line) == 132
    return line


def test_build_airways_does_not_merge_across_areas(input_dir, tmp_path):
    """AER-1979: the ER stream carries every Customer/Area Code (USA, CAN,
    PAC, LAM) in one interleaved sequence, and a route ident is only unique
    *within* an area -- 73 of 1,504 idents in a live cycle recur under a
    second area with an unrelated set of legs, each area numbering its own
    legs 10, 20, 30 ... . Pin a synthetic ident under USA and CAN, mirroring
    that shape, and assert the two areas' legs never land under one
    airway_id -- a naive ident-only key merges them, which is exactly this
    bug."""
    cifp = input_dir / "FAACIFP18"
    # The USA legs' fixes need records too, or the build refuses them as
    # unresolved (AER-5612): reuse the fixture's ZBV VOR under each ident.
    zbv = next(l.rstrip("\n") for l in cifp.open(encoding="latin-1")
               if l.startswith("SUSAD        ZBV   MY"))
    with cifp.open("a", encoding="latin-1") as f:
        for ident in ("AAAAA", "BBBBB"):
            f.write(zbv[:13] + ident + zbv[18:] + "\n")
        f.write(_er_line("USA", "Z999", "0100", "AAAAA") + "\n")
        f.write(_er_line("USA", "Z999", "0200", "BBBBB") + "\n")
        f.write(_er_line("CAN", "Z999", "0100", "CCCCC") + "\n")
        f.write(_er_line("CAN", "Z999", "0200", "DDDDD") + "\n")

    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = con.execute("SELECT id FROM airways WHERE ident = 'Z999'").fetchall()
    assert len(rows) == 1, "one ident under two areas produced more than one airways row"
    airway_id = rows[0][0]
    fixes = {r[0] for r in con.execute(
        "SELECT fix_id FROM airway_legs WHERE airway_id = ?", (airway_id,))}
    con.close()
    # USA-only build policy: the CAN legs are filtered out, not blended in
    # with the USA ones under a shared seq space.
    assert fixes == {"AAAAA", "BBBBB"}


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


# --- MSA (AER-4446) ------------------------------------------------------------

def test_build_procedures_msa_rows_one_per_sector(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT sector_seq, bearing_start, bearing_end, altitude_ft, radius_nm, mag_true, "
        "centre_type, centre_lat, centre_lon, cycle FROM msa "
        "WHERE airport = 'KSBA' AND centre_ident = 'RZS' ORDER BY sector_seq").fetchall()
    n_records = con.execute("SELECT COUNT(DISTINCT record) FROM msa").fetchone()[0]
    con.close()
    assert [r[:6] for r in rows] == [(1, 30, 120, 5900, 25, "M"), (2, 120, 210, 8100, 25, "M"),
                                     (3, 210, 300, 8800, 25, "M"), (4, 300, 30, 5200, 25, "M")]
    assert {r[6:] for r in rows} == {("vor", 34.50953333, -119.77098889, "2609")}
    assert n_records == 10


def test_build_procedures_msa_record_column_keeps_the_fields_as_read(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    (record,) = con.execute("SELECT DISTINCT record FROM msa WHERE airport = 'KABQ' "
                            "AND multiple_code = 'A'").fetchone()
    con.close()
    rec = json.loads(record)
    assert rec["continuations"] == []
    assert rec["fields"]["centre_ident"] == "ABQ  " and rec["fields"]["multiple_code"] == "A"
    assert rec["fields"]["sectors"].startswith("02020009025")


def test_build_procedures_unresolved_msa_centre_is_stored_without_a_position(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    rows = con.execute("SELECT centre_ident FROM msa WHERE centre_lat IS NULL").fetchall()
    con.close()
    assert rows == [("HQT",)]


def test_build_procedures_arc_radius_only_on_rf_legs(input_dir, tmp_path):
    """On a non-RF leg the centre columns name the procedure's MSA centre
    (KSBA I07 names RZS, the MSA on its plate), and a radius to it would be
    meaningless."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    con = sqlite3.connect(str(out))
    msa_ref = con.execute(
        "SELECT l.centre_fix, l.centre_lat, l.arc_radius_nm FROM legs l "
        "JOIN transitions t ON l.transition_id = t.id JOIN procedures p ON t.proc_id = p.id "
        "WHERE p.airport = 'KSBA' AND p.ident = 'I07' AND l.centre_fix IS NOT NULL").fetchall()
    n_non_rf_radius = con.execute(
        "SELECT COUNT(*) FROM legs WHERE path_term <> 'RF' AND arc_radius_nm IS NOT NULL"
    ).fetchone()[0]
    con.close()
    assert msa_ref == [("RZS", 34.50953333, None)]
    assert n_non_rf_radius == 0


def test_build_procedures_refuses_a_file_with_no_msa_records(input_dir, tmp_path):
    src = input_dir / "FAACIFP18"
    lines = src.read_text(encoding="latin-1").splitlines()
    src.write_text("\n".join(l for l in lines if not (len(l) == 132 and l[4] == "P"
                                                       and l[12] == "S")) + "\n",
                   encoding="latin-1")
    with pytest.raises(ProceduresBuildError, match="no MSA records"):
        build_procedures(input_dir, tmp_path / "out.pack")


def test_built_pack_declares_procedures_schema_version_4(input_dir, tmp_path):
    out = build_procedures(input_dir, tmp_path / "out.pack")
    assert detect_procedures_schema_version(out) == 4


def test_build_procedures_refuses_an_undecoded_route_type(input_dir, tmp_path):
    """AER-5611: a Route Type with no decoded role (here an engine-out SID,
    ``0``) fails the build rather than guessing which part it is."""
    src = input_dir / "FAACIFP18"
    lines = src.read_text(encoding="latin-1").splitlines(keepends=True)
    flout = next(i for i, l in enumerate(lines) if l.startswith("SUSAP KSBAK2DFLOUT51RW07"))
    lines[flout] = lines[flout][:19] + "0" + lines[flout][20:]
    src.write_text("".join(lines), encoding="latin-1")
    with pytest.raises(ProceduresBuildError, match="KSBA FLOUT5.*'0'"):
        build_procedures(input_dir, tmp_path / "out.pack")


# --- unresolved fixes (AER-5612) -----------------------------------------------
# The 2611 pack shipped 556 procedures with a leg naming a fix that had no
# position, and pyEfis refuses such a procedure outright. These pin the two
# PA14 found on the bench, then the gate that keeps it from shipping again.

def _leg_positions(out, airport, ident, fix_id):
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT t.ident, l.seq, l.path_term, l.fix_type, l.fix_lat, l.fix_lon "
        "FROM legs l JOIN transitions t ON t.id = l.transition_id "
        "JOIN procedures p ON p.id = t.proc_id "
        "WHERE p.airport = ? AND p.ident = ? AND l.fix_id = ? ORDER BY t.id, l.seq",
        (airport, ident, fix_id)).fetchall()
    con.close()
    return rows


def test_kbur_fern7_rw08_vm_leg_carries_the_airport_position(input_dir, tmp_path):
    """FERN7's only runway transition ends VM 076 "to" KBUR, coded as the
    PA airport record. Position is KBUR's ARP, N34120250 W118213120."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    [(trans, seq, pt, ftype, lat, lon)] = _leg_positions(out, "KBUR", "FERN7", "KBUR")
    assert (trans, seq, pt, ftype) == ("RW08", 30, "VM", "airport")
    assert lat == pytest.approx(34 + 12 / 60 + 2.50 / 3600, abs=1e-6)
    assert lon == pytest.approx(-(118 + 21 / 60 + 31.20 / 3600), abs=1e-6)


def test_kanb_i05z_terminal_ndb_an_carries_its_position(input_dir, tmp_path):
    """I05-Z names the terminal NDB AN (a PN record) on its IAF, its missed
    approach CF and its hold. Position is N33320362 W085555257."""
    out = build_procedures(input_dir, tmp_path / "out.pack")
    rows = _leg_positions(out, "KANB", "I05-Z", "AN")
    assert [(t, s, pt, ft) for t, s, pt, ft, _, _ in rows] == [
        ("AN", 10, "IF", "ndb"), (None, 60, "CF", "ndb"), (None, 70, "HM", "ndb")]
    for *_, lat, lon in rows:
        assert lat == pytest.approx(33 + 32 / 60 + 3.62 / 3600, abs=1e-6)
        assert lon == pytest.approx(-(85 + 55 / 60 + 52.57 / 3600), abs=1e-6)


def _with_fix(line, ident, icao, section, subsection):
    out = list(line)
    out[29:34] = ident.ljust(5)
    out[34:36] = icao
    out[36] = section
    out[37] = subsection
    return "".join(out)


def _fern7_vm_leg(cifp):
    return next(l.rstrip("\n") for l in cifp.open(encoding="latin-1")
                if l.startswith("SUSAP KBURK2EFERN7 3RW08  030"))


def test_build_refuses_a_leg_whose_fix_does_not_resolve_and_names_it(input_dir, tmp_path):
    cifp = input_dir / "FAACIFP18"
    leg = _with_fix(_fern7_vm_leg(cifp), "NOFIX", "K2", "E", "A")
    leg = leg[:26] + "040" + leg[29:]
    with cifp.open("a", encoding="latin-1") as f:
        f.write(leg + "\n")
    with pytest.raises(ProceduresBuildError,
                       match=r"NOFIX/K2/EA: 1 leg\(s\), e.g. KBUR star FERN7 RW08 seq 40 VM"):
        build_procedures(input_dir, tmp_path / "out.pack")


def test_build_keeps_a_declared_source_gap_without_a_position(input_dir, tmp_path):
    """CMY/K5/DB is named by KDAF R36 and defined nowhere in the file; it
    is the one declared gap, so the build stores it NULL and goes on."""
    assert ("CMY", "K5", "DB") in KNOWN_SOURCE_GAPS
    cifp = input_dir / "FAACIFP18"
    leg = _with_fix(_fern7_vm_leg(cifp), "CMY", "K5", "D", "B")
    leg = leg[:26] + "040" + leg[29:]
    with cifp.open("a", encoding="latin-1") as f:
        f.write(leg + "\n")
    out = build_procedures(input_dir, tmp_path / "out.pack")
    assert _leg_positions(out, "KBUR", "FERN7", "CMY") == [
        ("RW08", 40, "VM", "ndb", None, None)]


def test_build_refuses_a_declared_gap_the_file_now_defines(input_dir, tmp_path):
    cifp = input_dir / "FAACIFP18"
    zbv = next(l.rstrip("\n") for l in cifp.open(encoding="latin-1")
               if l.startswith("SUSAD        ZBV   MY"))
    with cifp.open("a", encoding="latin-1") as f:
        f.write("SUSADB       CMY   K5" + zbv[21:] + "\n")
    with pytest.raises(ProceduresBuildError, match="now defines CMY/K5/DB"):
        build_procedures(input_dir, tmp_path / "out.pack")
