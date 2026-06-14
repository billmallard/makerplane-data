"""Terrain pipeline: build region packs from an HGT tree, then install them
through the real Pi updater (download -> verify -> unzip-merge)."""

import datetime as dt
import json
import zipfile

import pytest

from packtools import make_terrain, signing
from packtools.manifest import Manifest
from packtools.upload import LocalStore
from pyefis_data.config import Config
from pyefis_data.core import Updater, LocalDirRemote, VerificationError

TODAY = dt.date(2026, 6, 14)
ORIGIN = "https://test.local"
# Tiles whose SW corners fall in the us-west region (bbox 31,-125,49,-102).
US_WEST_TILES = ["N32W120", "N33W121"]


def _ns(name):
    return make_terrain._ns_dir(name)


def make_hgt_tree(root, names):
    for n in names:
        d = root / _ns(n)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{n}.hgt").write_bytes(b"\x00\x01\x02\x03" * 64)   # tiny fake tile
    return root


def build_store_with_terrain(tmp_path, names=US_WEST_TILES):
    src = make_hgt_tree(tmp_path / "hgt", names)
    store = LocalStore(tmp_path / "r2")
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    return store, pub, packs


def make_updater(tmp_path, pub, store_root, regions=("us-west",)):
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", regions=regions)
    return Updater(cfg, pub, remote=LocalDirRemote(store_root), today=TODAY)


def test_build_region_pack_shape(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "b", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    assert len(packs) == 1
    tp = packs[0]
    assert tp.region == "us-west" and tp.tile_count == 2
    assert tp.entry.kind == "terrain"
    assert tp.entry.effective is None and tp.entry.expires is None   # non-cyclical
    assert tp.entry.regions == ["us-west"]
    assert tp.entry.tiles_bbox == [32, -121, 34, -119]               # SW corners +1deg
    with zipfile.ZipFile(tp.path) as z:
        names = set(z.namelist())
    assert "N32/N32W120.hgt" in names and "N33/N33W121.hgt" in names
    assert "pack_meta.json" in names


def test_terrain_only_tracked_when_region_opted_in(tmp_path):
    store, pub, _ = build_store_with_terrain(tmp_path)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    # default config (no regions) does NOT track terrain
    no_region = Updater(Config(base_url=ORIGIN, root=tmp_path / "p0"), pub, today=TODAY)
    assert no_region._tracked_ids(m) == []
    # opting into us-west tracks it
    yes = make_updater(tmp_path, pub, store.root)
    assert yes._tracked_ids(m) == ["terrain-us-west"]


def test_install_merges_tiles_and_records_region(tmp_path):
    store, pub, _ = build_store_with_terrain(tmp_path)
    up = make_updater(tmp_path, pub, store.root)
    up.update()
    tiles = tmp_path / "pi" / "terrain" / "tiles"
    assert (tiles / "N32" / "N32W120.hgt").exists()
    assert (tiles / "N33" / "N33W121.hgt").exists()
    assert not (tiles / "pack_meta.json").exists()      # metadata not unpacked into the tree
    regs = json.loads((tiles / ".regions.json").read_text())
    assert regs["us-west"] == "2024ed"
    # status: terrain is non-cyclical -> current once installed
    st = {r.pack_id: r for r in up.status()}
    assert st["terrain-us-west"].status == "current"
    assert st["terrain-us-west"].kind == "terrain"


def test_two_regions_union_into_one_tree(tmp_path):
    # us-west + a us-central tile; opting into both unions the tree.
    names = US_WEST_TILES + ["N35W095"]                 # N35W095 -> us-central
    src = make_hgt_tree(tmp_path / "hgt", names)
    store = LocalStore(tmp_path / "r2")
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west", "us-central"],
        log=lambda *a: None)
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    up = make_updater(tmp_path, pub, store.root, regions=("us-west", "us-central"))
    up.update()
    tiles = tmp_path / "pi" / "terrain" / "tiles"
    assert (tiles / "N32" / "N32W120.hgt").exists()     # us-west
    assert (tiles / "N35" / "N35W095.hgt").exists()     # us-central
    regs = json.loads((tiles / ".regions.json").read_text())
    assert set(regs) == {"us-west", "us-central"}


def test_corrupt_terrain_pack_leaves_tree_untouched(tmp_path):
    store, pub, packs = build_store_with_terrain(tmp_path)
    # tamper the pack bytes in the store
    pk = store.root / "packs" / packs[0].path.name
    pk.write_bytes(pk.read_bytes() + b"corrupt")
    up = make_updater(tmp_path, pub, store.root)
    up.update()
    assert up.errors                                    # sha mismatch recorded
    assert not (tmp_path / "pi" / "terrain" / "tiles" / "N32").exists()
