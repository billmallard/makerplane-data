# SPDX-License-Identifier: Apache-2.0
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
from packtools.publish import publish, retract
from packtools.upload import LocalStore
from tests.gatefake import STANDIN, standin_reporter
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
    publish(store, sk, gate=STANDIN, pairs=pairs, generated="2026-06-14T00:00:00Z",
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
    publish(store, sk, gate=STANDIN, pairs=[_nav(tmp_path)],
            generated="2026-06-14T00:00:00Z", sign=signing.sign, log=lambda *a: None)
    pack, entry = _water_pack(tmp_path)
    publish(store, sk, gate=STANDIN, pairs=[(entry, pack)], generated="2026-06-14T00:00:00Z",
            sign=signing.sign, log=lambda *a: None)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    ids = {p.id for p in m.packs}
    assert {"airports-conus", "water-conus"} <= ids       # both present


def test_retract_drops_one_entry_leaves_rest(tmp_path):
    # Undo path for a bad publish (makerplane-data#60): a states-limited
    # test build uploaded under the production id must be removable without
    # disturbing any other pack already in the manifest.
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    publish(store, sk, gate=STANDIN, pairs=[_nav(tmp_path)],
            generated="2026-06-14T00:00:00Z", sign=signing.sign, log=lambda *a: None)
    good = _sqlite_pack(tmp_path, "highways-conus", "highways", "2026q3r1")
    bad = _sqlite_pack(tmp_path, "highways-conus", "highways", "2026q3r1-smoketest")
    publish(store, sk, gate=STANDIN, pairs=[good, bad],
            generated="2026-06-14T00:00:00Z", sign=signing.sign, log=lambda *a: None)

    retract(store, sk, "highways-conus", "2026q3r1-smoketest",
            generated="2026-06-15T00:00:00Z", sign=signing.sign, log=lambda *a: None)

    raw = store.get_bytes("manifest.json")
    sig = store.get_bytes("manifest.json.minisig").decode("ascii")
    trusted = signing.verify(raw, sig, pub)
    assert "retracted highways-conus/2026q3r1-smoketest" in trusted
    m = Manifest.from_bytes(raw)
    cycles = {(p.id, p.cycle) for p in m.packs}
    assert ("highways-conus", "2026q3r1-smoketest") not in cycles
    assert ("highways-conus", "2026q3r1") in cycles
    assert ("airports-conus", "2606") in cycles


def test_retract_missing_entry_raises(tmp_path):
    pack, entry = _water_pack(tmp_path)
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    publish(store, sk, gate=STANDIN, pairs=[(entry, pack)], generated="2026-06-14T00:00:00Z",
            sign=signing.sign, log=lambda *a: None)
    with pytest.raises(RuntimeError):
        retract(store, sk, "no-such-id", "no-such-cycle",
                generated="2026-06-15T00:00:00Z", sign=signing.sign, log=lambda *a: None)


def _sqlite_pack(tmp_path, id, kind, cycle, **dates):
    """A one-table sqlite pack with its pack_meta and a matching entry: the
    publish gate checks the entry's sha256 and size against the file."""
    p = tmp_path / f"{id}-{cycle}.pack"
    con = sqlite3.connect(str(p)); con.execute("CREATE TABLE t(x)"); con.commit(); con.close()
    meta = PackMeta(id=id, kind=kind, cycle=cycle, attribution=ATTR, **dates)
    embed_sqlite(p, meta)
    return PackEntry.from_pack(p, meta, url=f"{ORIGIN}/packs/{p.name}"), p


def _nav(tmp_path):
    return _sqlite_pack(tmp_path, "airports-conus", "navdata", "2606",
                        effective="2026-06-11", expires="2026-07-09")


def _list_directly(tmp_path, pairs):
    """Sign a manifest listing ``pairs`` without the publish gate: for a kind
    the gate refuses on principle (an ungated kind), where the test is about
    the device install path, not about publishing."""
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    m = Manifest.new("2026-06-14T00:00:00Z")
    for entry, path in pairs:
        store.put_file(f"packs/{path.name}", path)
        m.upsert(entry)
    store.put_bytes("manifest.json", m.to_bytes())
    store.put_bytes("manifest.json.minisig", signing.sign(m.to_bytes(), sk).encode("ascii"))
    return store, pub


def test_highways_install(tmp_path):
    """Roads/highways is the same sqlite-pack shape as water (OSM motorway/
    trunk overlay) — a distinct kind reusing the install path."""
    db = tmp_path / "highways.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE highway_lines (id INTEGER, cls TEXT)")
    con.execute("INSERT INTO highway_lines VALUES (1, 'motorway')")
    con.commit(); con.close()
    pack = tmp_path / "build" / "packs" / "highways-conus-2026q2.pack"
    pack.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, pack)
    meta = PackMeta(id="highways-conus", kind="highways", cycle="2026q2",
                    attribution="OpenStreetMap contributors (ODbL)")
    embed_sqlite(pack, meta)
    entry = PackEntry.from_pack(pack, meta, url=f"{ORIGIN}/packs/{pack.name}",
                                regions=["conus"])
    store, pub = _publish(tmp_path, [(entry, pack)])
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", regions=("conus",))
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)
    up.update()
    f = tmp_path / "pi" / "highways" / "current" / "highways.sqlite"
    assert f.exists()
    con = sqlite3.connect(str(f))
    assert con.execute("SELECT COUNT(*) FROM highway_lines").fetchone()[0] == 1
    con.close()
    st = {r.pack_id: r for r in up.status()}
    assert st["highways-conus"].status == "current"
    assert st["highways-conus"].name == "Roads & Highways"


def test_rivers_install(tmp_path):
    """Rivers is a distinct BULK kind reusing the highways sqlite-pack shape
    (OSM waterway lines, #92/#39) — separate current symlink + status label."""
    db = tmp_path / "rivers.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE highway_lines (id INTEGER, fclass TEXT)")
    con.execute("INSERT INTO highway_lines VALUES (1, 'river')")
    con.commit(); con.close()
    pack = tmp_path / "build" / "packs" / "rivers-conus-2026q2.pack"
    pack.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, pack)
    meta = PackMeta(id="rivers-conus", kind="rivers", cycle="2026q2",
                    attribution="OpenStreetMap contributors (ODbL)")
    embed_sqlite(pack, meta)
    entry = PackEntry.from_pack(pack, meta, url=f"{ORIGIN}/packs/{pack.name}",
                                regions=["conus"])
    # rivers is ungated (report.UNGATED): the publish gate refuses it, so
    # list it directly to test the install path.
    store, pub = _list_directly(tmp_path, [(entry, pack)])
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", regions=("conus",))
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)
    up.update()
    f = tmp_path / "pi" / "rivers" / "current" / "rivers.sqlite"
    assert f.exists()
    con = sqlite3.connect(str(f))
    assert con.execute("SELECT COUNT(*) FROM highway_lines").fetchone()[0] == 1
    con.close()
    st = {r.pack_id: r for r in up.status()}
    assert st["rivers-conus"].status == "current"
    assert st["rivers-conus"].name == "Rivers & Waterways"
