"""Water pipeline: a water.sqlite is a single sqlite pack (kind=water). It
reuses the existing sqlite install path; the build side is build-pack/publish.
ODbL attribution (OSM) must ride along in pack_meta."""

import datetime as dt
import shutil
import sqlite3

import pytest

from packtools import signing
from packtools.manifest import Manifest, PackEntry
from packtools.packmeta import PackMeta, embed_sqlite, read_sqlite
from packtools.publish import publish
from packtools.upload import LocalStore
from pyefis_data.config import Config
from pyefis_data.core import Updater, LocalDirRemote

TODAY = dt.date(2026, 6, 14)
ORIGIN = "https://test.local"
ATTR = "OpenStreetMap contributors (ODbL); Natural Earth"


def _water_db(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE water_polygons (id INTEGER, kind TEXT, minlat REAL)")
    con.execute("INSERT INTO water_polygons VALUES (1, 'ocean', 34.0)")
    con.commit()
    con.close()


def _water_pack(tmp_path):
    """Build + pack a synthetic water.sqlite; return (pack_path, entry)."""
    db = tmp_path / "water.sqlite"
    _water_db(db)
    pack = tmp_path / "build" / "packs" / "water-conus-2026q2.pack"
    pack.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, pack)
    meta = PackMeta(id="water-conus", kind="water", cycle="2026q2", attribution=ATTR)
    embed_sqlite(pack, meta)
    entry = PackEntry.from_pack(
        pack, meta, url=f"{ORIGIN}/packs/{pack.name}", regions=["conus"])
    return pack, entry


def _publish(tmp_path, pairs):
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    publish(store, sk, pairs, generated="2026-06-14T00:00:00Z",
            sign=signing.sign, log=lambda *a: None)
    return store, pub


def test_water_is_non_cyclical_and_carries_odbl(tmp_path):
    _pack, entry = _water_pack(tmp_path)
    assert entry.kind == "water"
    assert entry.effective is None and entry.expires is None   # edition, not a cycle
    assert "ODbL" in entry.attribution


def test_publish_then_install_water(tmp_path):
    pack, entry = _water_pack(tmp_path)
    store, pub = _publish(tmp_path, [(entry, pack)])
    # water is region-gated (BULK): opt into 'conus'
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", regions=("conus",))
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)
    up.update()
    # installed as an sqlite with a current pointer (like navdata)
    assert up._current_target(tmp_path / "pi" / "water") == "2026q2"
    f = tmp_path / "pi" / "water" / "current" / "water.sqlite"
    assert f.exists()
    con = sqlite3.connect(str(f))
    assert con.execute("SELECT COUNT(*) FROM water_polygons").fetchone()[0] == 1
    con.close()
    assert "ODbL" in read_sqlite(f).attribution          # attribution survives install
    st = {r.pack_id: r for r in up.status()}
    assert st["water-conus"].status == "current" and st["water-conus"].kind == "water"


def test_water_not_tracked_without_region(tmp_path):
    pack, entry = _water_pack(tmp_path)
    store, pub = _publish(tmp_path, [(entry, pack)])
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi")    # no regions opted in
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    assert up._tracked_ids(m) == []                       # water-conus not pulled


def test_publish_adds_alongside_existing(tmp_path):
    # a prior navdata entry must survive a water publish
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    nav = PackEntry(id="airports-conus", kind="navdata", cycle="2606", bytes=1,
                    sha256="a" * 64, url="u", effective="2026-06-11", expires="2026-07-09")
    publish(store, sk, [(nav, _existing_sqlite(tmp_path))],
            generated="2026-06-14T00:00:00Z", sign=signing.sign, log=lambda *a: None)
    pack, entry = _water_pack(tmp_path)
    publish(store, sk, [(entry, pack)], generated="2026-06-14T00:00:00Z",
            sign=signing.sign, log=lambda *a: None)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    ids = {p.id for p in m.packs}
    assert {"airports-conus", "water-conus"} <= ids       # both present


def _existing_sqlite(tmp_path):
    p = tmp_path / "airports-conus-2606.pack"
    con = sqlite3.connect(str(p)); con.execute("CREATE TABLE t(x)"); con.commit(); con.close()
    return p
