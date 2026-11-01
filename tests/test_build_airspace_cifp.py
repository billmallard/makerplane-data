# SPDX-License-Identifier: Apache-2.0
"""FAA CIFP section U -> airspace pack (AER-4445 / AIRSP1).

Fixture: real 2611 records, tests/fixtures/cifp_airspace/README.md."""

import dataclasses
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from packtools import arinc424, cycles, report, signing
from packtools.build import BUILDERS
from packtools.build.airspace_cifp import (ARC_TOLERANCE_NM, AirspaceCifpBuildError,
                                           build_airspace_cifp, iter_sectors)
from packtools.geo import great_circle_nm
from packtools.packmeta import PackMeta, embed_sqlite
from packtools.run_cyclical import CyclicalRunner, PipelineFailure
from packtools.schema_guard import NULL_GUARD
from packtools.sources import SOURCES
from packtools.upload import LocalStore
from tests.gatefake import standin_reporter

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "cifp_airspace"
FIXTURE = FIXTURE_DIR / "FAACIFP18"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = build_airspace_cifp(FIXTURE_DIR, tmp_path_factory.mktemp("asp") / "asp.sqlite")
    con = sqlite3.connect(str(out))
    con.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in con.execute("SELECT * FROM airspaces")}
    con.close()
    return out, rows


def _ring(row):
    return json.loads(row["geometry"])["coordinates"][0]


def _props(row):
    return json.loads(row["properties_json"])


# --- parser -------------------------------------------------------------------

def test_parser_reads_ksba_class_c_fields_where_the_live_file_puts_them():
    recs = [r for r in arinc424.iter_airspace_records(FIXTURE) if r.designation == "KSBA"]
    assert [(r.multiple_code, r.seq, r.boundary_via) for r in recs] == [
        ("A", 10, "CE"), ("B", 20, "R"), ("B", 30, "G"), ("B", 40, "L"), ("B", 50, "GE")]
    a, b = recs[0], recs[1]
    assert (a.section, a.area, a.icao, a.airspace_type, a.classification) == (
        "UC", "USA", "K2", "A", "C")
    assert (a.centre_section, a.centre_subsection) == ("P", "A")
    assert (a.arc_origin_lat, a.arc_origin_lon, a.arc_distance_nm) == (
        34.42619167, -119.84149167, 5.0)
    assert a.lat is None and a.lon is None                  # a circle has no vertex
    assert (a.lower_limit, a.lower_unit, a.upper_limit, a.upper_unit) == (
        "GND", "A", "04000", "M")
    assert a.name == "SANTA BARBARA MUNI" and a.cycle == "2502"
    assert (b.lat, b.lon, b.arc_distance_nm, b.arc_bearing) == (
        34.42602500, -119.64001389, 10.0, 90.0)
    assert (b.lower_limit, b.lower_unit) == ("01500", "M")
    assert recs[2].name is None and recs[2].lower_limit is None   # first record only


def test_parser_folds_the_controlling_agency_continuation_into_its_primary():
    recs = [r for r in arinc424.iter_airspace_records(FIXTURE) if r.designation == "DELTA2"]
    assert (recs[0].area, recs[0].icao, recs[0].airspace_type) == ("CAN", "PA", "M")
    assert recs[0].continuation == "1"
    assert recs[0].controlling_agency == "FAA ANCHORAGE ARTCC"
    assert [r.seq for r in recs] == [10, 20, 30, 40, 50, 60]   # continuation is not a vertex


def test_parser_refuses_an_unknown_continuation_type(tmp_path):
    lines = FIXTURE.read_text(encoding="latin-1").splitlines()
    cont = next(i for i, ln in enumerate(lines) if ln.startswith("SCANURPAMDELTA2    A00102C"))
    lines[cont] = lines[cont][:25] + "T" + lines[cont][26:]
    bad = tmp_path / "FAACIFP18"
    bad.write_text("\n".join(lines) + "\n", encoding="latin-1")
    with pytest.raises(ValueError, match="unhandled airspace continuation"):
        list(arinc424.iter_airspace_records(bad))
    with pytest.raises(AirspaceCifpBuildError, match="unhandled airspace continuation"):
        build_airspace_cifp(tmp_path, tmp_path / "out.sqlite")


# --- builder ------------------------------------------------------------------

def test_registered_in_builders():
    assert BUILDERS["airspace-cifp"] is build_airspace_cifp


def test_one_row_per_sector_keyed_by_section_region_type_designation_and_sector(built):
    _, rows = built
    assert len(rows) == sum(1 for _ in iter_sectors(FIXTURE)) == 31
    assert {i for i in rows if "KLAX" in i} == {f"cifp-UC-K2-T-KLAX-{c}" for c in "ABCDEFGHIJKLMN"}
    assert "cifp-UR-K2-R-2508-A" in rows and "cifp-UC-PA-Z-PAEI-A" in rows


def test_class_type_and_limits_use_openaip_codes(built):
    _, rows = built

    def lim(rid):
        r = rows[rid]
        return (r["lower_limit"], r["lower_limit_unit"], r["lower_limit_reference"],
                r["upper_limit"], r["upper_limit_unit"], r["upper_limit_reference"])
    ksba = rows["cifp-UC-K2-A-KSBA-A"]
    assert (ksba["type"], ksba["icao_class"], ksba["country"]) == ("0", "2", "US")
    assert lim("cifp-UC-K2-A-KSBA-A") == ("0", "1", "0", "4000", "1", "1")    # GND, MSL
    assert lim("cifp-UC-K2-A-KSBA-B") == ("1500", "1", "1", "4000", "1", "1")
    assert rows["cifp-UC-K2-T-KLAX-A"]["icao_class"] == "1"
    assert rows["cifp-UC-PA-Z-PAEI-A"]["icao_class"] == "3"
    r2508 = rows["cifp-UR-K2-R-2508-A"]
    assert (r2508["type"], r2508["icao_class"]) == ("1", "8")
    assert lim("cifp-UR-K2-R-2508-A") == ("20000", "1", "1", "UNLTD", None, None)
    assert lim("cifp-UR-PG-R-7201A-A") == ("0", "1", "0", "600", "6", "2")    # FL600
    # "up to but not including 15,000": stored as the source writes it.
    assert lim("cifp-UR-K2-M-ROBERTS-A") == ("500", "1", "0", "14999", "1", "1")
    moa = rows["cifp-UR-K2-M-ROBERTS-A"]
    assert (moa["type"], moa["icao_class"]) == ("0", "8")      # no exact openAIP code
    assert _props(moa)["airspace_type_label"] == "MOA"
    assert moa["remarks"] == "Controlling agency: FAA OAKLAND ARTCC"
    assert moa["activity"] is None


def test_circle_is_densified_on_its_radius(built):
    _, rows = built
    ring = _ring(rows["cifp-UC-K2-A-KSBA-A"])
    centre = (34.42619167, -119.84149167)
    assert ring[0] == ring[-1] and len(ring) > 40
    for lon, lat in ring:
        assert great_circle_nm(centre, (lat, lon)) == pytest.approx(5.0, abs=1e-4)


def test_arc_passes_through_the_published_vertices_within_tolerance(built):
    _, rows = built
    row = rows["cifp-UC-K2-A-KSBA-B"]
    ring = _ring(row)
    pts = {(round(lon, 6), round(lat, 6)) for lon, lat in ring}
    for v in _props(row)["boundary"]:
        assert (round(v[3], 6), round(v[2], 6)) in pts        # every vertex is on the ring
    # The clockwise 10 NM arc from bearing 090: no chord's midpoint sags more
    # than the stated tolerance inside the arc through its two ends.
    centre = (34.42619167, -119.84149167)
    on_arc = [(lat, lon) for lon, lat in ring
              if abs(great_circle_nm(centre, (lat, lon)) - 10.0) < 0.05]
    assert len(on_arc) > 20
    for p, q in zip(on_arc, on_arc[1:]):
        if great_circle_nm(p, q) > 2:     # the filter's jump between the arc's two ends
            continue
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        r = (great_circle_nm(centre, p) + great_circle_nm(centre, q)) / 2
        assert r - great_circle_nm(centre, mid) <= ARC_TOLERANCE_NM + 1e-4


def _inside(ring, lat, lon):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def test_arc_direction_puts_the_ksba_shelf_over_the_water(built):
    # Sector B runs clockwise (R) at 10 NM from bearing 090 through south to
    # 295, and back counter-clockwise (L) at 5 NM: the shelf is the southern
    # half-ring. Drawn the wrong way round it would cover the north instead.
    from packtools.build.airspace_cifp import _destination
    _, rows = built
    ring = _ring(rows["cifp-UC-K2-A-KSBA-B"])
    centre = (34.42619167, -119.84149167)
    assert _inside(ring, *_destination(centre, 180.0, 7.5))
    assert not _inside(ring, *_destination(centre, 0.0, 7.5))
    assert not _inside(ring, *_destination(centre, 180.0, 2.5))   # the surface area


def test_rings_are_closed_counter_clockwise_and_simple(built):
    _, rows = built
    for rid, row in rows.items():
        ring = _ring(row)
        assert ring[0] == ring[-1], rid
        area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
        assert area2 > 0, rid
        assert report._ring_problem(ring) is None, rid


def test_source_spike_is_removed_from_the_geometry_but_kept_in_the_records(built):
    _, rows = built
    row = rows["cifp-UC-K4-A-KOKC-A"]
    props = _props(row)
    assert props["spikes_removed"] == 1
    lats = [round(v[2], 6) for v in props["boundary"]]
    assert lats.count(35.377192) == 2                # the out-and-back stays on record
    assert all(_props(r)["spikes_removed"] == 0 for i, r in rows.items() if "KSBA" in i)


def test_zero_height_sectors_are_flagged(built):
    _, rows = built
    flagged = sorted(i for i, r in rows.items() if _props(r)["zero_height"])
    assert flagged == ["cifp-UR-K2-M-ROBERTS-B", "cifp-UR-PG-R-7201A-B"]


def test_properties_keep_every_source_record_verbatim(built):
    _, rows = built
    source = [ln for ln in FIXTURE.read_text(encoding="latin-1").splitlines()
              if ln[4:5] == "U" and ln[24] in "01"]
    kept = [ln for r in rows.values() for ln in _props(r)["records"]]
    assert sorted(kept) == sorted(source)


def test_an_unmapped_icao_region_fails_the_build(tmp_path):
    lines = FIXTURE.read_text(encoding="latin-1").splitlines()
    i = next(i for i, ln in enumerate(lines) if ln.startswith("SUSAUCK2AKSBA"))
    lines[i] = lines[i][:6] + "CY" + lines[i][8:]
    (tmp_path / "FAACIFP18").write_text("\n".join(lines) + "\n", encoding="latin-1")
    with pytest.raises(AirspaceCifpBuildError, match="ICAO region 'CY'"):
        build_airspace_cifp(tmp_path, tmp_path / "out.sqlite")


def test_missing_input_fails(tmp_path):
    with pytest.raises(AirspaceCifpBuildError, match="no FAACIFP18"):
        build_airspace_cifp(tmp_path, tmp_path / "out.sqlite")


# --- the gate checks ----------------------------------------------------------

def _pack(sqlite_path, dest, pack_id="airspace-conus"):
    shutil.copy(sqlite_path, dest)
    embed_sqlite(dest, PackMeta(id=pack_id, kind="airspace", cycle="2611",
                                effective="2026-10-29", expires="2026-11-26",
                                attribution="FAA CIFP (public domain)",
                                license="LicenseRef-us-public-domain"))
    return dest


def _checks(rec):
    return {c["id"]: c for c in rec["checks"]}


def test_asp_checks_pass_on_the_real_fixture(built, tmp_path):
    pack = _pack(built[0], tmp_path / "a.pack")
    rec = report.build_report(pack, origin="federal", first_edition=True,
                              inputs=FIXTURE_DIR, probe=False, commit="0" * 40)
    c = _checks(rec)
    assert c["ASP-001"]["status"] == "pass"
    assert c["ASP-002"]["status"] == "pass"
    assert c["ASP-002"]["measured"].startswith("0 floor above ceiling")
    assert c["ASP-003"]["status"] == "pass"
    assert rec["metrics"]["ASP-003"]["source_sectors"] == {"C": 25, "R": 6}
    assert report.validate_report(rec) == []
    # Still ungated: no build of this kind clears the gate until QA accepts it.
    assert rec["gated"] is False
    assert any(b.startswith("ungated") for b in report.publish_blockers(rec))


def test_asp_003_without_inputs_blocks_and_an_openaip_pack_is_exempt(built, tmp_path):
    pack = _pack(built[0], tmp_path / "a.pack")
    rec = report.build_report(pack, origin="federal", first_edition=True, probe=False,
                              commit="0" * 40)
    assert _checks(rec)["ASP-003"]["not_run_reason"] == "no-inputs"
    assert any(b.startswith("ASP-003 is blocking") for b in report.publish_blockers(rec))

    con = sqlite3.connect(str(tmp_path / "oaip.sqlite"))
    con.execute(report_schema())
    con.execute("INSERT INTO airspaces (id, country, geometry, properties_json, lower_limit, "
                "lower_limit_unit, lower_limit_reference, upper_limit, upper_limit_unit, "
                "upper_limit_reference) VALUES ('f1', 'CA', ?, '{}', '0', '1', '0', '3500', "
                "'1', '1')", (json.dumps({"type": "Polygon", "coordinates": [
                    [[-114, 51], [-113.9, 51], [-113.9, 51.1], [-114, 51]]]}),))
    con.commit()
    con.close()
    oaip = _pack(tmp_path / "oaip.sqlite", tmp_path / "o.pack", pack_id="airspace-ca")
    rec = report.build_report(oaip, first_edition=True, probe=False, commit="0" * 40)
    c = _checks(rec)
    assert c["ASP-003"]["not_run_reason"] == "not-cifp"
    assert not any(b.startswith("ASP-003") for b in report.publish_blockers(rec))
    assert c["ASP-001"]["status"] == "pass" and c["ASP-002"]["status"] == "pass"


def report_schema():
    from packtools.build.airspace import _SCHEMA
    return _SCHEMA


def test_asp_001_and_002_catch_bad_geometry_and_inverted_limits(tmp_path):
    db = tmp_path / "bad.sqlite"
    con = sqlite3.connect(str(db))
    con.execute(report_schema())
    bowtie = [[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]
    open_ring = [[0, 0], [1, 0], [1, 1], [0, 1]]
    square = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    for rid, ring, lo, hi in (("bowtie", bowtie, "0", "100"), ("open", open_ring, "0", "100"),
                              ("inverted", square, "5000", "3000"),
                              ("agl-msl", square, "500", "3000")):
        con.execute("INSERT INTO airspaces (id, country, geometry, properties_json, lower_limit, "
                    "lower_limit_unit, lower_limit_reference, upper_limit, upper_limit_unit, "
                    "upper_limit_reference) VALUES (?, 'US', ?, '{}', ?, '1', ?, ?, '1', '1')",
                    (rid, json.dumps({"type": "Polygon", "coordinates": [ring]}), lo,
                     "0" if rid == "agl-msl" else "1", hi))
    con.commit()
    con.close()
    rec = report.build_report(_pack(db, tmp_path / "b.pack"), first_edition=True,
                              probe=False, commit="0" * 40)
    c = _checks(rec)
    assert c["ASP-001"]["status"] == "fail"
    assert "self-intersects: 1 (bowtie)" in c["ASP-001"]["measured"]
    assert "not closed: 1 (open)" in c["ASP-001"]["measured"]
    assert c["ASP-002"]["status"] == "fail"
    assert c["ASP-002"]["measured"].startswith("1 floor above ceiling of 3 compared")
    assert "1 not comparable (AGL against MSL)" in c["ASP-002"]["measured"]
    assert "inverted" in c["ASP-002"]["measured"]


def test_ring_problem_unclosed_and_touching():
    assert report._ring_problem([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0.5]]) == "not closed"
    # A ring that touches itself at a vertex is not simple.
    pinch = [[0, 0], [2, 0], [1, 1], [2, 2], [0, 2], [1, 1], [0, 0]]
    assert report._ring_problem(pinch) == "self-intersects"


# --- the source and the pipeline ----------------------------------------------

def test_airspace_conus_is_registered_deferred_and_federal():
    src = SOURCES["airspace-conus"]
    assert (src.kind, src.cadence, src.builder, src.origin) == (
        "airspace", "airac", "airspace-cifp", "federal")
    assert src.builder in BUILDERS
    assert src.implemented is False
    assert src.url_for(cycles.airac_cycle(dt.date(2026, 10, 31))).endswith("CIFP_261029.zip")


def _runner(tmp_path, reporter):
    sk, _pub = signing.generate_keypair()

    def fetch(url, work_dir, member=None):
        d = Path(work_dir) / "extracted"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE, d / "FAACIFP18")
        return d
    return CyclicalRunner(reporter=reporter, store=LocalStore(tmp_path / "r2"), secret=sk,
                          work_dir=tmp_path / "work", fetcher=fetch, guard=NULL_GUARD,
                          url_base="https://example.invalid/packs", today=dt.date(2026, 10, 31))


def test_the_pipeline_passes_the_source_origin_to_the_gate(tmp_path):
    seen = []

    def capture(path, **kw):
        seen.append(kw.get("origin"))
        return standin_reporter(path, **kw)
    runner = _runner(tmp_path, capture)
    with pytest.raises(PipelineFailure):          # ungated; see the next test
        runner.run([dataclasses.replace(SOURCES["airspace-conus"], implemented=True)])
    assert seen == ["federal", "federal"]       # current and next cycle


def test_the_real_gate_refuses_airspace_conus_while_the_kind_is_ungated(tmp_path):
    runner = _runner(tmp_path, report.build_report)
    with pytest.raises(PipelineFailure, match="ungated: kind airspace"):
        runner.run([SOURCES["airspace-conus"]])
    rec = json.loads((tmp_path / "work" / "reports" /
                      "airspace-conus-2611.rejected.json").read_text())
    assert rec["origin"] == "federal"
    assert {c["id"]: c["status"] for c in rec["checks"]
            if c["id"].startswith("ASP")} == {"ASP-001": "pass", "ASP-002": "pass",
                                              "ASP-003": "pass"}
