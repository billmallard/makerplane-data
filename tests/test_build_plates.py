# SPDX-License-Identifier: Apache-2.0
"""d-TPP plate records + PDFs -> sqlite pack (AER-1610/PA11)."""

import sqlite3
from pathlib import Path

import pytest

from packtools.build.plates import PlatesBuildError, build_plates
from packtools.dtpp import parse_metafile

FIXTURE_XML = Path(__file__).resolve().parent / "fixtures" / "dtpp" / "metafile_2609_sample.xml"
FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "dtpp" / "01244IYLY23.PDF"

# The same real CIFP cycle-2609 fix coordinates test_georef.py's ADK case
# uses -- see that file for provenance.
_ADK_FIX_INDEX = {
    "PADK": {
        "GIDKE": (51.99214722, -176.32959444),
        "SALSE": (51.97878056, -176.25224444),
        "GUISE": (51.95093056, -176.44881944),
        "TICCU": (52.01614444, -176.10220556),
        "LONOK": (52.15180556, -175.54861389),
        "COMAT": (51.80951944, -176.92393333),
    },
}
_ADK_WORD_POSITIONS = {
    "GIDKE": [(247.12, 214.0), (226.72, 407.58), (288.56, 185.39)],
    "SALSE": [(303.01, 252.44)],
    "GUISE": [(204.63, 241.45), (166.96, 418.01)],
    "TICCU": [(169.62, 158.45), (357.49, 271.5), (200.92, 139.56), (353.08, 228.39)],
    "LONOK": [(232.83, 168.71)],
    "COMAT": [(36.63, 326.59), (95.87, 399.43)],
}


@pytest.fixture()
def pdf_dir(tmp_path):
    """Real pdf_name -> bytes layout, using the one real PDF fixture for
    every distinct pdf_name the sample metafile references (content is
    irrelevant to most tests here; only 01244IYLY23.PDF's actual bytes
    matter for the real-extraction test)."""
    records = parse_metafile(FIXTURE_XML)
    d = tmp_path / "pdfs"
    d.mkdir()
    real_bytes = FIXTURE_PDF.read_bytes()
    for name in {r.pdf_name for r in records}:
        (d / name).write_bytes(real_bytes if name == "01244IYLY23.PDF" else b"%PDF-fake-" + name.encode())
    return d


def test_build_plates_writes_all_tables(pdf_dir, tmp_path):
    records = parse_metafile(FIXTURE_XML)
    out = build_plates(records, pdf_dir, tmp_path / "plates-alaska.pack", cycle="2609")
    con = sqlite3.connect(str(out))
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("plate_files", "plates", "plate_georef")}
    con.close()
    assert counts["plates"] == len(records) == 13
    assert counts["plate_files"] == 11   # distinct pdf_names, see test_dtpp.py
    assert counts["plate_georef"] == 0   # no fix_index given -> nothing attempted


def test_build_plates_stores_pdf_bytes_and_sha256(pdf_dir, tmp_path):
    records = parse_metafile(FIXTURE_XML)
    out = build_plates(records, pdf_dir, tmp_path / "out.pack", cycle="2609")
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT bytes, sha256, data FROM plate_files WHERE pdf_name = '01244IYLY23.PDF'").fetchone()
    con.close()
    real_bytes = FIXTURE_PDF.read_bytes()
    import hashlib
    assert row[0] == len(real_bytes)
    assert row[1] == hashlib.sha256(real_bytes).hexdigest()
    assert row[2] == real_bytes


def test_build_plates_dedupes_shared_booklet_into_one_blob(pdf_dir, tmp_path):
    records = parse_metafile(FIXTURE_XML)
    out = build_plates(records, pdf_dir, tmp_path / "out.pack", cycle="2609")
    con = sqlite3.connect(str(out))
    # AKRAD.PDF is referenced by two plates rows (EDF, FBK) but must be
    # stored exactly once (distinct-file basis, see module docstring).
    plate_rows = con.execute(
        "SELECT COUNT(*) FROM plates WHERE pdf_name = 'AKRAD.PDF'").fetchone()[0]
    file_rows = con.execute(
        "SELECT COUNT(*) FROM plate_files WHERE pdf_name = 'AKRAD.PDF'").fetchone()[0]
    con.close()
    assert plate_rows == 2
    assert file_rows == 1


def test_build_plates_raises_on_missing_pdf(tmp_path):
    records = parse_metafile(FIXTURE_XML)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(PlatesBuildError, match="missing from"):
        build_plates(records, empty_dir, tmp_path / "out.pack", cycle="2609")


def test_build_plates_raises_on_no_records(tmp_path):
    with pytest.raises(PlatesBuildError, match="no plate records"):
        build_plates([], tmp_path, tmp_path / "out.pack", cycle="2609")


def test_build_plates_georef_uses_the_real_pdf_and_real_fix_coordinates(pdf_dir, tmp_path):
    """End-to-end with a fake (injected) word extractor pinned to the real
    values tests/test_georef.py captured from this exact PDF -- proves the
    wiring (which records get attempted, how the result lands in
    plate_georef) without needing pymupdf installed to run this test."""
    records = parse_metafile(FIXTURE_XML)
    out = build_plates(
        records, pdf_dir, tmp_path / "out.pack", cycle="2609",
        fix_index=_ADK_FIX_INDEX,
        extract_words=lambda path: _ADK_WORD_POSITIONS if path.name == "01244IYLY23.PDF" else {})
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT pdf_name, status, control_points FROM plate_georef").fetchall()
    con.close()
    # Only IAP charts at an airport present in fix_index are attempted --
    # ADK has 5 IAP records in the fixture (Y, Z, RNAV, TACAN, NDB/DME).
    assert len(rows) == 5
    row = {r[0]: r for r in rows}["01244IYLY23.PDF"]
    # Matches the real, honest negative result test_georef.py documents:
    # only 2 of 7 fixes on this real plate are unambiguous.
    assert row[1] == "insufficient_control_points"
    assert row[2] == 2


def test_build_plates_georef_skips_airports_outside_fix_index(pdf_dir, tmp_path):
    records = parse_metafile(FIXTURE_XML)
    out = build_plates(
        records, pdf_dir, tmp_path / "out.pack", cycle="2609",
        fix_index={},  # no airport known to the (fake) procedures pack
        extract_words=lambda path: {})
    con = sqlite3.connect(str(out))
    n = con.execute("SELECT COUNT(*) FROM plate_georef").fetchone()[0]
    con.close()
    assert n == 0


@pytest.mark.parametrize("kind", ["real"])
def test_build_plates_real_pymupdf_extraction_matches_pinned_positions(pdf_dir, tmp_path, kind):
    """Uses the real default extractor (real pymupdf, real PDF) end to end
    -- skipped if pymupdf isn't installed (packtools[plates] extra)."""
    pytest.importorskip("pymupdf")
    records = [r for r in parse_metafile(FIXTURE_XML) if r.pdf_name == "01244IYLY23.PDF"]
    out = build_plates(records, pdf_dir, tmp_path / "out.pack", cycle="2609",
                       fix_index=_ADK_FIX_INDEX)
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT status, control_points FROM plate_georef WHERE pdf_name = '01244IYLY23.PDF'"
    ).fetchone()
    con.close()
    assert row == ("insufficient_control_points", 2)
