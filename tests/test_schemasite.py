# SPDX-License-Identifier: Apache-2.0
"""The pack format pages (SCH1, makerplane-data#137): DDL captured verbatim
from the built pack at publish time, rendered to /schema/ with the report
site's lint, written only under keys the renderer owns, and bootstrapped
from the published packs for kinds no publish has captured yet."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from packtools import cli, schemasite, signing
from packtools.manifest import Manifest, PackEntry
from packtools.packmeta import PackMeta, embed_sqlite, embed_zip
from packtools.publish import publish, sign_and_put
from packtools.reportsite import SiteError
from packtools.sitedeploy import site_files
from packtools.upload import LocalStore
from tests.gatefake import STANDIN

ORIGIN = "https://navdata.test"
GEN = "2026-06-14T00:00:00Z"
quiet = dict(log=lambda *a: None)

AIRPORTS_DDL = """CREATE TABLE airports (
    site_no   TEXT PRIMARY KEY,
    icao      TEXT NOT NULL,         -- ARPT_ID, e.g. "KSBA"
    lat       REAL NOT NULL,
    mag_var   REAL                   -- signed: +E, -W
)"""


def _sqlite_pack(dirpath, *, id="airports-conus", kind="navdata", cycle="2612",
                 ddl=AIRPORTS_DDL, schema_version=1, extra=()):
    p = Path(dirpath) / f"{id}-{cycle}.pack"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.unlink(missing_ok=True)
    con = sqlite3.connect(str(p))
    con.execute(ddl)
    con.execute("CREATE INDEX idx_airports_icao ON airports(icao)")
    con.execute("CREATE VIRTUAL TABLE apt_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
    for sql in extra:
        con.execute(sql)
    con.execute("INSERT INTO airports VALUES ('1', 'KSBA', 34.4, -12.0)")
    con.commit()
    con.close()
    meta = PackMeta(id=id, kind=kind, cycle=cycle, attribution="test", license="test",
                    schema_version=schema_version)
    embed_sqlite(p, meta)
    return PackEntry.from_pack(p, meta, url=f"{ORIGIN}/packs/{p.name}"), p


def _terrain_pack(dirpath, *, id="terrain-us-west", cycle="2024edr1", tiles=("N34W120", "N35W120")):
    p = Path(dirpath) / f"{id}-{cycle}.pack"
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for t in tiles:
            z.writestr(f"N{t[1:3]}/{t}.hgt", b"\0\0" * 4)
            z.writestr(f".mip/1/N{t[1:3]}/{t}.hgt", b"\0\0")
            z.writestr(f".mip/1/N{t[1:3]}/{t}.wmask", b"\0")
    meta = PackMeta(id=id, kind="terrain", cycle=cycle, attribution="Copernicus GLO-30")
    embed_zip(p, meta)
    return PackEntry.from_pack(p, meta, url=f"{ORIGIN}/packs/{p.name}"), p


def _signed_store(tmp_path, entries_and_paths):
    """A LocalStore holding the packs and a manifest listing them, signed."""
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    m = Manifest.new(GEN)
    for entry, path in entries_and_paths:
        store.put_file(f"packs/{path.name}", path)
        m.upsert(entry)
    raw = m.to_bytes()
    store.put_bytes("manifest.json", raw)
    store.put_bytes("manifest.json.minisig", signing.sign(raw, sk).encode("ascii"))
    return store, pub, json.loads(raw)


# --- capture -------------------------------------------------------------------

def test_capture_keeps_the_create_statement_verbatim_and_its_comments(tmp_path):
    _, path = _sqlite_pack(tmp_path)
    c = schemasite.capture(path, built={"commit": "a" * 40, "builder_refs": {"pyEfis": "b" * 40}})
    t = c["tables"]["airports"]
    assert t["sql"] == AIRPORTS_DDL
    cols = {x["name"]: x for x in t["columns"]}
    assert cols["icao"]["comment"] == 'ARPT_ID, e.g. "KSBA"'
    assert cols["mag_var"]["comment"] == "signed: +E, -W"
    assert "comment" not in cols["lat"]
    assert cols["site_no"]["pk"] == 1 and cols["icao"]["notnull"] is True
    assert c["indexes"] == {"idx_airports_icao": {
        "table": "airports", "sql": "CREATE INDEX idx_airports_icao ON airports(icao)",
        "unique": False, "columns": ["icao"]}}
    # The R-tree is listed once, with its columns; its shadow tables and the
    # implicit sqlite_autoindex_* entries are not.
    assert c["rtrees"]["apt_rtree"]["columns"] == ["id", "min_lat", "max_lat", "min_lon", "max_lon"]
    assert set(c["tables"]) == {"airports", "pack_meta"}
    assert "schema_version" in c["pack_meta_keys"] and "id" in c["pack_meta_keys"]
    assert c["pack"] == {"id": "airports-conus", "kind": "navdata", "cycle": "2612",
                         "schema_version": "1"}
    assert c["builder"]["tool"] == "tools/build_airport_db.py"
    assert c["builder"]["commit"] == "b" * 40          # the pyEfis tool's commit
    assert c["builder"]["makerplane_data_commit"] == "a" * 40


def test_capture_of_a_zip_pack_is_its_member_layout(tmp_path):
    _, path = _terrain_pack(tmp_path)
    c = schemasite.capture(path)
    assert c["container"] == "zip" and "tables" not in c
    assert [(g["pattern"], g["count"]) for g in c["layout"]] == [
        (".mip/#/N##/N##W###.hgt", 2), (".mip/#/N##/N##W###.wmask", 2),
        ("N##/N##W###.hgt", 2), ("pack_meta.json", 1)]
    assert c["layout"][2]["example"] == "N34/N34W120.hgt"
    assert c["builder"]["tool"] == "packtools/make_terrain.py"


def test_column_comments_ignores_lines_without_a_column():
    assert schemasite.column_comments("CREATE TABLE t (\n  -- a note\n  a INT, -- first\n"
                                      "  \"b\" TEXT -- second\n)") == {"a": "first", "b": "second"}


# --- capture at publish time ---------------------------------------------------

def test_publish_writes_the_capture_after_signing(tmp_path):
    store = LocalStore(tmp_path / "r2")
    sk, _ = signing.generate_keypair()
    entry, path = _sqlite_pack(tmp_path / "b")
    publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign, gate=STANDIN, **quiet)
    doc = json.loads(store.get_bytes("schema/navdata.json"))
    assert doc["schema"] == "pack-schema/1" and doc["kind"] == "navdata"
    cap = doc["packs"]["airports-conus"]
    assert cap["pack"]["cycle"] == "2612" and cap["source"] == "publish"
    assert cap["builder"]["makerplane_data_commit"] == "c" * 40   # from the record


def test_a_refused_publish_writes_no_capture(tmp_path):
    from packtools import gate
    store = LocalStore(tmp_path / "r2")
    sk, _ = signing.generate_keypair()
    entry, path = _sqlite_pack(tmp_path / "b")

    def refuse(p, **kw):
        rec = STANDIN.reporter(p, **kw)
        rec["checks"][0]["status"] = "fail"
        rec["checks"][0]["blocking"] = True
        return rec
    with pytest.raises(gate.GateRefused):
        publish(store, sk, [(entry, path)], generated=GEN, sign=signing.sign,
                gate=gate.Options(reporter=refuse), **quiet)
    assert store.list_keys("schema/") == []


def test_the_cyclical_publish_writes_the_capture_after_signing(tmp_path):
    import datetime as dt
    from packtools.run_cyclical import CyclicalRunner
    from packtools.schema_guard import NULL_GUARD
    from packtools.sources import SOURCES
    from tests.gatefake import standin_reporter
    from tests.test_gate import _fake_build, _fake_fetch
    store = LocalStore(tmp_path / "r2")
    sk, _ = signing.generate_keypair()
    CyclicalRunner(store=store, secret=sk, work_dir=tmp_path / "work",
                   url_base=f"{ORIGIN}/packs", fetcher=_fake_fetch,
                   builders={"obstacles": _fake_build}, guard=NULL_GUARD,
                   reporter=standin_reporter, today=dt.date(2026, 6, 14)
                   ).run([SOURCES["obstacles-conus"]])
    cap = json.loads(store.get_bytes("schema/obstacles.json"))["packs"]["obstacles-conus"]
    assert cap["tables"]["t"]["sql"] == "CREATE TABLE t (x INTEGER)"
    assert cap["pack"]["cycle"] in {p.cycle for p in Manifest.from_bytes(
        store.get_bytes("manifest.json")).packs}


def test_a_later_publish_replaces_only_its_own_id(tmp_path):
    store = LocalStore(tmp_path / "r2")
    a = _terrain_pack(tmp_path / "a", id="terrain-us-west")
    b = _terrain_pack(tmp_path / "b", id="terrain-us-east")
    schemasite.put_captures(store, [(a[1], None), (b[1], None)], **quiet)
    a2 = _terrain_pack(tmp_path / "c", id="terrain-us-west", cycle="2025edr1")
    schemasite.put_captures(store, [(a2[1], None)], **quiet)
    packs = json.loads(store.get_bytes("schema/terrain.json"))["packs"]
    assert packs["terrain-us-west"]["pack"]["cycle"] == "2025edr1"
    assert packs["terrain-us-east"]["pack"]["cycle"] == "2024edr1"


def test_an_unreadable_pack_is_logged_never_raised(tmp_path):
    store = LocalStore(tmp_path / "r2")
    bad = tmp_path / "junk.pack"
    bad.write_bytes(b"not a pack")
    log = []
    assert schemasite.put_captures(store, [(bad, None)], log=log.append) == []
    assert any(line.startswith("WARN schema: no capture of junk.pack") for line in log)


# --- render --------------------------------------------------------------------

def _kinds(tmp_path, **kw):
    store = LocalStore(tmp_path / "caps")
    sq = _sqlite_pack(tmp_path / "s", **kw)
    tz = _terrain_pack(tmp_path / "t")
    schemasite.put_captures(store, [(sq[1], None), (tz[1], None)], **quiet)
    manifest = Manifest.new(GEN)
    manifest.upsert(sq[0])
    manifest.upsert(tz[0])
    return schemasite.load_kinds(store), manifest.to_obj()


def test_build_renders_a_page_per_kind_and_the_index(tmp_path):
    kinds, manifest = _kinds(tmp_path)
    out = schemasite.build(kinds, manifest, commit="d" * 40, descriptions={
        "navdata.airports.site_no": "NASR site number"})
    assert set(out) == {"schema/index.html", "schema/index.json", "schema/navdata.html",
                        "schema/terrain.html"}
    page = out["schema/navdata.html"].decode()
    assert "airports-conus</code> edition <code>2612</code>" in page
    assert "tools/build_airport_db.py" in page
    assert "signed: +E, -W" in page and "NASR site number" in page
    assert "CREATE TABLE airports (" in page
    # Undocumented columns are listed, not failed.
    assert "Not yet described" in page and "<code>airports.lat</code>" in page
    terrain = out["schema/terrain.html"].decode()
    assert "there is no SQL" in terrain and "N##/N##W###.hgt" in terrain
    idx = json.loads(out["schema/index.json"])
    assert idx["schema"] == "schema-index/1"
    assert idx["kinds"]["navdata"]["packs"]["airports-conus"] == {
        "cycle": "2612", "schema_version": "1", "listed": True}
    assert "airports.lat" in idx["kinds"]["navdata"]["undocumented"]
    assert idx["uncaptured"] == []
    home = out["schema/index.html"].decode()
    for field in ("report_sha256", "sha256", "schema_version", "license_url"):
        assert f"<code>{field}</code>" in home
    assert "ROW-003" in home and "Apache License 2.0" in home


def test_the_version_rules_come_from_packmeta(monkeypatch):
    from packtools import packmeta
    rules = schemasite.version_rules()
    hw = [r for r in rules if r["kind"] == "highways"]
    assert [r["schema_version"] for r in hw] == [1, 2]
    assert hw[1]["content"] == "adds flags, ref"
    # A new version in packmeta appears with no edit here.
    monkeypatch.setitem(packmeta.PROCEDURES_TABLE_SCHEMAS,
                        frozenset({"airways", "airway_legs", "procedures", "transitions",
                                   "legs", "msa", "grid_mora_bands", "grid_mora",
                                   "airspace"}), 99)
    last = [r for r in schemasite.version_rules() if r["kind"] == "procedures"][-1]
    assert (last["schema_version"], last["content"]) == (99, "adds airspace")


def test_a_live_kind_with_no_capture_is_named_on_the_index(tmp_path):
    kinds, manifest = _kinds(tmp_path)
    manifest["packs"].append(dict(manifest["packs"][0], id="water-na", kind="water",
                                  cycle="2026q2r6"))
    out = schemasite.build(kinds, manifest, descriptions={})
    assert json.loads(out["schema/index.json"])["uncaptured"] == ["water"]
    assert "no capture yet" in out["schema/index.html"].decode()


def test_unchanged_captures_render_to_unchanged_bytes(tmp_path):
    kinds, manifest = _kinds(tmp_path)
    assert schemasite.build(kinds, manifest, descriptions={}) == \
        schemasite.build(json.loads(json.dumps(kinds)), manifest, descriptions={})


def test_a_schema_version_change_regenerates_the_page(tmp_path):
    k1, m1 = _kinds(tmp_path / "1")
    k2, m2 = _kinds(tmp_path / "2", schema_version=2)
    a = schemasite.build(k1, m1, descriptions={})
    b = schemasite.build(k2, m2, descriptions={})
    assert a["schema/navdata.html"] != b["schema/navdata.html"]
    assert a["schema/terrain.html"] == b["schema/terrain.html"]
    assert json.loads(b["schema/index.json"])["kinds"]["navdata"]["packs"][
        "airports-conus"]["schema_version"] == "2"


def test_a_banned_word_in_a_builder_comment_fails_the_render(tmp_path):
    kinds, manifest = _kinds(tmp_path, ddl=AIRPORTS_DDL.replace("signed: +E, -W",
                                                                "certified value"))
    with pytest.raises(SiteError, match="banned word 'certified'"):
        schemasite.build(kinds, manifest, descriptions={})


def test_a_local_path_in_a_capture_is_scrubbed(tmp_path):
    kinds, manifest = _kinds(tmp_path, ddl=AIRPORTS_DDL.replace(
        "signed: +E, -W", "from /home/builder/nasr/APT_BASE.csv"))
    page = schemasite.build(kinds, manifest, descriptions={})["schema/navdata.html"].decode()
    assert "/home/builder" not in page and "&lt;local path&gt;" in page


def test_the_shipped_descriptions_file_parses_and_keys_are_well_formed():
    desc = schemasite.load_descriptions()
    assert desc["highways.highway_lines.flags"].startswith("Bit 0: tunnel")
    for key in desc:
        head = key.split(".", 1)[0]
        assert head in {"*", "manifest", "pack_meta"} | set(schemasite.BUILDERS), key


# --- what may be written --------------------------------------------------------

def test_the_renderer_writes_only_pages_and_the_index(tmp_path):
    store = LocalStore(tmp_path / "r2")
    for key in ("schema/navdata.json", "schema/sub/x.html", "reports/index.html",
                "manifest.json"):
        with pytest.raises(SiteError, match="not a generated page"):
            schemasite.publish(store, {key: b"x"}, **quiet)
    rows = schemasite.publish(store, {"schema/index.json": b"{}", "schema/a.html": b"<p>"},
                              **quiet)
    assert [r[2] for r in rows] == ["new", "new"]
    assert [r[2] for r in schemasite.publish(store, {"schema/a.html": b"<p>"}, **quiet)] == \
        ["unchanged"]


def test_site_deploy_refuses_schema_keys(tmp_path):
    root = tmp_path / "site"
    (root / "schema").mkdir(parents=True)
    (root / "schema" / "index.html").write_bytes(b"<p>")
    with pytest.raises(ValueError, match="reserved for the pack pipeline"):
        site_files(root)


# --- bootstrap -----------------------------------------------------------------

def _bytes_fetch(blob, calls):
    def fetch(url, start, end):
        calls.append((start, end))
        return blob[start:end + 1]
    return fetch


def test_http_range_file_lets_zipfile_list_a_remote_pack(tmp_path):
    _, path = _terrain_pack(tmp_path)
    blob, calls = path.read_bytes(), []
    raw = schemasite.HTTPRangeFile("https://x/p", len(blob), _bytes_fetch(blob, calls))
    with zipfile.ZipFile(io.BufferedReader(raw, buffer_size=64)) as z:
        assert "pack_meta.json" in z.namelist()
    assert calls and all(e >= s for s, e in calls)


def test_bootstrap_captures_each_uncaptured_kind_from_the_published_packs(tmp_path):
    nav = _sqlite_pack(tmp_path / "n")
    nav_big = _sqlite_pack(tmp_path / "n2", id="airports-west", extra=(
        "CREATE TABLE padding (x BLOB)", "INSERT INTO padding VALUES (zeroblob(200000))"))
    ter = _terrain_pack(tmp_path / "t")
    store, _, manifest = _signed_store(tmp_path, [nav, nav_big, ter])
    blob = ter[1].read_bytes()
    written = schemasite.bootstrap(store, manifest, fetch=_bytes_fetch(blob, []), **quiet)
    assert written == ["schema/navdata.json", "schema/terrain.json"]
    navdoc = json.loads(store.get_bytes("schema/navdata.json"))
    # One pack per sqlite kind, the smallest; every zip pack.
    assert list(navdoc["packs"]) == ["airports-conus"]
    assert navdoc["packs"]["airports-conus"]["source"].startswith("bootstrap")
    assert list(json.loads(store.get_bytes("schema/terrain.json"))["packs"]) == ["terrain-us-west"]
    # A second run has nothing to do.
    assert schemasite.bootstrap(store, manifest, fetch=_bytes_fetch(blob, []), **quiet) == []


def test_bootstrap_never_replaces_a_publish_capture(tmp_path):
    nav = _sqlite_pack(tmp_path / "n")
    store, _, manifest = _signed_store(tmp_path, [nav])
    schemasite.put_captures(store, [(nav[1], {"built": {"commit": "e" * 40}})], **quiet)
    before = store.get_bytes("schema/navdata.json")
    assert schemasite.bootstrap(store, manifest, **quiet) == []
    assert store.get_bytes("schema/navdata.json") == before


def test_bootstrap_skips_a_pack_whose_bytes_do_not_match_the_manifest(tmp_path):
    nav = _sqlite_pack(tmp_path / "n")
    store, _, manifest = _signed_store(tmp_path, [nav])
    store.put_bytes(f"packs/{nav[1].name}", nav[1].read_bytes() + b"tampered")
    log = []
    assert schemasite.bootstrap(store, manifest, log=log.append) == []
    assert any("does not match the signed manifest's sha256" in line for line in log)


# --- the command ---------------------------------------------------------------

def test_schema_site_command_renders_a_preview(tmp_path, monkeypatch):
    nav = _sqlite_pack(tmp_path / "n")
    store, pub, _ = _signed_store(tmp_path, [nav])
    (tmp_path / "pub").write_text(pub)
    out = tmp_path / "preview"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    rc = cli.main(["schema-site", "--from", str(store.root), "--out", str(out),
                   "--pub", str(tmp_path / "pub"), "--bootstrap"])
    assert rc == 0
    assert (out / "schema" / "navdata.html").exists()
    assert json.loads((out / "schema" / "index.json").read_text())["kinds"]["navdata"]
    assert (out / "index.html").exists()        # the site itself, for browsing


def test_schema_site_command_refuses_an_unsigned_manifest(tmp_path):
    nav = _sqlite_pack(tmp_path / "n")
    store, _, _ = _signed_store(tmp_path, [nav])
    _, other_pub = signing.generate_keypair()
    (tmp_path / "pub").write_text(other_pub)
    assert cli.main(["schema-site", "--from", str(store.root), "--out", str(tmp_path / "o"),
                     "--pub", str(tmp_path / "pub")]) == 1
