# SPDX-License-Identifier: Apache-2.0
"""Per-region d-TPP plate pack orchestrator (AER-1610/PA11)."""

import datetime as dt
import shutil
import sqlite3
from pathlib import Path

import pytest

from packtools import make_plates
from packtools.build.procedures import build_procedures
from packtools.cycles import Cycle
from packtools.dtpp import PlateRecord, parse_metafile
from packtools.regions import load_regions

FIXTURE_XML = Path(__file__).resolve().parent / "fixtures" / "dtpp" / "metafile_2609_sample.xml"
FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "dtpp" / "01244IYLY23.PDF"
CIFP_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cifp" / "FAACIFP18"

_CYCLE = Cycle(cycle="2609", effective=dt.date(2026, 9, 3), expires=dt.date(2026, 10, 1))


def _rec(**kw):
    base = dict(state="XX", state_name="X", city="X", volume="X-1", apt_ident="XXX",
               icao_ident="KXXX", military=False, chart_seq="1", chart_code="IAP",
               chart_name="TEST", pdf_name="X.PDF", procuid=None, faanfd18=None,
               civil=None, amdt_num=None, amdt_date=None)
    base.update(kw)
    return PlateRecord(**base)


# --- airport_region_map ----------------------------------------------------

def test_airport_region_map_basic():
    regions = load_regions()
    airports = [
        {"ident": "KSFO", "latitude_deg": "37.6189", "longitude_deg": "-122.3750"},  # us-west
        {"ident": "KJFK", "latitude_deg": "40.6398", "longitude_deg": "-73.7789"},   # us-east
        {"ident": "EGLL", "latitude_deg": "51.4706", "longitude_deg": "-0.4619"},    # nowhere (UK)
    ]
    m = make_plates.airport_region_map(airports, regions)
    assert m["KSFO"] == ["us-west"]
    assert m["KJFK"] == ["us-east"]
    assert "EGLL" not in m


def test_airport_region_map_overlap_lists_every_region():
    regions = load_regions()
    # us-west/us-central/us-south overlap by regions.yaml's own design
    # (module comment there) -- pick a point inside all three.
    airports = [{"ident": "TEST1", "latitude_deg": "32.0", "longitude_deg": "-103.0"}]
    m = make_plates.airport_region_map(airports, regions)
    assert set(m["TEST1"]) == {"us-west", "us-central", "us-south"}


def test_airport_region_map_only_regions_filters_candidates():
    regions = load_regions()
    airports = [{"ident": "TEST1", "latitude_deg": "32.0", "longitude_deg": "-103.0"}]
    m = make_plates.airport_region_map(airports, regions, only_regions=["us-central"])
    assert m["TEST1"] == ["us-central"]


def test_airport_region_map_skips_rows_with_no_coordinates():
    regions = load_regions()
    airports = [{"ident": "TEST1", "latitude_deg": "", "longitude_deg": ""}]
    assert make_plates.airport_region_map(airports, regions) == {}


def test_airport_region_map_real_adak_falls_outside_the_alaska_region():
    """A real, measured finding, not a hypothetical: Adak Island (PADK) is
    west of regions.yaml's "alaska" region lon_min (-170 vs PADK's real
    -176.676), so a real FAA airport with real d-TPP plates maps to no
    plate region today. See docs/plates.md and the unmapped-record log line
    in make_plate_packs()."""
    regions = load_regions()
    airports = [{"ident": "PADK", "latitude_deg": "51.87187778", "longitude_deg": "-176.67598889"}]
    assert make_plates.airport_region_map(airports, regions) == {}


# --- load_fix_index ---------------------------------------------------------

def test_load_fix_index_from_real_procedures_pack(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(CIFP_FIXTURE, input_dir / "FAACIFP18")
    procedures_pack = build_procedures(input_dir, tmp_path / "procedures-conus.pack")

    fix_index = make_plates.load_fix_index(procedures_pack)
    assert "KSBA" in fix_index
    # I07's IAF fix, real CIFP data from tests/fixtures/cifp/README.md's KSBA set.
    con = sqlite3.connect(str(procedures_pack))
    any_fix = con.execute(
        "SELECT fix_id, fix_lat, fix_lon FROM legs WHERE fix_id IS NOT NULL "
        "AND fix_lat IS NOT NULL LIMIT 1").fetchone()
    con.close()
    fid, lat, lon = any_fix
    matches = [airport_fixes[fid] for airport_fixes in fix_index.values() if fid in airport_fixes]
    assert (lat, lon) in matches


# --- make_plate_packs --------------------------------------------------------

@pytest.fixture()
def pdf_dir(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    (d / "W.PDF").write_bytes(b"%PDF-west")
    (d / "E.PDF").write_bytes(b"%PDF-east")
    return d


def test_make_plate_packs_splits_by_region(pdf_dir, tmp_path):
    records = [
        _rec(apt_ident="KSFO", icao_ident="KSFO", pdf_name="W.PDF"),
        _rec(apt_ident="KJFK", icao_ident="KJFK", pdf_name="E.PDF"),
    ]
    region_map = {"KSFO": ["us-west"], "KJFK": ["us-east"]}
    packs = make_plates.make_plate_packs(
        records=records, region_map=region_map, pdf_dir=pdf_dir, out_dir=tmp_path / "out",
        cycle=_CYCLE, url_base="https://test.local/packs", log=lambda *a: None)
    by_region = {p.region: p for p in packs}
    assert set(by_region) == {"us-west", "us-east"}
    assert by_region["us-west"].record_count == 1
    assert by_region["us-west"].entry.id == "plates-us-west"
    assert by_region["us-west"].entry.kind == "plates"
    assert by_region["us-west"].entry.regions == ["us-west"]
    assert by_region["us-west"].entry.license == make_plates.DEFAULT_LICENSE
    assert by_region["us-west"].entry.effective == "2026-09-03"


def test_make_plate_packs_overlap_airport_lands_in_every_matched_pack(pdf_dir, tmp_path):
    records = [_rec(apt_ident="KABC", icao_ident="KABC", pdf_name="W.PDF")]
    region_map = {"KABC": ["us-west", "us-central"]}
    packs = make_plates.make_plate_packs(
        records=records, region_map=region_map, pdf_dir=pdf_dir, out_dir=tmp_path / "out",
        cycle=_CYCLE, url_base="https://test.local/packs", log=lambda *a: None)
    assert {p.region for p in packs} == {"us-west", "us-central"}
    assert all(p.record_count == 1 for p in packs)


def test_make_plate_packs_skips_regions_with_no_matched_airports(pdf_dir, tmp_path):
    records = [_rec(apt_ident="KSFO", icao_ident="KSFO", pdf_name="W.PDF")]
    packs = make_plates.make_plate_packs(
        records=records, region_map={"KSFO": ["us-west"]}, pdf_dir=pdf_dir,
        out_dir=tmp_path / "out", cycle=_CYCLE, url_base="https://test.local/packs",
        log=lambda *a: None)
    assert {p.region for p in packs} == {"us-west"}


# --- fetch_plate_pdfs --------------------------------------------------------

def test_fetch_plate_pdfs_skips_existing_and_fetches_missing(tmp_path):
    dest = tmp_path / "pdfs"
    dest.mkdir()
    (dest / "HAVE.PDF").write_bytes(b"already here")
    fetched = []

    def fake_downloader(url, path):
        fetched.append(url)
        Path(path).write_bytes(b"downloaded")

    make_plates.fetch_plate_pdfs(["HAVE.PDF", "NEED.PDF"], dest, cycle=_CYCLE,
                                 downloader=fake_downloader, log=lambda *a: None)
    assert fetched == ["https://aeronav.faa.gov/d-tpp/2609/NEED.PDF"]
    assert (dest / "HAVE.PDF").read_bytes() == b"already here"
    assert (dest / "NEED.PDF").read_bytes() == b"downloaded"


# --- run() end to end, no network ------------------------------------------

def test_run_end_to_end_with_pre_seeded_work_dir(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "d-tpp_Metafile_2609.xml").write_bytes(FIXTURE_XML.read_bytes())
    pdf_dir = work_dir / "pdfs"
    pdf_dir.mkdir()
    for name in {r.pdf_name for r in parse_metafile(FIXTURE_XML)}:
        (pdf_dir / name).write_bytes(
            FIXTURE_PDF.read_bytes() if name == "01244IYLY23.PDF" else b"%PDF-fake")

    def fail_if_called(url, path):
        raise AssertionError(f"unexpected network fetch: {url}")

    # PADK/PAEI/PAED/PAFB placed inside the "alaska" region box on purpose
    # (unlike their real coordinates -- see
    # test_airport_region_map_real_adak_falls_outside_the_alaska_region)
    # so this test exercises the orchestration wiring, not the real gap.
    ourairports_rows = [
        {"ident": ident, "latitude_deg": "60.0", "longitude_deg": "-150.0"}
        for ident in ("PADK", "PAEI", "PAED", "PAFB")
    ]

    packs = make_plates.run(
        out_dir=tmp_path / "out", cycle=_CYCLE, url_base="https://test.local/packs",
        work_dir=work_dir, ourairports_rows=ourairports_rows,
        downloader=fail_if_called, log=lambda *a: None)

    assert len(packs) == 1
    pack = packs[0]
    assert pack.region == "alaska"
    assert pack.record_count == 13  # every record in the sample metafile
    con = sqlite3.connect(str(pack.path))
    n_files = con.execute("SELECT COUNT(*) FROM plate_files").fetchone()[0]
    con.close()
    assert n_files == 11  # distinct PDFs, see test_dtpp.py
