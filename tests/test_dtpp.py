# SPDX-License-Identifier: Apache-2.0
"""FAA d-TPP metafile parsing + URL builders (AER-1610/PA11)."""

import datetime as _dt
from pathlib import Path

import pytest

from packtools.cycles import Cycle
from packtools.dtpp import (
    DtppParseError,
    distinct_pdf_names,
    metafile_url,
    parse_metafile,
    plate_pdf_url,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dtpp" / "metafile_2609_sample.xml"

_CYCLE_2609 = Cycle(cycle="2609", effective=_dt.date(2026, 9, 3), expires=_dt.date(2026, 10, 1))


def test_metafile_url():
    assert metafile_url(_CYCLE_2609) == (
        "https://aeronav.faa.gov/d-tpp/2609/xml_data/d-tpp_Metafile.xml")


def test_plate_pdf_url():
    assert plate_pdf_url(_CYCLE_2609, "01244IYLY23.PDF") == (
        "https://aeronav.faa.gov/d-tpp/2609/01244IYLY23.PDF")


def test_parse_metafile_drops_useraction_d_placeholder():
    records = parse_metafile(FIXTURE)
    assert all(r.pdf_name != "DELETED_JOB.PDF" for r in records)
    assert not any(r.chart_name == "FALCO FIVE" for r in records)


def test_parse_metafile_keeps_every_real_record():
    records = parse_metafile(FIXTURE)
    # ADK: 10 real records; EIL: 1 real (TAKEOFF MINIMUMS) + 1 dropped (D);
    # EDF: 1; FBK: 1.
    assert len(records) == 10 + 1 + 1 + 1


def test_parse_metafile_adk_ils_y_record_fields():
    records = parse_metafile(FIXTURE)
    rec = next(r for r in records if r.pdf_name == "01244IYLY23.PDF")
    assert rec.apt_ident == "ADK"
    assert rec.icao_ident == "PADK"
    assert rec.state == "AK"
    assert rec.military is False
    assert rec.chart_code == "IAP"
    assert rec.chart_name == "ILS Y OR LOC Y RWY 23"
    assert rec.procuid == "39683"
    assert rec.amdt_num == "0"
    assert rec.amdt_date == "12/05/2019"


def test_parse_metafile_military_flag():
    records = parse_metafile(FIXTURE)
    eil = [r for r in records if r.apt_ident == "EIL"]
    assert eil and all(r.military for r in eil)
    adk = [r for r in records if r.apt_ident == "ADK"]
    assert adk and all(not r.military for r in adk)


def test_parse_metafile_shared_booklet_pdf_referenced_by_multiple_airports():
    # AKRAD.PDF ("RADAR MINIMUMS") is a real regional MIN booklet shared by
    # EDF and FBK (docs/dtpp_plates_spike.md §1.2's shared-booklet finding).
    records = parse_metafile(FIXTURE)
    airports = {r.apt_ident for r in records if r.pdf_name == "AKRAD.PDF"}
    assert airports == {"EDF", "FBK"}
    # AKTO.PDF ("TAKEOFF MINIMUMS") is shared by ADK and EIL too.
    airports = {r.apt_ident for r in records if r.pdf_name == "AKTO.PDF"}
    assert airports == {"ADK", "EIL"}


def test_distinct_pdf_names_dedupes_shared_booklets():
    records = parse_metafile(FIXTURE)
    names = distinct_pdf_names(records)
    assert len(names) == len(set(names))  # no duplicates
    assert names.count("AKRAD.PDF") == 1
    # 10 distinct PDFs at ADK (one of them AKTO.PDF) + 1 new one from EDF's
    # AKRAD.PDF; EIL's AKTO.PDF and FBK's AKRAD.PDF are both repeats.
    assert len(names) == 11


def test_parse_metafile_rejects_wrong_root_element():
    with pytest.raises(DtppParseError, match="not a d-TPP metafile"):
        parse_metafile("<not_digital_tpp/>")


def test_parse_metafile_accepts_raw_bytes():
    xml = FIXTURE.read_bytes()
    records = parse_metafile(xml)
    assert len(records) == 13
