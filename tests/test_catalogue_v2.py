# SPDX-License-Identifier: Apache-2.0
"""Catalogue v2 (TV7): cross-source checks against Copernicus terrain,
golden facts, per-state OSM diffs and diff maps, and the terrain tile,
void and water-mask checks."""

import array
import json
import sqlite3
import struct
import sys
import zipfile
from pathlib import Path

import pytest

from packtools import admin1, diffmap, golden, packmeta, report, terrain_ref
from packtools.packmeta import PackMeta

FT = terrain_ref.M_PER_FT
LICENSE = dict(attribution="FAA (public domain)", license="LicenseRef-us-public-domain")


def _by_id(rec):
    return {c["id"]: c for c in rec["checks"]}


def _rep(path, **kw):
    kw.setdefault("probe", False)
    kw.setdefault("commit", "0" * 40)
    return report.build_report(path, **kw)


# --- terrain fixtures ---------------------------------------------------------

def _grid(side, fn):
    """A big-endian int16 .hgt of ``side`` posts a side, value ``fn(row, col)``
    with row 0 at the north edge."""
    return struct.pack(f">{side * side}h", *(fn(r, c) for r in range(side) for c in range(side)))


def _hgt_dir(root, tiles):
    for name, data in tiles.items():
        d = root / name[:3]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.hgt").write_bytes(data)
    return root


def _terrain_pack(path, members, pack_id="terrain-us-central"):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    packmeta.embed_zip(path, PackMeta(id=pack_id, kind="terrain", cycle="2024edr1",
                                      attribution="Copernicus GLO-30",
                                      license="LicenseRef-copernicus"))
    return Path(path)


def _flat(metres, side=11):
    return _grid(side, lambda r, c: metres)


# --- terrain_ref ----------------------------------------------------------------

def test_tile_names_follow_the_sw_corner():
    assert terrain_ref.tile_name(39.86, -104.67) == "N39W105"
    assert terrain_ref.tile_name(-0.5, 0.5) == "S01E000"


def test_bilinear_samples_between_posts_and_voids_give_none(tmp_path):
    # 11 posts span one degree: row 0 is the north edge (lat 41), col 0 the
    # west edge (lon -100). Elevation rises 10 m per column eastward.
    root = _hgt_dir(tmp_path, {"N40W100": _grid(11, lambda r, c: c * 10),
                               "N40W099": _grid(11, lambda r, c: -32768 if c == 0 else 5)})
    ref = terrain_ref.TerrainRef([root])
    got = dict(ref.sample_many([("w", 40.5, -100.0), ("mid", 40.5, -99.95),
                                ("e", 40.0, -99.0 - 1e-9), ("void", 40.5, -99.0),
                                ("none", 10.0, -10.0)]))
    assert got["w"] == pytest.approx(0)
    assert got["mid"] == pytest.approx(5)          # half way between posts 0 and 1
    assert got["e"] == pytest.approx(100)
    assert got["void"] is None and got["none"] is None
    assert ref.tiles_read == 2                      # each tile read once


def test_row_zero_is_the_north_edge(tmp_path):
    root = _hgt_dir(tmp_path, {"N40W100": _grid(11, lambda r, c: r * 10)})
    got = dict(terrain_ref.TerrainRef([root]).sample_many(
        [("n", 41.0 - 1e-9, -99.5), ("s", 40.0, -99.5), ("q", 40.75, -99.5)]))
    assert got["n"] == pytest.approx(0, abs=1e-5)
    assert got["s"] == pytest.approx(100)
    assert got["q"] == pytest.approx(25)


def test_a_pack_and_its_tile_tree_sample_the_same(tmp_path):
    data = _grid(11, lambda r, c: r * 7 + c)
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": data})
    tree = _hgt_dir(tmp_path / "tree", {"N40W100": data})
    pts = [("a", 40.33, -99.71), ("b", 40.9, -99.1)]
    assert dict(terrain_ref.TerrainRef([pack]).sample_many(pts)) == \
        dict(terrain_ref.TerrainRef([tree]).sample_many(pts))
    assert terrain_ref.open_source(pack).label == "terrain-us-central 2024edr1"


def test_a_source_with_no_tiles_is_refused(tmp_path):
    with pytest.raises(ValueError):
        terrain_ref.open_source(tmp_path)


def test_remote_member_reads_one_range_and_checks_the_crc(tmp_path, monkeypatch):
    data = _grid(11, lambda r, c: 42)
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": data})
    blob = pack.read_bytes()
    reader = terrain_ref._RangeReader.__new__(terrain_ref._RangeReader)
    reader.size, reader.etag, reader.pos = len(blob), None, 0
    monkeypatch.setattr(reader, "get", lambda start, n: blob[start:start + n], raising=False)
    info = zipfile.ZipFile(pack).getinfo("N40/N40W100.hgt")
    assert reader.member(info) == data
    # One flipped bit in the compressed data: a zlib error or a CRC mismatch.
    corrupt = blob[:info.header_offset + 60] + bytes([blob[info.header_offset + 60] ^ 1]) + \
        blob[info.header_offset + 61:]
    monkeypatch.setattr(reader, "get", lambda start, n: corrupt[start:start + n], raising=False)
    with pytest.raises(Exception):
        reader.member(info)


# --- XS-001 / XS-002 / TER-005 ----------------------------------------------------

def _airports_pack(path, rows, cycle="2612", kind="navdata"):
    """rows: (icao, lat, lon, elev_ft)."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE airports (site_no TEXT PRIMARY KEY, icao TEXT NOT NULL, "
                "name TEXT, lat REAL NOT NULL, lon REAL NOT NULL, elev_ft REAL)")
    con.executemany("INSERT INTO airports VALUES (?, ?, 'x', ?, ?, ?)",
                    [(str(i), *r) for i, r in enumerate(rows)])
    con.execute("CREATE TABLE runways (site_no TEXT, rwy_id TEXT, length_ft REAL, width_ft REAL, "
                "surface TEXT, lighting TEXT)")
    con.execute("INSERT INTO runways VALUES ('0', '07/25', 6052, 150, 'ASPH', '')")
    con.commit()
    con.close()
    packmeta.embed_sqlite(path, PackMeta(id="airports-conus", kind=kind, cycle=cycle, **LICENSE))
    return Path(path)


def _flat_tree(tmp_path, metres=1000):
    return _hgt_dir(tmp_path / "terrain", {"N40W100": _flat(metres)})


def test_field_elevations_that_agree_with_terrain_pass(tmp_path):
    tree = _flat_tree(tmp_path)
    pack = _airports_pack(tmp_path / "a.pack", [
        ("AAA", 40.5, -99.5, 1000 / FT + 20), ("BBB", 40.2, -99.2, 1000 / FT - 10),
        ("FAR", 10.0, -10.0, 50)])             # no terrain there: not compared
    rec = _rep(pack, terrain=[tree])
    c = _by_id(rec)["XS-001"]
    assert c["status"] == "pass", c
    assert c["measured"].startswith("2 of 3 airports compared; median |difference| ")
    assert "0 beyond 500 ft" in c["measured"]
    assert c["pending_review"] == report.V2_REVIEW
    assert rec["metrics"]["XS-001"]["terrain"] == ["directory terrain"]


def test_a_systematic_error_warns_and_an_old_outlier_is_carried_not_new(tmp_path):
    tree = _flat_tree(tmp_path)
    # Elevations written in metres instead of feet: every airport is off.
    metres = _airports_pack(tmp_path / "m.pack", [(f"A{i}", 40.5, -99.5, 1000) for i in range(5)])
    c = _by_id(_rep(metres, terrain=[tree]))["XS-001"]
    assert c["status"] == "warn" and "median |difference| 2,280.8 ft" in c["measured"]

    rows = [(f"A{i}", 40.5, -99.5, 1000 / FT) for i in range(9)] + [("BAD", 40.5, -99.5, 18)]
    prev = _rep(_airports_pack(tmp_path / "p.pack", rows, cycle="2611"), terrain=[tree])
    assert _by_id(prev)["XS-001"]["status"] == "pass"       # no previous to be new against
    assert prev["metrics"]["XS-001"]["outliers"] == {"BAD": -3263}
    same = _rep(_airports_pack(tmp_path / "s.pack", rows), terrain=[tree], previous=prev)
    c = _by_id(same)["XS-001"]
    assert c["status"] == "pass" and "BAD -3,263 ft" in c["measured"]
    assert "0 not beyond it in the previous edition" in c["measured"]
    worse = _rep(_airports_pack(tmp_path / "w.pack", rows + [("NEW", 40.5, -99.5, 9000)]),
                 terrain=[tree], previous=prev)
    c = _by_id(worse)["XS-001"]
    assert c["status"] == "warn" and "1 not beyond it in the previous edition (NEW)" in c["measured"]


def test_pending_checks_never_block_a_publish_before_qa_review(tmp_path):
    tree = _flat_tree(tmp_path)
    pack = _airports_pack(tmp_path / "m.pack", [("A", 40.5, -99.5, 1000)])
    rec = _rep(pack, terrain=[tree])
    assert _by_id(rec)["XS-001"]["status"] == "warn"           # and no build note
    assert "XS-001" not in " ".join(report.publish_blockers(rec))
    # Even absent from the record (an older record), a pending check is not missing.
    rec["checks"] = [c for c in rec["checks"] if c["id"] != "XS-001"]
    assert "XS-001" not in " ".join(report.publish_blockers(rec))
    # The flag lives in the catalogue, never in the record (R1).
    pending = {c.id for cs in report.all_checks().values() for c in cs if c.review}
    assert pending == {"XS-001", "XS-002", "TER-005", "OSM-002", "OSM-006"}


def test_checks_qa_accepted_gate_a_publish(tmp_path):
    # AER-4584: GLD-001, TER-007 and TER-008 gate now. Non-blocking, so a
    # warn needs a build note.
    pack = _airports_pack(tmp_path / "a.pack", [("A", 40.5, -99.5, 13.5)])
    facts = _facts(tmp_path, [dict(GOOD, value=6100)])         # the pack has 6052
    rec = _rep(pack, golden_facts=facts)
    assert _by_id(rec)["GLD-001"].get("pending_review") is None
    assert any(b.startswith("GLD-001 warns with no build note") for b in report.publish_blockers(rec))
    rec = _rep(pack, golden_facts=facts, notes={"GLD-001": "runway extended, YAML to follow"})
    assert not any(b.startswith("GLD-001") for b in report.publish_blockers(rec))
    ter = _rep(_terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _flat(500)}),
               origin="community")
    assert any(b.startswith("TER-007 warns with no build note") for b in report.publish_blockers(ter))


def test_without_terrain_the_cross_source_check_is_not_run_with_a_declared_reason(tmp_path):
    pack = _airports_pack(tmp_path / "a.pack", [("A", 40.5, -99.5, 3280)])
    c = _by_id(_rep(pack))["XS-001"]
    assert (c["status"], c["not_run_reason"]) == ("not-run", "no-terrain")
    assert "--terrain" in c["note"]


def test_obstacle_base_is_amsl_minus_agl(tmp_path):
    tree = _flat_tree(tmp_path)
    con = sqlite3.connect(tmp_path / "o.pack")
    con.execute("CREATE TABLE obstacles (oas_no TEXT PRIMARY KEY, lat REAL, lon REAL, "
                "amsl_ft REAL, agl_ft REAL)")
    ground = 1000 / FT
    con.executemany("INSERT INTO obstacles VALUES (?, 40.5, -99.5, ?, ?)",
                    [("01-1", ground + 300, 300), ("01-2", ground + 1000, 1000),
                     ("04-086611", 1675, 160)])        # base 1,515 ft on 3,281 ft terrain
    con.commit()
    con.close()
    packmeta.embed_sqlite(tmp_path / "o.pack", PackMeta(id="obstacles-conus", kind="obstacles",
                                                        cycle="261001", **LICENSE))
    rec = _rep(tmp_path / "o.pack", terrain=[tree])
    c = _by_id(rec)["XS-002"]
    assert c["status"] == "pass" and "1 beyond 500 ft (e.g. 04-086611 -1,766 ft)" in c["measured"]
    assert rec["metrics"]["XS-002"]["outliers"] == {"04-086611": -1766}


def test_terrain_spot_heights_take_field_elevations_from_an_airports_pack(tmp_path):
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _flat(1000)})
    apts = _airports_pack(tmp_path / "a.pack", [("IN", 40.5, -99.5, 1000 / FT + 5),
                                                ("OUT", 30.5, -99.5, 10)])
    c = _by_id(_rep(pack, origin="community", airports=apts))["TER-005"]
    assert c["status"] == "pass"
    assert c["measured"].startswith("1 of 1 airports compared")
    assert c["measured"].endswith("(field elevations from airports-conus 2612)")
    c = _by_id(_rep(pack, origin="community"))["TER-005"]
    assert (c["status"], c["not_run_reason"]) == ("not-run", "no-airports")


# --- GLD-001 --------------------------------------------------------------------

def test_the_packaged_golden_facts_load_and_each_cites_its_source():
    facts = golden.load()
    assert len(facts) >= 40
    assert {f.kind for f in facts} == {"navdata", "navaids"}
    for f in facts:
        assert f.source_url.startswith("https://aeronav.faa.gov/afd/"), f.id
        assert "Chart Supplement" in f.source


def _facts(tmp_path, items):
    p = tmp_path / "facts.yaml"
    p.write_text(json.dumps({"facts": items}))      # JSON is YAML
    return p


GOOD = {"id": "x", "kind": "navdata", "fact": "runway_length_ft",
        "key": {"airport": "A", "runway": "07/25"}, "value": 6052, "tolerance": 0,
        "source": "FAA Chart Supplement", "source_url": "https://example.org/x.pdf",
        "added": "2026-11-01"}


@pytest.mark.parametrize("change, message", [
    ({"fact": "runway_width_ft"}, "is not one of"),
    ({"kind": "navaids"}, "belongs to kind navdata"),
    ({"key": {"airport": "A"}}, "key needs exactly airport, runway"),
    ({"source_url": "http://example.org"}, "must be an https://"),
    ({"tolerance": -1}, "tolerance is negative"),
    ({"value": "long"}, "must be numbers"),
    ({"note": "x"}, "unknown field note"),
    ({"source": ""}, "missing source"),
])
def test_a_malformed_fact_is_refused_with_its_reason(tmp_path, change, message):
    with pytest.raises(golden.FactsError, match=message):
        golden.load(_facts(tmp_path, [dict(GOOD, **change)]))


def test_duplicate_fact_ids_are_refused(tmp_path):
    with pytest.raises(golden.FactsError, match="id used twice"):
        golden.load(_facts(tmp_path, [GOOD, GOOD]))


def test_golden_facts_match_differ_and_go_missing(tmp_path):
    pack = _airports_pack(tmp_path / "a.pack", [("A", 40.5, -99.5, 13.5), ("A2", 41, -99, 0)])
    facts = _facts(tmp_path, [
        GOOD,
        dict(GOOD, id="elev", fact="airport_elevation_ft", key={"airport": "A"}, value=14,
             tolerance=0.5),
        dict(GOOD, id="long", value=6100),
        dict(GOOD, id="gone", key={"airport": "ZZZ", "runway": "07/25"})])
    rec = _rep(pack, golden_facts=facts)
    c = _by_id(rec)["GLD-001"]
    assert c["status"] == "warn"
    assert c["measured"] == ("4 facts: 2 match, 1 differ, 1 not found, 0 ambiguous "
                             "(long: published 6100, pack 6052; gone: published 6052, pack -)")
    assert [r["outcome"] for r in rec["metrics"]["GLD-001"]] == \
        ["match", "match", "differs", "not found"]


def test_an_ambiguous_key_is_reported_not_guessed(tmp_path):
    con = sqlite3.connect(tmp_path / "n.pack")
    con.execute("CREATE TABLE navaids (id TEXT, type TEXT, name TEXT, freq TEXT, elev_ft REAL, "
                "lat REAL, lon REAL)")
    con.executemany("INSERT INTO navaids VALUES ('BOS', 'VOR/DME', 'x', ?, 0, 42, -71)",
                    [("112.7",), ("113",)])
    con.commit()
    rows = golden.check(con, golden.load(_facts(tmp_path, [dict(
        GOOD, kind="navaids", fact="navaid_frequency_mhz", key={"navaid": "BOS", "type": "VOR/DME"},
        value=112.7, tolerance=0.001)])))
    assert rows[0]["outcome"] == "ambiguous" and rows[0]["found"] == [112.7, 113.0]


# --- terrain: TER-007, TER-008 ------------------------------------------------------

def test_expected_tiles_come_from_the_copernicus_list():
    tiles = report.expected_tiles("us-central")
    assert len(tiles) == 311 and "N40W100" in tiles
    # Ocean cells have no Copernicus tile, so they are not expected.
    assert "N25W091" not in tiles


def test_a_missing_land_tile_is_listed(tmp_path):
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _flat(500)})
    rec = _rep(pack, origin="community")
    c = _by_id(rec)["TER-007"]
    assert c["status"] == "warn"
    assert c["measured"].startswith("1 of the 311 Copernicus tiles inside us-central present; "
                                    "310 missing: ")
    assert rec["metrics"]["TER-007"]["expected"] == 311


def test_a_tile_with_voids_warns(tmp_path):
    holes = _grid(11, lambda r, c: -32768 if r < 2 else 300)   # 22 of 121 posts void
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _flat(300),
                                               "N40/N40W099.hgt": holes})
    c = _by_id(_rep(pack, origin="community"))["TER-008"]
    assert c["status"] == "warn"
    assert c["measured"] == ("2 native tiles read; 1 with more than 1% void; worst N40W099 "
                             "18.18% (N40W099 18.2%)")


# --- admin1, OSM-002, OSM-006 ----------------------------------------------------------

@pytest.mark.parametrize("lat, lon, code", [
    (39.74, -104.99, "US-CO"), (43.65, -79.38, "CA-ON"), (34.05, -118.24, "US-CA"),
    (19.43, -99.13, None), (40.0, -40.0, "outside")])
def test_points_land_in_their_state_or_province(lat, lon, code):
    got = admin1.locate(lat, lon)
    if code is None:                                  # Mexico City: a Mexican code
        assert got.startswith("MX-")
    else:
        assert got == code


def test_a_bay_cell_takes_the_nearest_state():
    # Mid San Francisco Bay: water in Natural Earth, so no polygon holds it.
    assert admin1.locate(37.70, -122.25) == "outside"
    assert admin1.locate_near(37.70, -122.25) == "US-CA"


def test_binning_uses_the_nearest_state_for_bay_features(tmp_path):
    con = sqlite3.connect(tmp_path / "b.sqlite")
    con.execute("CREATE TABLE highway_lines (min_lat REAL, max_lat REAL, min_lon REAL, "
                "max_lon REAL, verts BLOB)")
    con.executemany("INSERT INTO highway_lines VALUES (?, ?, ?, ?, x'')",
                    [(37.65, 37.65, -122.32, -122.32), (40.0, 40.0, -40.0, -40.0)])
    got = admin1.per_state(con, "highway_lines", measure=False)
    assert got == {"US-CA": {"features": 1}, "outside": {"features": 1}}


def test_line_length_and_polygon_area():
    one_degree = array.array("f", [40.0, -100.0, 41.0, -100.0]).tobytes()
    assert admin1.vlen32(one_degree) == pytest.approx(111.195, rel=1e-4)
    sq = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    hole = [(0.25, 0.25), (0.25, 0.75), (0.75, 0.75), (0.75, 0.25)]
    blob = array.array("d", [v for p in sq + hole for v in p]).tobytes()
    full = admin1.varea64(array.array("d", [v for p in sq for v in p]).tobytes())
    assert full == pytest.approx(111.195 ** 2, rel=1e-3)
    rings = array.array("H", [4, 8]).tobytes()
    assert admin1.varea64(blob, rings) == pytest.approx(full * 0.75, rel=1e-3)
    # A truncated blob is measured on its whole pairs, never raised on.
    assert admin1.vlen32(one_degree + b"\x01") == admin1.vlen32(one_degree)


def _lines_pack(path, cycle, lines):
    """lines: [(lat, lon, length_deg_north)]: one highway_lines row each."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE highway_lines (id INTEGER PRIMARY KEY, fclass TEXT NOT NULL, "
                "min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL, verts BLOB NOT NULL, "
                "flags INTEGER NOT NULL DEFAULT 0, ref TEXT)")
    con.execute("CREATE VIRTUAL TABLE highway_rtree USING rtree(id, min_lat, max_lat, "
                "min_lon, max_lon)")
    for lat, lon, dl in lines:
        v = array.array("f", [lat, lon, lat + dl, lon])
        if sys.byteorder == "big":
            v.byteswap()
        con.execute("INSERT INTO highway_lines (fclass, min_lat, max_lat, min_lon, max_lon, verts) "
                    "VALUES ('primary', ?, ?, ?, ?, ?)", (lat, lat + dl, lon, lon, v.tobytes()))
    con.execute("INSERT INTO highway_rtree SELECT id, min_lat, max_lat, min_lon, max_lon "
                "FROM highway_lines")
    con.commit()
    con.close()
    meta = PackMeta(id="highways-conus", kind="highways", cycle=cycle,
                    attribution="OpenStreetMap contributors (ODbL)", license="ODbL-1.0")
    meta.schema_version = packmeta.detect_highways_schema_version(path)
    packmeta.embed_sqlite(path, meta)
    return Path(path)


CO = (39.5, -105.5)
WY = (43.0, -107.5)


def _roads(co, wy, dl=0.01):
    return [(*CO, dl)] * co + [(*WY, dl)] * wy


def test_a_state_going_to_zero_fails_and_others_are_counted(tmp_path):
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)))
    assert prev["metrics"]["OSM-002"]["highway_lines"] == {"US-CO": 150, "US-WY": 150}
    rec = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(0, 160)), previous=prev)
    c = _by_id(rec)
    assert c["OSM-002"]["status"] == "fail"
    assert c["OSM-002"]["measured"] == ("largest drop: highway_lines US-CO -100.0% (150 -> 0); "
                                        "1 states down more than 10%, 1 at zero")
    # OSM-001 bins by region; Wyoming growth hides most of Colorado's loss there.
    assert c["OSM-001"]["status"] != "fail"
    assert "OSM-002" not in " ".join(report.publish_blockers(rec))


def test_lengths_per_state_warn_on_a_drop_counts_cannot_see(tmp_path):
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150, 0.1)))
    km = prev["metrics"]["OSM-006"]["highway_lines"]
    assert km["US-CO"] == pytest.approx(150 * 11.12, rel=1e-3)
    # Same number of features, each half as long (a simplification bug).
    rec = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(150, 150, 0.05)), previous=prev)
    c = _by_id(rec)
    assert c["OSM-002"]["status"] == "pass"
    assert c["OSM-006"]["status"] == "warn"
    assert "-50.0%" in c["OSM-006"]["measured"] and "km" in c["OSM-006"]["measured"]


def _without_review(monkeypatch, *ids):
    """The catalogue as it will be once QA removes ``review`` from ``ids``."""
    import dataclasses
    real = report.all_checks
    monkeypatch.setattr(report, "all_checks", lambda: {
        k: [dataclasses.replace(c, review="") if c.id in ids else c for c in cs]
        for k, cs in real().items()})


def test_a_state_at_zero_refuses_the_publish_once_reviewed(tmp_path, monkeypatch):
    # AER-4584: a fail from a non-blocking check never blocked, so the #17
    # case (one state at zero) would have published. OSM-002/006 now block
    # like OSM-001; this is the California stand-in, with Colorado.
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150, 0.1)),
                                first_edition=True)
    rec = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(0, 300, 0.1)), previous=prev)
    c = _by_id(rec)
    assert c["OSM-001"]["status"] != "fail"                    # the region check misses it
    assert (c["OSM-002"]["status"], c["OSM-006"]["status"]) == ("fail", "fail")
    assert not any(b.startswith("OSM-00") for b in report.publish_blockers(rec))   # pending
    _without_review(monkeypatch, "OSM-002", "OSM-006")
    blockers = report.publish_blockers(rec)
    assert any(b.startswith("OSM-002 failed: largest drop: highway_lines US-CO -100.0%")
               for b in blockers)
    assert any(b.startswith("OSM-006 failed:") for b in blockers)


def test_a_previous_record_without_state_metrics_is_a_declared_exemption(tmp_path, monkeypatch):
    # Every live record predates catalogue v2. Without this exemption the
    # first gated build would be refused, and so would every one after it.
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)),
                                first_edition=True)
    for k in ("OSM-002", "OSM-006"):
        prev["metrics"].pop(k)
    rec = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(150, 150)), previous=prev)
    for cid in ("OSM-002", "OSM-006"):
        assert _by_id(rec)[cid]["not_run_reason"] == "no-previous-metric"
    _without_review(monkeypatch, "OSM-002", "OSM-006")
    assert not any(b.startswith("OSM-00") for b in report.publish_blockers(rec))
    # A missing --previous is still not a first edition.
    bare = _rep(_lines_pack(tmp_path / "c.pack", "r3", _roads(150, 150)))
    assert any(b.startswith("OSM-002 is blocking and did not run")
               for b in report.publish_blockers(bare))


def test_first_edition_exempts_the_state_checks(tmp_path):
    rec = _rep(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)), first_edition=True)
    for cid in ("OSM-002", "OSM-006"):
        assert _by_id(rec)[cid]["not_run_reason"] == "first-edition"


# --- diff map ------------------------------------------------------------------

def test_diff_map_colours_a_lost_state_and_labels_it(tmp_path):
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)))
    cur = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(0, 160)), previous=prev)
    html = diffmap.page(cur, prev)
    assert html.count("<figure") == 2                 # OSM-002 and OSM-006
    assert "<title>Colorado: -100% (150 -&gt; 0 features)</title>" in html
    assert 'fill="#b8302f"' in html                   # down 50% or more
    assert "OpenStreetMap contributors, ODbL" in html
    assert "<td>Colorado</td><td>150</td><td>0</td><td>-100%</td>" in html


def test_diff_map_needs_both_editions(tmp_path):
    rec = _rep(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)), first_edition=True)
    assert diffmap.page(rec, {"metrics": {}}) == ""


def test_the_report_page_draws_the_diff_map_and_passes_the_lint(tmp_path):
    from packtools import reportsite as rs
    from packtools.upload import LocalStore
    prev = report.previous_from(_lines_pack(tmp_path / "a.pack", "r1", _roads(150, 150)),
                                first_edition=True)
    cur = _rep(_lines_pack(tmp_path / "b.pack", "r2", _roads(0, 160)), previous=prev)
    store = LocalStore(tmp_path / "bucket")
    for rec in (prev, cur):
        store.put_bytes(rs.record_key(rec["pack"]["id"], rec["pack"]["cycle"]),
                        report.to_bytes(rec))
    site = rs.build(rs.load_records(store), None, commit="a" * 40)   # raises on a lint failure
    page = site[f"{rs.PREFIX}highways-conus/r2/report.html"].decode()
    assert "<h2>Changes by state or province</h2>" in page
    assert "<title>Colorado: -100% (150 -&gt; 0 features)</title>" in page
    # The first edition has nothing to compare against, so no map.
    assert "Changes by state" not in site[f"{rs.PREFIX}highways-conus/r1/report.html"].decode()
