# SPDX-License-Identifier: Apache-2.0
"""FAS data blocks (CIFP ``PP`` records) in the procedures pack (AER-5623).

The golden blocks are real 2611 records in the shared fixture
(tests/fixtures/cifp/FAACIFP18): KSSF R28 (LPV), PAMR R08-Z (LP only) and
PAMR R08-Y (an LTP longitude off the FAS block's 0.0005" grid). Each one's
CRC is the FAA's, so a block that re-computes to it decoded every field in
the CRC wrap the way the FAA's own source did.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from packtools import arinc424 as a
from packtools import packmeta, report
from packtools.build.procedures import ProceduresBuildError, build_procedures
from packtools.packmeta import PackMeta

FIXTURE = Path(__file__).parent / "fixtures" / "cifp" / "FAACIFP18"

KSSF_R28 = ("SUSAP KSSFK4PR28   RW28 001 0000W28A0N2920080300W09827562295+014670300N2920274035"
            "W09829357460106751224000400F400500A433594C235732508")


def _blocks(path=FIXTURE):
    return {(b.airport, b.approach_ident): b for b in a.iter_path_points(path)}


def _input(tmp_path, edit=None):
    """The golden fixture in an input dir, each line passed through ``edit``."""
    d = tmp_path / "input"
    d.mkdir()
    lines = FIXTURE.read_text(encoding="latin-1").splitlines()
    if edit:
        lines = [edit(ln) for ln in lines]
    (d / "FAACIFP18").write_text("\n".join(lines) + "\n", encoding="latin-1")
    return d


# --- DoD 1: golden blocks, every field decoded and the CRC verified ----------

def test_kssf_rwy28_lpv_golden_block():
    b = _blocks()[("KSSF", "R28")]
    assert b.crc == "A433594C" and b.crc_computed == "A433594C" and b.crc_ok
    assert (b.area, b.airport, b.airport_icao, b.approach_ident, b.runway) == (
        "USA", "KSSF", "K4", "R28", "RW28")
    assert (b.operation_type, b.route_indicator, b.sbas_provider,
            b.ref_path_data_selector, b.ref_path_ident, b.apd) == (0, None, 0, 0, "W28A", 0)
    # N29 20 08.0300 / W098 27 56.2295 and N29 20 27.4035 / W098 29 35.7460
    assert b.ltp_lat == pytest.approx(29 + 20 / 60 + 8.0300 / 3600, abs=1e-10)
    assert b.ltp_lon == pytest.approx(-(98 + 27 / 60 + 56.2295 / 3600), abs=1e-10)
    assert b.fpap_lat == pytest.approx(29 + 20 / 60 + 27.4035 / 3600, abs=1e-10)
    assert b.fpap_lon == pytest.approx(-(98 + 29 / 60 + 35.7460 / 3600), abs=1e-10)
    assert (b.ltp_ellipsoid_height_m, b.glidepath_angle_deg, b.course_width_m,
            b.length_offset_m, b.tch, b.tch_units, b.hal_m, b.val_m) == (
        146.7, 3.0, 106.75, 1224, 40.0, "F", 40.0, 50.0)
    # Continuation: the FAA leaves the FPAP ellipsoid height blank.
    assert (b.fpap_ellipsoid_height_m, b.fpap_orthometric_height_m,
            b.ltp_orthometric_height_m, b.approach_type_ident, b.gnss_channel,
            b.helicopter_course) == (None, 172.2, 172.2, "LPV", 42747, None)
    # The 2611 plate (dtpp 00372R28.PDF) prints WAAS CH 42747, W28A, TCH 40, GP 3.00.
    assert len(b.continuations) == 1 and b.fields["crc"] == "A433594C"


def test_pamr_r08z_lp_only_golden_block():
    b = _blocks()[("PAMR", "R08-Z")]
    assert b.crc == "DBD6916D" and b.crc_ok
    assert (b.area, b.runway, b.route_indicator, b.ref_path_ident, b.apd) == (
        "CAN", "RW08", "Z", "W08A", 0)     # Alaska is coded under area CAN
    assert b.ltp_lat == pytest.approx(61 + 12 / 60 + 35.1095 / 3600, abs=1e-10)
    assert b.ltp_lon == pytest.approx(-(149 + 51 / 60 + 24.0880 / 3600), abs=1e-10)
    assert b.fpap_lat == pytest.approx(61 + 11 / 60 + 50.6595 / 3600, abs=1e-10)
    assert b.fpap_lon == pytest.approx(-(149 + 48 / 60 + 44.6280 / 3600), abs=1e-10)
    assert (b.ltp_ellipsoid_height_m, b.glidepath_angle_deg, b.course_width_m,
            b.length_offset_m, b.tch, b.tch_units, b.hal_m, b.val_m) == (
        46.7, 3.75, 106.75, 0, 43.0, "F", 40.0, 0.0)   # LP: no vertical alert limit
    assert (b.ltp_orthometric_height_m, b.fpap_orthometric_height_m,
            b.approach_type_ident, b.gnss_channel) == (39.9, 39.9, "LP", 51874)


def test_an_off_grid_ltp_rounds_to_the_nearest_fas_step():
    # PAMR R08-Y's LTP longitude ends ...7784 ten-thousandths of a second,
    # not a multiple of the FAS block's 0.0005". Only rounding it to 7785
    # reproduces the FAA's CRC; flooring to 7780 does not.
    b = _blocks()[("PAMR", "R08-Y")]
    assert b.fields["ltp_lon"] == "W14951117784"
    assert b.crc_ok and b.crc == "39AFF456"
    assert b.route_indicator == "Y" and b.approach_type_ident == "LPV"
    floored = dict(b.fields, ltp_lon="W14951117780")
    assert a.fas_crc(floored) != b.crc


def test_fas_step_rounds_to_the_nearest_either_side_of_zero():
    # Pins the chosen rule. The data proves only the negative ...7784 case,
    # which "nearest", "away from zero" and a signed floor all reproduce.
    assert [a._fas_step(v) for v in (7784, 7782, 7780, -7784, -7782, -7780)] == [
        1557, 1556, 1556, -1557, -1556, -1556]


def test_every_field_in_the_crc_wrap_moves_the_crc():
    # Each field the encoder reads changes the CRC when its value changes, so
    # none of them is silently ignored by the encoding.
    b = _blocks()[("KSSF", "R28")]
    edits = {"operation_type": "01", "sbas_provider": "01", "airport": "KSSG",
             "runway": "RW28L", "apd": "1", "route_indicator": "A",
             "ref_path_data_selector": "01", "ref_path_ident": "W28B",
             "ltp_lat": "N2920080305", "ltp_lon": "W09827562300",
             "ltp_ellipsoid_height": "+01468", "glidepath_angle": "0301",
             "fpap_lat": "N2920274040", "fpap_lon": "W09829357465",
             "course_width": "10700", "length_offset": "1232", "tch": "000410",
             "tch_units": "M", "hal": "410", "val": "510"}
    for k, v in edits.items():
        assert len(v) == len(b.fields[k]), k
        assert a.fas_crc(dict(b.fields, **{k: v})) not in (None, b.crc), k


def test_the_pp_layout_matches_the_record():
    # One real primary, sliced field by field (ARINC 424-17 Sec. 4.1.28.1).
    f = {k: KSSF_R28[v] for k, v in a._PP.items()}
    assert (f["approach_ident"], f["runway"], f["ref_path_ident"], f["ltp_lat"],
            f["ltp_lon"], f["fpap_lat"], f["fpap_lon"], f["crc"], f["cycle"]) == (
        "R28   ", "RW28 ", "W28A", "N2920080300", "W09827562295", "N2920274035",
        "W09829357460", "A433594C", "2508")
    assert a.fas_crc(f) == "A433594C"


# --- the pack -----------------------------------------------------------------

def test_builder_stores_every_verified_block_attached_to_its_approach(tmp_path):
    out = build_procedures(_input(tmp_path), tmp_path / "p.pack")
    con = sqlite3.connect(out)
    try:
        rows = con.execute(
            "SELECT f.airport, f.approach_ident, f.runway, f.approach_type, f.hal_m, f.val_m, "
            "f.crc, p.kind, p.ident, p.runway FROM fas_blocks f "
            "JOIN procedures p ON p.id = f.proc_id ORDER BY 1, 2").fetchall()
        record = json.loads(con.execute(
            "SELECT record FROM fas_blocks WHERE airport = 'KSSF'").fetchone()[0])
    finally:
        con.close()
    assert rows == [
        ("KSSF", "R28", "RW28", "LPV", 40.0, 50.0, "A433594C", "approach", "R28", "28"),
        ("PAMR", "R08-Y", "RW08", "LPV", 40.0, 50.0, "39AFF456", "approach", "R08-Y", "08"),
        ("PAMR", "R08-Z", "RW08", "LP", 40.0, 0.0, "DBD6916D", "approach", "R08-Z", "08"),
    ]
    assert record["fields"]["crc"] == "A433594C" and len(record["continuations"]) == 1
    assert packmeta.detect_procedures_schema_version(out) == 4


# --- DoD 2: a corrupted block fails its CRC, is dropped, and is named ---------

def _corrupt_kssf_hal(line):
    # HAL 400 -> 410 in KSSF R28's primary: still a well-formed record.
    if line.startswith("SUSAP KSSFK4PR28   RW28 001"):
        return line[:109] + "410" + line[112:]
    return line


def test_a_corrupted_block_is_dropped_and_named(tmp_path, capsys):
    out = build_procedures(_input(tmp_path, _corrupt_kssf_hal), tmp_path / "p.pack")
    err = capsys.readouterr().err
    con = sqlite3.connect(out)
    try:
        kept = [r[0] for r in con.execute(
            "SELECT airport || ' ' || approach_ident FROM fas_blocks ORDER BY 1")]
    finally:
        con.close()
    assert kept == ["PAMR R08-Y", "PAMR R08-Z"]
    assert "WARN fas_blocks: dropped KSSF R28 RW28: CRC A433594C, computed " in err


def test_the_pack_report_lists_the_drop(tmp_path):
    inputs = _input(tmp_path, _corrupt_kssf_hal)
    out = build_procedures(inputs, tmp_path / "p.pack")
    packmeta.embed_sqlite(out, PackMeta(id="procedures-conus", kind="procedures",
                                        cycle="2611", attribution="FAA",
                                        license="LicenseRef-public-domain"))
    rec = report.build_report(out, origin="federal", commit="0" * 40, inputs=inputs, probe=False,
                              first_edition=True)
    checks = {c["id"]: c for c in rec["checks"]}
    c = checks["PROC-FAS-001"]
    assert c["status"] == "warn"
    assert c["measured"] == ("2 of 3 PP records stored; 1 dropped for a CRC that does not "
                             "re-compute (KSSF R28 RW28)")
    assert rec["metrics"]["PROC-FAS-001"] == {"source_primaries": 3, "stored": 2,
                                              "crc_dropped": ["KSSF R28 RW28"]}
    assert checks["PROC-FAS-002"]["status"] == "pass"
    assert c["pending_review"]          # QA has not reviewed it: it cannot block yet


# --- DoD 3: the pack-wide count is the source's PP count, minus drops ----------

def test_the_census_matches_the_source(tmp_path):
    inputs = _input(tmp_path)
    out = build_procedures(inputs, tmp_path / "p.pack")
    packmeta.embed_sqlite(out, PackMeta(id="procedures-conus", kind="procedures",
                                        cycle="2611", attribution="FAA",
                                        license="LicenseRef-public-domain"))
    rec = report.build_report(out, origin="federal", commit="0" * 40, inputs=inputs, probe=False,
                              first_edition=True)
    checks = {c["id"]: c for c in rec["checks"]}
    assert (checks["PROC-FAS-001"]["status"], checks["PROC-FAS-001"]["measured"]) == (
        "pass", "3 of 3 PP records stored; 0 dropped for a CRC that does not re-compute")
    assert checks["PROC-FAS-003"]["measured"] == "0 of 3 blocks"


def test_a_stored_block_whose_crc_does_not_match_its_record_fails(tmp_path):
    out = build_procedures(_input(tmp_path), tmp_path / "p.pack")
    con = sqlite3.connect(out)
    con.execute("UPDATE fas_blocks SET crc = '00000000' WHERE airport = 'KSSF'")
    con.commit()
    con.close()
    packmeta.embed_sqlite(out, PackMeta(id="procedures-conus", kind="procedures",
                                        cycle="2611", attribution="FAA",
                                        license="LicenseRef-public-domain"))
    rec = report.build_report(out, origin="federal", commit="0" * 40, probe=False, first_edition=True)
    c = {c["id"]: c for c in rec["checks"]}["PROC-FAS-002"]
    assert (c["status"], c["measured"]) == ("fail", "1 of 3 blocks (KSSF R28 RW28)")


def test_builder_refuses_a_pack_with_no_fas_blocks(tmp_path):
    def strip_pp(line):
        return "" if len(line) == 132 and line[4] == "P" and line[12] == "P" else line
    with pytest.raises(ProceduresBuildError, match="no FAS data blocks"):
        build_procedures(_input(tmp_path, strip_pp), tmp_path / "p.pack")
