"""packtool CLI — genkey -> build-pack -> verify, plus failure exit codes."""

import sqlite3

import pytest

from packtools import cli


def _make_sqlite(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE airports (icao TEXT)")
    con.execute("INSERT INTO airports VALUES ('KSBA')")
    con.commit()
    con.close()


def test_genkey_build_verify_roundtrip(tmp_path, capsys):
    keys = tmp_path / "keys"
    assert cli.main(["genkey", "--out", str(keys)]) == 0
    assert (keys / "minisign.pub").exists()
    assert (keys / "minisign.sec").exists()

    src = tmp_path / "airports.sqlite"
    _make_sqlite(src)
    out = tmp_path / "work"
    rc = cli.main(["build-pack", str(src), "--id", "airports-conus",
                   "--kind", "navdata", "--date", "2026-06-14",
                   "--attribution", "FAA NASR", "--regions", "conus",
                   "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0
    assert (out / "manifest.json").exists()
    # named by the computed AIRAC cycle for 2026-06-14
    assert (out / "packs" / "airports-conus-2606.pack").exists()

    rc = cli.main(["verify", str(out / "manifest.json"),
                   "--pub", str(keys / "minisign.pub")])
    assert rc == 0


def test_verify_detects_tampering(tmp_path):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "obstacles.sqlite"
    _make_sqlite(src)
    out = tmp_path / "work"
    cli.main(["build-pack", str(src), "--id", "obstacles-conus",
              "--kind", "obstacles", "--date", "2026-06-14",
              "--sec", str(keys / "minisign.sec"), "--out", str(out)])

    manifest = out / "manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"obstacles", b"0bstacles"))
    rc = cli.main(["verify", str(manifest), "--pub", str(keys / "minisign.pub")])
    assert rc == 2          # signature no longer matches the bytes


def test_build_pack_noncyclical_requires_cycle(tmp_path):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "t.sqlite"
    _make_sqlite(src)
    with pytest.raises(SystemExit):
        cli.main(["build-pack", str(src), "--id", "terrain-na", "--kind", "terrain",
                  "--date", "2026-06-14", "--sec", str(keys / "minisign.sec"),
                  "--out", str(tmp_path / "w")])


def test_build_pack_noncyclical_cycle_only_ok(tmp_path):
    # --cycle alone is sufficient for a non-cyclical kind (water/terrain/highways);
    # no --effective needed. Regression for the build-pack && bug.
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "w.sqlite"
    _make_sqlite(src)
    out = tmp_path / "out"
    rc = cli.main(["build-pack", str(src), "--id", "water-conus", "--kind", "water",
                   "--cycle", "2026q2", "--attribution", "OSM ODbL", "--regions", "conus",
                   "--date", "2026-06-14", "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0
    from packtools.manifest import Manifest
    m = Manifest.read(out / "manifest.json")
    e = next(p for p in m.packs if p.id == "water-conus")
    assert e.cycle == "2026q2" and e.effective is None     # non-cyclical entry
