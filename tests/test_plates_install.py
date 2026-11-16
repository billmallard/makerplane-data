# SPDX-License-Identifier: Apache-2.0
"""Plates on the device (AER-5795): a d-TPP plates pack (kind=plates, one per
region) installs through the same verify-then-swap sqlite path as every other
pack, under plates/<pack_id>/current/plates.sqlite so several opted-in regions
coexist. pyEfis's Charts page points its plates_path at the plates/ directory.

Packs are listed directly (no publish gate): plates has never been published,
and these tests are about the device install path, not about publishing."""

import datetime as dt
import shutil
import sqlite3

from packtools import signing
from packtools.manifest import Manifest, PackEntry
from packtools.packmeta import PackMeta, embed_sqlite
from packtools.upload import LocalStore
from pyefis_data.config import Config
from pyefis_data.core import Updater, LocalDirRemote, CURRENT, MISSING

TODAY = dt.date(2026, 6, 14)
ORIGIN = "https://test.local"
CYCLE, EFFECTIVE, EXPIRES = "2606", "2026-06-11", "2026-07-09"


def _plates_pack(tmp_path, region, airport):
    """A minimal plates.sqlite (the PA11 tables, one row) packed as
    plates-<region>; returns (entry, pack_path)."""
    pid = f"plates-{region}"
    db = tmp_path / f"{pid}.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE plate_files (pdf_name TEXT PRIMARY KEY, sha256 TEXT,"
                " bytes INTEGER, pdf BLOB)")
    con.execute("CREATE TABLE plates (airport TEXT, chart_code TEXT,"
                " chart_name TEXT, pdf_name TEXT)")
    con.execute("INSERT INTO plate_files VALUES ('A.PDF', 'x', 1, x'25')")
    con.execute("INSERT INTO plates VALUES (?, 'IAP', 'RNAV (GPS) RWY 25', 'A.PDF')",
                (airport,))
    con.commit()
    con.close()
    pack = tmp_path / "build" / "packs" / f"{pid}-{CYCLE}.pack"
    pack.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, pack)
    meta = PackMeta(id=pid, kind="plates", cycle=CYCLE, effective=EFFECTIVE,
                    expires=EXPIRES, attribution="FAA d-TPP (public domain)")
    embed_sqlite(pack, meta)
    entry = PackEntry.from_pack(pack, meta, url=f"{ORIGIN}/packs/{pack.name}",
                                regions=[region])
    return entry, pack


def _store(tmp_path, pairs):
    store = LocalStore(tmp_path / "r2")
    sk, pub = signing.generate_keypair()
    m = Manifest.new("2026-06-14T00:00:00Z")
    for entry, path in pairs:
        store.put_file(f"packs/{path.name}", path)
        m.upsert(entry)
    store.put_bytes("manifest.json", m.to_bytes())
    store.put_bytes("manifest.json.minisig",
                    signing.sign(m.to_bytes(), sk).encode("ascii"))
    return store, pub


def _two_regions(tmp_path):
    return [_plates_pack(tmp_path, "us-west", "KSBA"),
            _plates_pack(tmp_path, "us-east", "KBOS")]


def _updater(tmp_path, store, pub, regions):
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", regions=regions)
    return Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)


def _airport_in(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT airport FROM plates").fetchone()[0]
    finally:
        con.close()


def test_plates_install_per_region_path(tmp_path):
    """Opting into two regions installs both, side by side, each under its own
    pack id -- one region must not overwrite the other."""
    store, pub = _store(tmp_path, _two_regions(tmp_path))
    up = _updater(tmp_path, store, pub, ("us-west", "us-east"))
    up.update()
    assert not up.errors
    base = tmp_path / "pi" / "plates"
    assert _airport_in(base / "plates-us-west" / "current" / "plates.sqlite") == "KSBA"
    assert _airport_in(base / "plates-us-east" / "current" / "plates.sqlite") == "KBOS"


def test_plates_are_opt_in_by_region(tmp_path):
    """Hundreds of MB a region: nothing installs by default, and opting into
    one region pulls only that region."""
    store, pub = _store(tmp_path, _two_regions(tmp_path))
    up = _updater(tmp_path, store, pub, ())
    up.update()
    assert not (tmp_path / "pi" / "plates").exists()

    up = _updater(tmp_path, store, pub, ("us-west",))
    up.update()
    assert (tmp_path / "pi" / "plates" / "plates-us-west" / "current"
            / "plates.sqlite").exists()
    assert not (tmp_path / "pi" / "plates" / "plates-us-east").exists()


def test_plates_status_reports_cycle(tmp_path):
    """The Data Status screen and status.json show the plates cycle and its
    window, like every other cyclical kind."""
    store, pub = _store(tmp_path, _two_regions(tmp_path))
    up = _updater(tmp_path, store, pub, ("us-west",))
    before = {r.pack_id: r for r in up.status()}
    assert before["plates-us-west"].status == MISSING
    up.update()
    row = {r.pack_id: r for r in up.status()}["plates-us-west"]
    assert row.status == CURRENT
    assert row.kind == "plates" and row.name == "Approach Plates"
    assert row.cycle == CYCLE
    assert (row.effective, row.expires) == (EFFECTIVE, EXPIRES)


def test_plates_sha_mismatch_installs_nothing(tmp_path):
    """Verified like every other signed pack: a pack whose bytes don't match
    the signed manifest's sha256 never reaches plates/."""
    pairs = _two_regions(tmp_path)
    store, pub = _store(tmp_path, pairs)
    tampered = store.root / "packs" / pairs[0][1].name
    tampered.write_bytes(tampered.read_bytes() + b"x")
    up = _updater(tmp_path, store, pub, ("us-west",))
    up.update()
    assert any("plates-us-west" in e and "sha256" in e for e in up.errors)
    assert not (tmp_path / "pi" / "plates" / "plates-us-west" / "current").exists()


def test_plates_remove_leaves_sibling_region(tmp_path):
    """Deselecting one region removes only that region's pack directory."""
    store, pub = _store(tmp_path, _two_regions(tmp_path))
    up = _updater(tmp_path, store, pub, ("us-west", "us-east"))
    up.update()
    up.remove_pack("plates-us-west", "plates")
    base = tmp_path / "pi" / "plates"
    assert not (base / "plates-us-west").exists()
    assert (base / "plates-us-east" / "current" / "plates.sqlite").exists()
