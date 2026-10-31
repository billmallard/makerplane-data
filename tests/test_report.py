# SPDX-License-Identifier: Apache-2.0
"""pack-report/1: ``packtool report`` and ``--check`` (AER-4100, TV2).

Synthetic packs via tmp_path, no network and no pyEfis. The device
acceptance check is exercised through a stand-in ``pyefis`` package on
PYTHONPATH, which proves the subprocess plumbing; the real readers are
exercised against real packs by hand (see the PR).
"""

import json
import sqlite3
import struct
import zipfile
from pathlib import Path

import pytest

from packtools import cli, packmeta, report
from packtools.build.procedures import _SCHEMA as PROC_SCHEMA
from packtools.manifest import NOTICE
from packtools.packmeta import PackMeta

LICENSE = dict(attribution="FAA CIFP (public domain)", license="LicenseRef-us-public-domain")


# --- fixtures -----------------------------------------------------------------

def _proc_pack(path, cycle="2611", *, airways=40, collide=0, unresolved=1, legs=400,
               jet=False, mora_bands=report.PROC_MORA_BAND_FLOOR, mora_unk=0,
               mora_ft=3000, mora_cells=30):
    """A procedures pack: ``airways`` airways of 5 legs, ``collide`` of
    them carrying a second area's legs with the same sequence numbers (the
    AER-1979 merge), and ``legs`` procedure legs of which ``unresolved``
    name a fix with no coordinates. ``jet`` names the last airway J80.
    ``mora_bands`` Grid MORA bands of ``mora_cells`` cells each, all
    ``mora_ft`` except the first ``mora_unk`` cells, which are UNK."""
    con = sqlite3.connect(path)
    con.executescript(PROC_SCHEMA)
    for a in range(1, airways + 1):
        ident = "J80" if jet and a == airways else f"V{a}"
        con.execute("INSERT INTO airways (id, ident, cycle) VALUES (?, ?, ?)", (a, ident, cycle))
        for seq in range(10, 60, 10):
            con.execute("INSERT INTO airway_legs (airway_id, seq, fix_id, fix_lat, fix_lon) "
                        "VALUES (?, ?, ?, ?, ?)", (a, seq, f"F{a}{seq}", 40.0, -100.0))
            if a <= collide:
                con.execute("INSERT INTO airway_legs (airway_id, seq, fix_id, fix_lat, fix_lon) "
                            "VALUES (?, ?, ?, ?, ?)", (a, seq, f"C{a}{seq}", 45.0, -75.0))
    con.execute("INSERT INTO procedures (id, airport, kind, ident, cycle) "
                "VALUES (1, 'KDEN', 'sid', 'BAYLR6', ?)", (cycle,))
    con.execute("INSERT INTO transitions (id, proc_id, role) VALUES (1, 1, 'common')")
    for i in range(legs):
        lat = None if i < unresolved else 39.8
        con.execute("INSERT INTO legs (transition_id, seq, path_term, fix_id, fix_lat, "
                    "fix_lon, fix_type, flags) VALUES (1, ?, 'TF', ?, ?, ?, 'airport', 0)",
                    (i, f"FX{i}", lat, None if lat is None else -104.7))
    n = 0
    for b in range(1, mora_bands + 1):
        lat_sw, lon_sw = b % 60, -180 + 30 * (b // 60)
        con.execute("INSERT INTO grid_mora_bands (id, lat_sw, lon_sw, raw) VALUES (?, ?, ?, '')",
                    (b, lat_sw, lon_sw))
        for i in range(mora_cells):
            unk = n < mora_unk
            n += 1
            con.execute("INSERT INTO grid_mora (band_id, lat_sw, lon_sw, mora_ft, raw) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (b, lat_sw, lon_sw + i, None if unk else mora_ft,
                         "UNK" if unk else f"{mora_ft // 100:03d}"))
    con.commit()
    con.close()
    packmeta.embed_sqlite(path, PackMeta(id="procedures-conus", kind="procedures",
                                         cycle=cycle, **LICENSE))
    return Path(path)


def _hwy_pack(path, cycle, per_region):
    """A highways pack (schema v2 columns) with ``per_region`` rows centred
    in each of {label: (lat, lon)}, indexed in ``highway_rtree`` as the
    pyEfis builder does."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE highway_lines (id INTEGER PRIMARY KEY, fclass TEXT NOT NULL, "
                "min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL, verts BLOB NOT NULL, "
                "flags INTEGER NOT NULL DEFAULT 0, ref TEXT)")
    con.execute("CREATE VIRTUAL TABLE highway_rtree USING rtree(id, min_lat, max_lat, "
                "min_lon, max_lon)")
    for (lat, lon), n in per_region.items():
        con.executemany("INSERT INTO highway_lines (fclass, min_lat, max_lat, min_lon, max_lon, "
                        "verts) VALUES ('primary', ?, ?, ?, ?, x'00')",
                        [(lat, lat + 0.01, lon, lon + 0.01)] * n)
    con.execute("INSERT INTO highway_rtree SELECT id, min_lat, max_lat, min_lon, max_lon "
                "FROM highway_lines")
    con.commit()
    con.close()
    meta = PackMeta(id="highways-conus", kind="highways", cycle=cycle,
                    attribution="OpenStreetMap contributors (ODbL)", license="ODbL-1.0")
    meta.schema_version = packmeta.detect_highways_schema_version(path)
    packmeta.embed_sqlite(path, meta)
    return Path(path)


CA = (36.0, -120.0)     # us-west
CO = (39.7, -105.0)     # us-west and us-central overlap edge -> us-west only (lon < -104)
TX = (31.0, -97.0)      # us-central, us-south


def _hgt(side, value=100):
    return struct.pack(f">{side * side}h", *([value] * side * side))


def _terrain_pack(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    packmeta.embed_zip(path, PackMeta(id="terrain-us-central", kind="terrain",
                                      cycle="2024edr1", attribution="Copernicus GLO-30",
                                      license="LicenseRef-copernicus"))
    return Path(path)


def _by_id(rec):
    return {c["id"]: c for c in rec["checks"]}


def _rep(path, **kw):
    kw.setdefault("probe", False)
    kw.setdefault("commit", "0" * 40)
    return report.build_report(path, **kw)


# --- the catalogue ------------------------------------------------------------

def test_every_check_states_what_it_cannot_catch():
    for kind, checks in report.all_checks().items():
        ids = [c.id for c in checks]
        assert len(ids) == len(set(ids)), kind
        assert "ACC-001" in ids, f"{kind} has no device acceptance check"
        for c in checks:
            assert c.cannot_catch.strip() and c.expected.strip(), (kind, c.id)


def test_catalogue_words_follow_the_standing_text():
    # Plan section 4.7: never these words about a pack or a check result.
    banned = ("verified", "certified", "approved", "safe", "compliant", "airworthy")
    for checks in report.all_checks().values():
        for c in checks:
            text = " ".join((c.title, c.expected, c.cannot_catch)).lower()
            assert not any(w in text.split() for w in banned), c.id


# --- procedures ---------------------------------------------------------------

def test_procedures_record_is_well_formed(tmp_path):
    rec = _rep(_proc_pack(tmp_path / "p.pack"))
    assert report.validate_report(rec) == []
    assert rec["schema"] == "pack-report/1" and rec["notice"] == NOTICE
    assert rec["origin"] == "federal" and rec["previous"] is None
    assert rec["pack"]["kind"] == "procedures" and rec["pack"]["schema_version"] == "1"
    assert rec["tables"]["airways"] == {"rows": 40, "prev": None}
    assert "pack_meta" not in rec["tables"]


def test_unresolved_fix_legs_are_counted_with_their_expectation(tmp_path):
    c = _by_id(_rep(_proc_pack(tmp_path / "a.pack", unresolved=2, legs=1000)))["PROC-FIX-001"]
    assert (c["status"], c["measured"]) == ("pass", "2 of 1,000 legs (0.20%)")
    assert "regression bound" in c["expected"] and "950 of 201,030" in c["expected"]
    assert "does not accept" in c["expected"]
    c = _by_id(_rep(_proc_pack(tmp_path / "b.pack", unresolved=6, legs=1000)))["PROC-FIX-001"]
    assert c["status"] == "warn" and not c["blocking"]


def test_airway_area_collision_fails_as_the_aer1979_regression(tmp_path):
    good = _by_id(_rep(_proc_pack(tmp_path / "good.pack")))
    assert good["PROC-AWY-002"]["status"] == "pass"
    bad = _by_id(_rep(_proc_pack(tmp_path / "bad.pack", collide=3)))
    c = bad["PROC-AWY-002"]
    assert (c["status"], c["measured"], c["blocking"], c["since"]) == ("fail", 3, True, "AER-1979")
    # Idents stay unique in a merged pack: that is why AWY-002 exists.
    assert bad["PROC-AWY-001"]["status"] == "pass"


def test_foreflight_differential_is_listed_as_not_run(tmp_path):
    c = _by_id(_rep(_proc_pack(tmp_path / "p.pack")))["PROC-DIFF-001"]
    assert c["status"] == "not-run" and c["measured"] is None and "AER-1702" in c["note"]


def test_row_delta_warns_and_a_build_note_clears_the_publish_block(tmp_path):
    prev = report.previous_from(_proc_pack(tmp_path / "2610.pack", "2610", airways=50))
    pack = _proc_pack(tmp_path / "2611.pack", airways=40)
    rec = _rep(pack, previous=prev)
    c = _by_id(rec)["ROW-002"]
    assert c["status"] == "warn" and c["measured"] == "largest change: airway_legs -20.0% (250 -> 200)"
    assert rec["previous"] == {"cycle": "2610", "sha256": prev["pack"]["sha256"]}
    assert rec["tables"]["airways"] == {"rows": 40, "prev": 50}
    assert any(b.startswith("ROW-002 warns with no build note") for b in report.publish_blockers(rec))
    noted = _rep(pack, previous=prev, notes={"ROW-002": "FAA withdrew 10 airways"})
    assert _by_id(noted)["ROW-002"]["note"] == "FAA withdrew 10 airways"
    # The note clears ROW-002; only the switched-off device check is left.
    assert _blocking_ids(noted) == ["ACC-001"]


def test_a_table_that_disappears_fails_unless_the_schema_version_moved(tmp_path):
    # C4 (AER-4102): a vanished table used to be a -100% ROW-002 warn that
    # a build note cleared.
    prev_pack = _proc_pack(tmp_path / "2610.pack", "2610")
    _sql(prev_pack, "CREATE TABLE holds (id INTEGER)", "INSERT INTO holds VALUES (1)")
    prev = report.previous_from(prev_pack)
    pack = _proc_pack(tmp_path / "2611.pack")
    rec = _rep(pack, previous=prev, notes={"ROW-002": "holds table dropped"})
    c = _by_id(rec)["ROW-003"]
    assert (c["status"], c["blocking"]) == ("fail", True)
    assert c["measured"] == "gone: holds (schema_version unchanged at 1)"
    assert "ROW-003" in _blocking_ids(rec)
    # A schema_version change is the declared way to drop a table.
    packmeta.embed_sqlite(pack, PackMeta(id="procedures-conus", kind="procedures", cycle="2611",
                                         schema_version=2, **LICENSE))
    c = _by_id(_rep(pack, previous=prev))["ROW-003"]
    assert c["status"] == "pass" and c["measured"] == "gone: holds; schema_version 1 -> 2"
    kept = _by_id(_rep(_proc_pack(tmp_path / "x.pack"), previous=prev))
    assert kept["ROW-003"]["status"] == "fail"
    same = _by_id(_rep(_proc_pack(tmp_path / "y.pack"),
                       previous=report.previous_from(_proc_pack(tmp_path / "z.pack", "2610"))))
    assert (same["ROW-003"]["status"], same["ROW-003"]["measured"]) == ("pass", "0 of 7 tables gone")


def test_unmeasured_thresholds_are_labelled_provisional_in_the_record(tmp_path):
    # C7 (AER-4102): ROW_DELTA_WARN, the GEO-002 allowance and
    # OSM_REGION_DROP_WARN have no edition-to-edition baseline yet.
    proc = _by_id(_rep(_proc_pack(tmp_path / "p.pack")))
    hwy = _by_id(_rep(_hwy_pack(tmp_path / "h.pack", "r1", {CA: 5})))
    for c in (proc["ROW-002"], proc["GEO-002"], hwy["OSM-001"]):
        assert "no edition-to-edition baseline" in c["provisional"], c["id"]
        assert c["expected"].startswith("Provisional threshold:"), c["id"]
    assert "provisional" not in proc["PROC-AWY-002"] and "provisional" not in proc["GEO-001"]


def test_previous_report_file_works_like_the_previous_pack(tmp_path):
    prev_pack = _proc_pack(tmp_path / "2610.pack", "2610", airways=50)
    prev_json = tmp_path / "2610.json"
    prev_json.write_bytes(report.to_bytes(report.previous_from(prev_pack)))
    pack = _proc_pack(tmp_path / "2611.pack")
    a = _rep(pack, previous=report.previous_from(prev_pack))
    b = _rep(pack, previous=report.previous_from(prev_json))
    assert a["checks"] == b["checks"] and a["tables"] == b["tables"]


def test_a_crashing_check_is_a_failed_check_not_a_missing_one(tmp_path, monkeypatch):
    def boom(ctx):
        raise RuntimeError("nope")
    monkeypatch.setattr(report, "_c_unresolved", boom)
    checks = [c if c.id != "PROC-FIX-001" else
              report.Check(c.id, c.title, c.blocking, c.expected, c.cannot_catch, boom)
              for c in report._KIND_CHECKS["procedures"]]
    monkeypatch.setitem(report._KIND_CHECKS, "procedures", checks)
    rec = _rep(_proc_pack(tmp_path / "p.pack"))
    c = _by_id(rec)["PROC-FIX-001"]
    assert c["status"] == "fail" and "RuntimeError: nope" in c["measured"]
    # C2 (AER-4102): a check that crashed measured nothing, so it blocks
    # even though PROC-FIX-001 itself is non-blocking.
    assert not c["blocking"] and c["raised"] is True
    assert any(b.startswith("PROC-FIX-001 raised") for b in report.publish_blockers(rec))


def _blocking_ids(rec):
    return sorted({b.split()[0] for b in report.publish_blockers(rec)})


def test_a_blocking_check_that_did_not_run_blocks_a_build(tmp_path):
    # C1 (AER-4102): device acceptance switched off, or no pyEfis checkout,
    # used to pass the gate silently. In a build record it blocks.
    pack = _proc_pack(tmp_path / "p.pack")
    rec = _rep(pack)
    c = _by_id(rec)["ACC-001"]
    assert c["status"] == "not-run" and c["blocking"]
    # No --previous and no --first-edition: the blocking delta check blocks too.
    assert _blocking_ids(rec) == ["ACC-001", "ROW-003"]
    # Declaring a first edition exempts the delta check, not the device check.
    assert _blocking_ids(_rep(pack, first_edition=True)) == ["ACC-001"]
    # A retro record describes old bytes; it can never clear the gate (R1).
    # The C1 rule no longer depends on the record's own type field.
    assert _blocking_ids(_rep(pack, record="retro")) == ["ACC-001", "ROW-003", "record"]


def test_only_a_first_edition_declared_by_the_caller_exempts_a_delta_check(tmp_path):
    pack = _hwy_pack(tmp_path / "h.pack", "r1", {CA: 200, TX: 200})
    missing = _by_id(_rep(pack))["OSM-001"]
    assert missing["status"] == "not-run" and missing["not_run_reason"] == "no-previous"
    assert "OSM-001" in _blocking_ids(_rep(pack))
    first = _rep(pack, first_edition=True)
    c = _by_id(first)["OSM-001"]
    assert c["not_run_reason"] == "first-edition" and "first edition" in c["note"].lower()
    assert "OSM-001" not in _blocking_ids(first)
    with pytest.raises(report.ReportError):
        _rep(pack, first_edition=True, previous=report.previous_from(pack))


def test_the_gate_takes_blocking_and_the_check_set_from_the_catalogue(tmp_path):
    # R1 (AER-4102 re-verdict): QA's tampering probe. Flipping blocking,
    # deleting checks or relabelling the record left 0 blockers.
    prev = report.previous_from(_hwy_pack(tmp_path / "r1.pack", "r1", {CA: 200, TX: 200}))
    pack = _hwy_pack(tmp_path / "r2.pack", "r2", {CA: 200, TX: 200})
    _sql(pack, "DELETE FROM highway_rtree")
    rec = _rep(pack, previous=prev)
    assert _blocking_ids(rec) == ["ACC-001", "IDX-001"]

    flipped = json.loads(json.dumps(rec))
    for c in flipped["checks"]:
        c["blocking"] = False
    assert _blocking_ids(flipped) == ["ACC-001", "IDX-001"]

    stripped = json.loads(json.dumps(rec))
    stripped["checks"] = [c for c in stripped["checks"] if c["id"] not in ("ACC-001", "IDX-001")]
    blockers = report.publish_blockers(stripped)
    assert "ACC-001 is in the highways catalogue but missing from the record" in blockers
    assert "IDX-001 is in the highways catalogue but missing from the record" in blockers

    retro = json.loads(json.dumps(rec))
    retro["record"] = "retro"
    assert any(b.startswith("record is retro") for b in report.publish_blockers(retro))

    unknown = json.loads(json.dumps(rec))
    unknown["pack"]["kind"] = "nope"
    assert any(b.startswith("kind 'nope'") for b in report.publish_blockers(unknown))


def test_a_record_cannot_exempt_itself(tmp_path):
    # The exemption comes from the catalogue entry, not from the record.
    rec = _rep(_proc_pack(tmp_path / "p.pack"))
    acc = _by_id(rec)["ACC-001"]
    acc["not_run_reason"] = "first-edition"
    assert "ACC-001" in _blocking_ids(rec)


# --- federal sqlite -----------------------------------------------------------

def _airports_pack(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE airports (site_no TEXT PRIMARY KEY, icao TEXT NOT NULL, "
                "name TEXT, lat REAL NOT NULL, lon REAL NOT NULL, elev_ft REAL)")
    con.executemany("INSERT INTO airports VALUES (?, ?, 'x', ?, ?, 0)", rows)
    con.execute("CREATE TABLE runways (site_no TEXT, rwy_id TEXT, length_ft REAL, width_ft REAL, "
                "surface TEXT, lighting TEXT)")
    con.execute("INSERT INTO runways VALUES ('1', '07/25', 5000, 100, 'ASPH', '')")
    con.commit()
    con.close()
    packmeta.embed_sqlite(path, PackMeta(id="airports-conus", kind="navdata", cycle="2611",
                                         attribution="FAA NASR (public domain)",
                                         license="LicenseRef-us-public-domain"))
    return Path(path)


def test_federal_coordinate_and_uniqueness_checks(tmp_path):
    pack = _airports_pack(tmp_path / "a.pack", [
        ("1", "DEN", 39.86, -104.67), ("2", "DEN", 0.0, 0.0), ("3", "XYZ", 95.0, -100.0),
        ("4", "DGA", -7.31, 72.41)])
    c = _by_id(_rep(pack))
    assert (c["GEO-001"]["status"], c["GEO-001"]["measured"]) == ("fail", 2)
    assert c["UNQ-001"]["status"] == "fail"
    assert "airports(icao) 1" in c["UNQ-001"]["measured"]
    # Outside the US envelope (Diego Garcia, 0,0, the bad latitude) is
    # tracked, not failed; with no previous edition it records the count.
    assert c["GEO-002"]["status"] == "not-run" and "3 such points" in c["GEO-002"]["note"]


def test_schema_guard_layers_are_persisted_not_printed(tmp_path):
    rec = _rep(_airports_pack(tmp_path / "a.pack", [("1", "DEN", 39.86, -104.67)]))
    c = _by_id(rec)
    for layer in ("SG-001", "SG-002", "SG-003"):
        assert c[layer]["status"] == "not-run" and "--inputs" in c[layer]["note"]
    # Layer 4 runs on the pack: one airport is far below the real floor.
    assert c["SG-004"]["status"] == "fail"
    assert "airports 1 (floor 9,700)" in c["SG-004"]["measured"]


def _obs_pack(path, rows=1, nulls=0):
    """An obstacles pack: ``rows`` obstacles, the first ``nulls`` with no
    latitude."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE obstacles (oas_no TEXT PRIMARY KEY, lat REAL, lon REAL, "
                "amsl_ft REAL, agl_ft REAL)")
    con.executemany("INSERT INTO obstacles VALUES (?, ?, -100, 1000, 300)",
                    [(str(i), None if i < nulls else 40.0) for i in range(rows)])
    con.commit()
    con.close()
    packmeta.embed_sqlite(path, PackMeta(id="obstacles-conus", kind="obstacles",
                                         cycle="261001", **LICENSE))
    return Path(path)


def test_null_coordinates_are_bad_on_point_tables(tmp_path):
    # C5 (AER-4102): a row with a NULL latitude was skipped, so an
    # obstacle with no position passed GEO-001.
    rec = _rep(_obs_pack(tmp_path / "o.pack", rows=5, nulls=2))
    c = _by_id(rec)["GEO-001"]
    assert (c["status"], c["measured"]) == ("fail", 2)
    assert "GEO-001" in _blocking_ids(rec)
    # Procedure legs keep the tolerance: PROC-FIX-001 owns unplaced fixes.
    legs = _by_id(_rep(_proc_pack(tmp_path / "p.pack", unresolved=3)))
    assert (legs["GEO-001"]["status"], legs["GEO-001"]["measured"]) == ("pass", 0)


def test_obstacles_have_a_blocking_row_floor_and_no_guard_checks(tmp_path, monkeypatch):
    # AER-4102: SG-001..004 were blocking but could never run for
    # obstacles (no contract), and nothing short of an empty table blocked.
    rec = _rep(_obs_pack(tmp_path / "o.pack", rows=3))
    c = _by_id(rec)
    assert not any(k.startswith("SG-") for k in c)
    assert (c["OBS-001"]["status"], c["OBS-001"]["blocking"]) == ("fail", True)
    assert c["OBS-001"]["measured"] == "obstacles 3 (floor 328,500)"
    assert "OBS-001" in _blocking_ids(rec)
    monkeypatch.setitem(report._ROW_FLOORS["obstacles"], "obstacles", 3)
    c = _by_id(_rep(tmp_path / "o.pack"))["OBS-001"]
    assert (c["status"], c["measured"]) == ("pass", "obstacles 3 (floor 3)")


def test_missing_license_fails_the_metadata_check(tmp_path):
    pack = _proc_pack(tmp_path / "p.pack")
    packmeta.embed_sqlite(pack, PackMeta(id="procedures-conus", kind="procedures", cycle="2611"))
    c = _by_id(_rep(pack))["PACK-002"]
    assert (c["status"], c["measured"]) == ("fail", "missing: attribution, license")


# --- OSM ----------------------------------------------------------------------

def test_a_region_going_to_zero_fails_like_the_june_highways_pack(tmp_path):
    prev = report.previous_from(_hwy_pack(tmp_path / "r1.pack", "r1", {CA: 200, TX: 200}))
    rec = _rep(_hwy_pack(tmp_path / "r2.pack", "r2", {TX: 210}), previous=prev)
    c = _by_id(rec)["OSM-001"]
    assert c["status"] == "fail" and c["blocking"]
    assert "us-west -100.0% (200 -> 0)" in c["measured"]
    assert rec["metrics"]["OSM-001"]["highway_lines"]["us-west"] == 0


def test_osm_region_counts_and_schema_agreement_pass_on_a_steady_edition(tmp_path):
    prev = report.previous_from(_hwy_pack(tmp_path / "r1.pack", "r1", {CA: 200, TX: 200}))
    c = _by_id(_rep(_hwy_pack(tmp_path / "r2.pack", "r2", {CA: 195, TX: 205}), previous=prev))
    assert c["OSM-001"]["status"] == "pass"
    assert c["OSM-003"]["measured"] == "declared 2, columns say 2"
    assert c["OSM-002"]["status"] == "not-run" and "TV7" in c["OSM-002"]["note"]


def test_osm_region_wording_when_every_region_grew(tmp_path):
    # AER-4102: measured said "largest drop" on highways 2026q3r1, where
    # every region grew.
    prev = report.previous_from(_hwy_pack(tmp_path / "r1.pack", "r1", {CA: 200, TX: 200}))
    c = _by_id(_rep(_hwy_pack(tmp_path / "r2.pack", "r2", {CA: 250, TX: 210}),
                    previous=prev))["OSM-001"]
    assert c["status"] == "pass" and "drop" not in c["measured"].replace("no region dropped", "")
    assert c["measured"].startswith("no region dropped; smallest growth: highway_lines ")
    assert "+5.0% (200 -> 210)" in c["measured"]


def test_kinds_without_an_accepted_catalogue_are_declared_ungated(tmp_path):
    # AER-4102: rivers, cifp, airspace and plates have no content check
    # beyond the common ones and no device probe. A record may be emitted,
    # but it must not count as a gate.
    assert set(report.UNGATED) == {"rivers", "cifp", "airspace", "plates"}
    pack = _hwy_pack(tmp_path / "r.pack", "r1", {CA: 5})
    packmeta.embed_sqlite(pack, PackMeta(id="rivers-conus", kind="rivers", cycle="r1",
                                         attribution="OSM", license="ODbL-1.0"))
    rec = _rep(pack, first_edition=True)
    assert rec["gated"] is False and "no device probe" in rec["ungated"]
    assert any(b.startswith("ungated: kind rivers") for b in report.publish_blockers(rec))
    assert report.validate_report(rec) == []
    gated = _rep(_proc_pack(tmp_path / "p.pack"))
    assert gated["gated"] is True and "ungated" not in gated


def test_highways_schema_version_drift_fails(tmp_path):
    pack = _hwy_pack(tmp_path / "h.pack", "r1", {CA: 5})
    packmeta.embed_sqlite(pack, PackMeta(id="highways-conus", kind="highways", cycle="r1",
                                         attribution="OSM", license="ODbL-1.0"))  # declares 1
    c = _by_id(_rep(pack))["OSM-003"]
    assert c["status"] == "fail" and c["since"] == "AER-1715"


def _sql(path, *stmts):
    con = sqlite3.connect(path)
    for st in stmts:
        con.execute(st)
    con.commit()
    con.close()


def test_an_emptied_spatial_index_blocks_the_publish(tmp_path):
    # C3 (AER-4102): QA's mutation. DELETE FROM highway_rtree left every
    # open check passing (quick_check, ROW-001, ROW-002, OSM-001, OSM-004,
    # OSM-003), and rtreecheck() says ok on an empty R-tree.
    prev = report.previous_from(_hwy_pack(tmp_path / "r1.pack", "r1", {CA: 200, TX: 200}))
    pack = _hwy_pack(tmp_path / "r2.pack", "r2", {CA: 200, TX: 200})
    good = _by_id(_rep(pack, previous=prev))["IDX-001"]
    assert (good["status"], good["blocking"]) == ("pass", True)
    assert good["measured"] == "highway_rtree 400 = highway_lines 400, rtreecheck ok"
    _sql(pack, "DELETE FROM highway_rtree")
    rec = _rep(pack, previous=prev)
    c = _by_id(rec)
    assert c["SQL-001"]["status"] == "pass" and c["OSM-001"]["status"] == "pass"
    assert c["IDX-001"]["status"] == "fail"
    assert "highway_rtree 0 != highway_lines 400" in c["IDX-001"]["measured"]
    assert "IDX-001" in _blocking_ids(rec)


def test_a_damaged_spatial_index_fails_rtreecheck(tmp_path):
    pack = _hwy_pack(tmp_path / "h.pack", "r1", {CA: 50})
    _sql(pack, "DELETE FROM highway_rtree_rowid WHERE rowid = 7")   # count stays 50
    c = _by_id(_rep(pack))["IDX-001"]
    assert c["status"] == "fail" and "rtreecheck" in c["measured"]
    assert "highway_rtree 50 = highway_lines 50" in c["measured"]


def test_a_missing_declared_spatial_index_fails(tmp_path):
    pack = _hwy_pack(tmp_path / "h.pack", "r1", {CA: 5})
    _sql(pack, "DROP TABLE highway_rtree")
    c = _by_id(_rep(pack))["IDX-001"]
    assert c["status"] == "fail" and "highway_rtree missing" in c["measured"]


def _water_pack(path, polygons=3, waterways=0):
    """A water pack shaped like the live water-na-2026q2r6: polygons, plus
    the waterway tables the builder creates empty without --waterway."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE water_polygons (id INTEGER PRIMARY KEY, min_lat REAL, "
                "max_lat REAL, min_lon REAL, max_lon REAL, rings BLOB)")
    con.execute("CREATE TABLE waterway_lines (id INTEGER PRIMARY KEY, min_lat REAL, "
                "max_lat REAL, min_lon REAL, max_lon REAL, verts BLOB)")
    for t in ("water", "waterway"):
        con.execute(f"CREATE VIRTUAL TABLE {t}_rtree USING rtree(id, min_lat, max_lat, "
                    "min_lon, max_lon)")
    for table, rt, n in (("water_polygons", "water_rtree", polygons),
                         ("waterway_lines", "waterway_rtree", waterways)):
        for i in range(1, n + 1):
            con.execute(f"INSERT INTO {table} VALUES (?, 47.6, 47.7, -122.4, -122.3, x'00')", (i,))
            con.execute(f"INSERT INTO {rt} VALUES (?, 47.6, 47.7, -122.4, -122.3)", (i,))
    con.commit()
    con.close()
    packmeta.embed_sqlite(path, PackMeta(id="water-na", kind="water", cycle="2026q2r6",
                                         attribution="OSM", license="ODbL-1.0"))
    return Path(path)


def test_water_declares_its_empty_waterway_table(tmp_path):
    # Owner answer on AER-4137: waterway_lines is created empty in every
    # water build without --waterway (river lines are the rivers kind).
    # The catalogue declares that one table for that one kind.
    rec = _rep(_water_pack(tmp_path / "w.pack"))
    c = _by_id(rec)
    assert c["ROW-001"]["status"] == "pass"
    assert c["ROW-001"]["measured"] == "0 empty tables; empty by declaration: waterway_lines"
    assert "waterway_lines" in c["ROW-001"]["expected"]
    assert c["IDX-001"]["status"] == "pass"
    # Every other table still has to hold rows.
    c = _by_id(_rep(_water_pack(tmp_path / "e.pack", polygons=0)))["ROW-001"]
    assert c["status"] == "fail" and c["measured"].startswith("empty: water_polygons")
    # And the declaration is the water kind's only.
    hwy = _hwy_pack(tmp_path / "h.pack", "r1", {CA: 5})
    _sql(hwy, "CREATE TABLE waterway_lines (id INTEGER)")
    assert _by_id(_rep(hwy))["ROW-001"]["status"] == "fail"


# --- terrain ------------------------------------------------------------------

def test_terrain_checks_on_a_region_pack(tmp_path):
    pack = _terrain_pack(tmp_path / "t.pack", {
        "N40/N40W100.hgt": _hgt(3, 1500),
        ".mip/1/N40/N40W100.hgt": _hgt(2, 1500),
        ".mip/1/N40/N40W100.wmask": b"\x00\x00",
        "N40/N40W120.hgt": _hgt(3, 9500),          # outside us-central, and too high
    })
    c = _by_id(_rep(pack, origin="federal"))
    assert c["TER-001"]["status"] == "pass"
    assert c["TER-002"]["status"] == "fail" and "1 outside us-central" in c["TER-002"]["measured"]
    assert c["TER-003"]["status"] == "pass"
    assert c["TER-004"]["status"] == "warn" and "max 9,500 m" in c["TER-004"]["measured"]
    assert c["TER-005"]["status"] == "not-run"


def test_terrain_missing_water_mask_and_bad_member(tmp_path):
    pack = _terrain_pack(tmp_path / "t.pack", {
        "N40/N40W100.hgt": _hgt(3),
        ".mip/1/N40/N40W100.hgt": _hgt(2),         # no .wmask sibling
        "N40/N40W099.hgt": b"\x00" * 7,             # not a square int16 grid
    })
    c = _by_id(_rep(pack, origin="federal"))
    assert (c["TER-003"]["status"], c["TER-003"]["measured"]) == ("fail", 1)
    assert c["TER-001"]["status"] == "fail" and "N40W099.hgt" in c["TER-001"]["measured"]


def test_terrain_losing_a_native_tile_fails(tmp_path):
    # AER-4102: TER-002 showed the previous tile count but never compared,
    # so a pack that dropped half its tiles passed every check.
    tiles = {"N40/N40W100.hgt": _hgt(3), "N40/N40W099.hgt": _hgt(3)}
    prev = report.previous_from(_terrain_pack(tmp_path / "a.pack", tiles), origin="federal")
    assert prev["metrics"]["TER-006"] == ["N40W099", "N40W100"]
    same = _by_id(_rep(_terrain_pack(tmp_path / "b.pack", dict(tiles)), origin="federal",
                       previous=prev))["TER-006"]
    assert (same["status"], same["blocking"]) == ("pass", True)
    lost = _rep(_terrain_pack(tmp_path / "c.pack", {"N40/N40W100.hgt": _hgt(3)}),
                origin="federal", previous=prev)
    c = _by_id(lost)["TER-006"]
    assert c["status"] == "fail" and "lost 1: N40W099" in c["measured"]
    assert "TER-006" in _blocking_ids(lost)
    # Only a declared first edition exempts it.
    first = _rep(_terrain_pack(tmp_path / "d.pack", tiles), origin="federal", first_edition=True)
    assert "TER-006" not in _blocking_ids(first)


def test_only_the_mosaic_pack_id_is_exempt_from_tile_placement(tmp_path):
    # R2 (AER-4102 re-verdict): a regional pack that lost every native tile
    # exempted itself as "a mosaic pack", and as a first edition nothing
    # else caught it.
    stripped = _terrain_pack(tmp_path / "s.pack", {".mip/mosaic/L0.hgt": _hgt(2),
                                                   ".mip/mosaic/L0.json": b'{"rows": 2, "cols": 2}'})
    rec = _rep(stripped, origin="federal", first_edition=True)
    c = _by_id(rec)["TER-002"]
    assert (c["status"], c["blocking"]) == ("fail", True)
    assert c["measured"] == "0 native tiles in terrain-us-central, which is not the mosaic pack"
    assert "TER-002" in _blocking_ids(rec)
    packmeta.embed_zip(stripped, PackMeta(id="terrain-mosaic", kind="terrain", cycle="2024edr1",
                                          attribution="Copernicus GLO-30",
                                          license="LicenseRef-copernicus"))
    c = _by_id(_rep(stripped, origin="federal", first_edition=True))["TER-002"]
    assert (c["status"], c["not_run_reason"]) == ("not-run", "mosaic-pack")


def test_terrain_has_no_default_origin(tmp_path):
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _hgt(3)})
    c = _by_id(_rep(pack))["PACK-003"]
    assert (c["status"], c["measured"]) == ("fail", "(unset) (kind terrain has no default; pass --origin)")


def test_origin_check_says_how_the_label_was_set(tmp_path):
    # C6 (AER-4102): for a kind with a default label the check is
    # tautological, and the record has to say so.
    pack = _proc_pack(tmp_path / "p.pack")
    c = _by_id(_rep(pack))["PACK-003"]
    assert c["measured"] == "federal (defaulted by kind procedures, not read from the pack)"
    assert "tautological" in c["cannot_catch"]
    c = _by_id(_rep(pack, origin="community"))["PACK-003"]
    assert c["measured"] == "community (passed with --origin)"


def test_sources_check_looks_at_each_sources_edition_and_sha256(tmp_path):
    pack = _proc_pack(tmp_path / "p.pack")
    good = {"name": "FAA CIFP", "edition": "2611", "url": "u", "retrieved": "t",
            "sha256": "a" * 64}
    c = _by_id(_rep(pack, sources=[good]))["PACK-004"]
    assert (c["status"], c["measured"]) == ("pass", "1 source(s), each with edition and sha256")
    c = _by_id(_rep(pack, sources=[good, {"name": "NASR", "edition": "", "sha256": "x"}]))
    assert (c["PACK-004"]["status"], c["PACK-004"]["measured"]) == (
        "fail", "2 source(s); incomplete: NASR (no edition, no 64-hex sha256)")


def test_wording_names_what_the_checks_do_not_cover():
    cat = {c.id: c for c in report.catalogue("navdata", is_zip=False)}
    assert "integrity_check" in cat["SQL-001"].cannot_catch
    acc = cat["ACC-001"].cannot_catch
    assert "Denver" in acc and "Alaska" in acc and "Hawaii" in acc


# --- device acceptance --------------------------------------------------------

_FAKE_AIRWAYS = '''
import sqlite3
class AirwayGraph:
    def __init__(self, path):
        self._con = sqlite3.connect(str(path))
        self.ready = True
    def legs(self, ident):
        return self._con.execute(
            "SELECT al.seq FROM airway_legs al JOIN airways a ON a.id = al.airway_id "
            "WHERE a.ident = ?", (ident,)).fetchall()
    def close(self):
        self._con.close()
'''


def _fake_pyefis(tmp_path):
    src = tmp_path / "pyefis-src"
    (src / "pyefis" / "flightplan").mkdir(parents=True)
    (src / "pyefis" / "__init__.py").write_text("")
    (src / "pyefis" / "flightplan" / "__init__.py").write_text("")
    (src / "pyefis" / "flightplan" / "airways.py").write_text(_FAKE_AIRWAYS)
    return src


def test_device_acceptance_runs_the_reader_in_a_subprocess(tmp_path):
    src = _fake_pyefis(tmp_path)
    rec = _rep(_proc_pack(tmp_path / "p.pack", airways=30), probe=True, pyefis_src=src)
    c = _by_id(rec)["ACC-001"]
    # No J80 in a pack of airways V1..V30: that query is empty and the check fails.
    assert c["status"] == "fail" and c["measured"].endswith("empty: airway legs J80")
    full = _rep(_proc_pack(tmp_path / "q.pack", airways=30, jet=True), probe=True,
                pyefis_src=src)
    c = _by_id(full)["ACC-001"]
    assert (c["status"], c["measured"]) == ("pass", "opened; 3/3 queries returned data")
    assert [q["count"] for q in full["metrics"]["ACC-001"]] == [5, 5, 5]


def test_device_acceptance_without_pyefis_is_not_run(tmp_path, monkeypatch):
    monkeypatch.delenv("PYEFIS_SRC_DIR", raising=False)
    monkeypatch.delenv("PYEFIS_TOOLS_DIR", raising=False)
    c = _by_id(_rep(_proc_pack(tmp_path / "p.pack"), probe=True))["ACC-001"]
    assert c["status"] == "not-run" and "PYEFIS_SRC_DIR" in c["note"]
    # A checkout whose reader cannot be imported is an environment problem, not a pack defect.
    broken = tmp_path / "broken"
    (broken / "pyefis").mkdir(parents=True)
    (broken / "pyefis" / "__init__.py").write_text("")
    c = _by_id(_rep(_proc_pack(tmp_path / "q.pack"), probe=True, pyefis_src=broken))["ACC-001"]
    assert c["status"] == "not-run" and "could not be loaded" in c["note"]


_FAKE_SVS = '''
import os
class _Grid:
    def __init__(self, side):
        self.shape = (side, side)
class TileCache:
    def __init__(self, root):
        self.root = root
    def get(self, lat, lon):
        return _Grid(int(os.environ["FAKE_GRID_SIDE"]))
'''


def test_terrain_device_probe_wants_the_real_grid_side(tmp_path, monkeypatch):
    # AER-4102: the probe accepted any grid side of 1 or more; it must be
    # the side the .hgt size implies (the side TER-001 checks).
    src = tmp_path / "pyefis-src"
    ai = src / "pyefis" / "instruments" / "ai"
    ai.mkdir(parents=True)
    for d in (src / "pyefis", src / "pyefis" / "instruments", ai):
        (d / "__init__.py").write_text("")
    (ai / "svs.py").write_text(_FAKE_SVS)
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _hgt(3)})
    monkeypatch.setenv("FAKE_GRID_SIDE", "1")
    c = _by_id(_rep(pack, origin="federal", probe=True, pyefis_src=src))["ACC-001"]
    assert c["status"] == "fail"
    monkeypatch.setenv("FAKE_GRID_SIDE", "3")
    rec = _rep(pack, origin="federal", probe=True, pyefis_src=src)
    assert _by_id(rec)["ACC-001"]["status"] == "pass"
    assert rec["metrics"]["ACC-001"][0]["min"] == 3


# --- --check: reproduce a published record ------------------------------------

def test_check_reproduces_the_published_numbers(tmp_path, capsys):
    prev_pack = _proc_pack(tmp_path / "2610.pack", "2610", airways=50, collide=2)
    pack = _proc_pack(tmp_path / "2611.pack", unresolved=3, legs=1000)
    published = tmp_path / "report.json"
    # Exit 1: device acceptance was switched off, which blocks a build record.
    assert cli.main(["report", str(pack), "--previous", str(prev_pack), "--no-device",
                     "--note", "ROW-002=FAA withdrew airways", "-o", str(published)]) == 1
    printed = capsys.readouterr().out
    assert [ln.split()[2] for ln in printed.splitlines() if "BLOCKS PUBLISH" in ln] == ["ACC-001"]
    assert cli.main(["report", "--check", str(pack), "--against", str(published),
                     "--previous", str(prev_pack), "--no-device"]) == 0
    out = capsys.readouterr().out
    assert "0 mismatched" in out and "MISMATCH" not in out
    assert "PROC-FIX-001   reproduced" in out


def test_check_flags_a_number_that_does_not_reproduce(tmp_path, capsys):
    pack = _proc_pack(tmp_path / "p.pack", unresolved=3)
    rec = _rep(pack)
    for c in rec["checks"]:
        if c["id"] == "PROC-FIX-001":
            c["measured"] = "1 of 400 legs (0.25%)"
    rec["tables"]["legs"]["rows"] = 399
    published = tmp_path / "report.json"
    published.write_bytes(report.to_bytes(rec))
    assert cli.main(["report", "--check", str(pack), "--against", str(published),
                     "--no-device"]) == 1
    out = capsys.readouterr().out
    assert "PROC-FIX-001   MISMATCH" in out and "MISMATCH tables.legs" in out


def test_check_reproduces_an_origin_passed_on_the_command_line(tmp_path, capsys):
    pack = _terrain_pack(tmp_path / "t.pack", {"N40/N40W100.hgt": _hgt(3)})
    published = tmp_path / "report.json"
    cli.main(["report", str(pack), "--origin", "community", "--no-device", "-o", str(published)])
    capsys.readouterr()
    assert cli.main(["report", "--check", str(pack), "--against", str(published),
                     "--no-device"]) == 0
    assert "PACK-003       reproduced" in capsys.readouterr().out


def test_check_without_the_previous_edition_says_not_reproducible(tmp_path):
    prev = report.previous_from(_proc_pack(tmp_path / "2610.pack", "2610", airways=50))
    pack = _proc_pack(tmp_path / "2611.pack")
    rows, problems = report.compare(_rep(pack, previous=prev), _rep(pack))
    by = {r.id: r.outcome for r in rows}
    assert problems == []
    assert by["ROW-002"] == "not reproducible here"
    assert by["PROC-DIFF-001"] == "not run in record"
    assert by["PROC-AWY-002"] == "reproduced"


def test_check_prints_auspex_findings_as_not_reproducible():
    lines = report.auspex_lines({"auspex": {
        "check": "pack_check", "when": "pre-publish", "verdict": "fail",
        "findings": [{"locator": {"table": "water", "row_id": 7},
                      "statement": "ring crosses itself"}]}})
    assert "not reproducible here (Auspex)" in lines[0]
    assert "table=water, row_id=7" in lines[1]


# --- CLI and serialisation ----------------------------------------------------

def test_report_cli_writes_canonical_bytes_and_gates(tmp_path, capsys):
    pack = _proc_pack(tmp_path / "p.pack", collide=1)
    out = tmp_path / "r.json"
    assert cli.main(["report", str(pack), "--no-device", "-o", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "BLOCKS PUBLISH  PROC-AWY-002 failed: 1" in printed
    raw = out.read_bytes()
    assert raw == report.to_bytes(json.loads(raw))
    assert report.validate_report(json.loads(raw)) == []


def test_list_checks_prints_every_kind(capsys):
    assert cli.main(["report", "--list-checks"]) == 0
    out = capsys.readouterr().out
    for kind in packmeta.KINDS:
        assert f"== {kind}" in out
    assert "cannot catch:" in out


def test_validate_report_catches_a_malformed_record(tmp_path):
    rec = _rep(_proc_pack(tmp_path / "p.pack"))
    rec["checks"][0]["cannot_catch"] = ""
    rec["checks"][1]["status"] = "not-run"
    rec["notice"] = "trust me"
    errs = report.validate_report(rec)
    assert any("no cannot_catch" in e for e in errs)
    assert any("measured must be null" in e for e in errs)
    assert any("summary does not equal" in e for e in errs)
    assert any("notice" in e for e in errs)


def test_http_get_never_sends_the_default_python_user_agent(monkeypatch):
    from packtools import fetch
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert fetch.http_get("https://navdata.aerocommons.org/manifest.json") == b"ok"
    assert seen["ua"] == fetch.USER_AGENT and "Python-urllib" not in seen["ua"]


# --- Grid MORA (AER-4447) -------------------------------------------------------------

def test_a_clean_grid_passes_every_mora_check(tmp_path):
    c = _by_id(_rep(_proc_pack(tmp_path / "p.pack", mora_unk=7)))
    for cid in ("PROC-MORA-001", "PROC-MORA-002", "PROC-MORA-003"):
        assert c[cid]["status"] == "pass", (cid, c[cid])
        assert c[cid]["blocking"] is True
    assert c["PROC-MORA-004"]["status"] == "pass"
    assert c["PROC-MORA-004"]["measured"].startswith("7 of 3,600 cells UNK")


def test_short_band_fails_the_cell_count(tmp_path):
    c = _by_id(_rep(_proc_pack(tmp_path / "p.pack", mora_cells=29)))["PROC-MORA-001"]
    assert c["status"] == "fail"
    assert "3,480 cells for 120 bands (expected 3,600)" in c["measured"]


def test_too_few_bands_fails_the_floor(tmp_path):
    c = _by_id(_rep(_proc_pack(tmp_path / "p.pack",
                               mora_bands=report.PROC_MORA_BAND_FLOOR - 1)))
    assert c["PROC-MORA-001"]["status"] == "fail"


def test_missing_grid_fails(tmp_path):
    pack = _proc_pack(tmp_path / "p.pack")
    con = sqlite3.connect(pack)
    con.executescript("DROP TABLE grid_mora; DROP TABLE grid_mora_bands;")
    con.close()
    c = _by_id(_rep(pack))
    assert all(c[f"PROC-MORA-00{i}"]["status"] == "fail" for i in (1, 2, 3, 4))


def test_duplicate_cell_fails(tmp_path):
    pack = _proc_pack(tmp_path / "p.pack")
    con = sqlite3.connect(pack)
    con.execute("DROP INDEX idx_mora_cell")
    con.execute("UPDATE grid_mora SET lon_sw = lon_sw - 1 WHERE rowid = 2")
    con.commit()
    con.close()
    c = _by_id(_rep(pack))["PROC-MORA-002"]
    assert (c["status"], c["measured"]) == ("fail", "1 duplicate cells")


@pytest.mark.parametrize("ft, raw", [(900, "009"), (25100, "251")])
def test_out_of_range_value_fails(tmp_path, ft, raw):
    pack = _proc_pack(tmp_path / "p.pack")
    con = sqlite3.connect(pack)
    con.execute("UPDATE grid_mora SET mora_ft = ?, raw = ? WHERE rowid = 1", (ft, raw))
    con.commit()
    con.close()
    c = _by_id(_rep(pack))["PROC-MORA-003"]
    assert c["status"] == "fail" and c["measured"].startswith("1 cells outside")


def test_garbled_field_fails(tmp_path):
    pack = _proc_pack(tmp_path / "p.pack")
    con = sqlite3.connect(pack)
    con.execute("UPDATE grid_mora SET mora_ft = NULL, raw = '1 3' WHERE rowid = 1")
    con.commit()
    con.close()
    c = _by_id(_rep(pack))["PROC-MORA-003"]
    assert c["status"] == "fail" and "1 fields neither digits nor UNK" in c["measured"]


def test_more_unk_than_last_edition_warns(tmp_path):
    prev = report.previous_from(_proc_pack(tmp_path / "2610.pack", "2610", mora_unk=5))
    more = _by_id(_rep(_proc_pack(tmp_path / "a.pack", mora_unk=6), previous=prev))
    same = _by_id(_rep(_proc_pack(tmp_path / "b.pack", mora_unk=5), previous=prev))
    assert more["PROC-MORA-004"]["status"] == "warn"
    assert more["PROC-MORA-004"]["blocking"] is False
    assert same["PROC-MORA-004"]["status"] == "pass"
