"""pack_meta embedding/reading for both container types."""

import sqlite3
import zipfile

import pytest

from packtools import packmeta
from packtools.packmeta import PackMeta


def _make_sqlite(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE airports (icao TEXT, lat REAL, lon REAL)")
    con.execute("INSERT INTO airports VALUES ('KSBA', 34.4, -119.8)")
    con.commit()
    con.close()


def test_sqlite_embed_read_roundtrip(tmp_path):
    db = tmp_path / "airports.sqlite"
    _make_sqlite(db)
    meta = PackMeta(id="navdata-conus", kind="navdata", cycle="2606",
                    effective="2026-06-11", expires="2026-07-09",
                    attribution="FAA NASR (public domain)")
    packmeta.embed_sqlite(db, meta)
    got = packmeta.read_sqlite(db)
    assert got == meta


def test_embedding_does_not_disturb_payload(tmp_path):
    db = tmp_path / "airports.sqlite"
    _make_sqlite(db)
    packmeta.embed_sqlite(db, PackMeta(id="x", kind="navdata", cycle="2606"))
    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT icao FROM airports").fetchall()
    con.close()
    assert rows == [("KSBA",)]


def test_reembed_replaces_not_appends(tmp_path):
    db = tmp_path / "a.sqlite"
    _make_sqlite(db)
    packmeta.embed_sqlite(db, PackMeta(id="x", kind="navdata", cycle="2605"))
    packmeta.embed_sqlite(db, PackMeta(id="x", kind="navdata", cycle="2606"))
    con = sqlite3.connect(str(db))
    n = con.execute("SELECT COUNT(*) FROM pack_meta WHERE key='cycle'").fetchone()[0]
    cycle = con.execute("SELECT value FROM pack_meta WHERE key='cycle'").fetchone()[0]
    con.close()
    assert n == 1 and cycle == "2606"


def test_zip_embed_read_roundtrip(tmp_path):
    z = tmp_path / "terrain-us-west.pack"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("N32/N32W097.hgt", b"\x00\x01\x02")
    meta = PackMeta(id="terrain-us-west", kind="terrain", cycle="2024ed",
                    attribution="Copernicus GLO-30")
    packmeta.embed_zip(z, meta)
    assert packmeta.read_zip(z) == meta
    # payload survived
    with zipfile.ZipFile(z) as zf:
        assert "N32/N32W097.hgt" in zf.namelist()


def test_read_dispatch_detects_container(tmp_path):
    db = tmp_path / "a.sqlite"
    _make_sqlite(db)
    packmeta.embed_sqlite(db, PackMeta(id="x", kind="obstacles", cycle="260611"))
    assert packmeta.read(db).kind == "obstacles"

    z = tmp_path / "b.pack"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("f", b"x")
    packmeta.embed_zip(z, PackMeta(id="y", kind="terrain", cycle="2024ed"))
    assert packmeta.read(z).kind == "terrain"


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        PackMeta(id="x", kind="nonsense", cycle="2606")


def test_non_cyclical_meta_omits_none_fields():
    meta = PackMeta(id="terrain-na", kind="terrain", cycle="2024ed")
    d = meta.as_dict()
    assert "effective" not in d and "expires" not in d
    assert d["schema_version"] == packmeta.SCHEMA_VERSION
