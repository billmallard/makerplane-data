# SPDX-License-Identifier: Apache-2.0
"""Terrain pipeline: build region packs from an HGT tree, then install them
through the real Pi updater (download -> verify -> unzip-merge)."""

import datetime as dt
import json
import zipfile

import numpy as np
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


def _add_mip_pyramid(root, names, levels=2):
    """Fake coarse .mip/<L>/<NSdir>/<name>.hgt tiles alongside the native tree."""
    for n in names:
        for level in range(1, levels + 1):
            d = root / ".mip" / str(level) / _ns(n)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{n}.hgt").write_bytes(b"\x0a\x0b" * (8 // level or 1))
    return root


def test_pack_carries_mip_pyramid_and_updater_installs_it(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mip_pyramid(src, US_WEST_TILES, levels=2)
    # native tiles are still found; the .mip tree is NOT treated as native
    assert set(make_terrain.find_tiles(src)) == {"N32W120", "N33W121"}
    store = LocalStore(tmp_path / "r2")
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    assert packs[0].tile_count == 2                       # native count, not inflated by mips
    with zipfile.ZipFile(packs[0].path) as z:
        names_in = set(z.namelist())
    assert "N32/N32W120.hgt" in names_in                  # native
    assert ".mip/1/N32/N32W120.hgt" in names_in           # coarse levels ride along
    assert ".mip/2/N33/N33W121.hgt" in names_in
    # install through the real updater: the pyramid lands in the tile tree
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    make_updater(tmp_path, pub, store.root).update()
    tiles = tmp_path / "pi" / "terrain" / "tiles"
    assert (tiles / "N32" / "N32W120.hgt").exists()
    assert (tiles / ".mip" / "1" / "N32" / "N32W120.hgt").exists()
    assert (tiles / ".mip" / "2" / "N33" / "N33W121.hgt").exists()


def _add_mosaic(root, levels=(4, 5, 6)):
    """Fake .mip/mosaic/L<level>.{hgt,json} as build_terrain_mosaic.py writes
    them (a 2deg CONUS-ish window: lat 32..34, lon -121..-119)."""
    mdir = root / ".mip" / "mosaic"
    mdir.mkdir(parents=True, exist_ok=True)
    for lv in levels:
        spd = 225 >> (lv - 4)                              # coarser levels, smaller spd
        rows = cols = 2 * spd + 1                          # 2deg span
        (mdir / f"L{lv}.hgt").write_bytes(b"\x00\x64" * (rows * cols))
        (mdir / f"L{lv}.json").write_text(json.dumps(
            {"level": lv, "rows": rows, "cols": cols, "spd": spd,
             "lat_n": 34, "lon_w": -121}))
    return root


def test_build_mosaic_pack_shape(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mosaic(src)
    tp = make_terrain.build_mosaic_pack(
        src, out_dir=tmp_path / "b", edition="2024ed", url_base=f"{ORIGIN}/packs")
    assert tp is not None
    assert tp.entry.id == "terrain-mosaic" and tp.entry.kind == "terrain"
    assert tp.entry.regions == ["mosaic"]                  # synthetic region, isolates provenance
    assert tp.entry.effective is None and tp.entry.expires is None
    assert tp.entry.tiles_bbox == [32, -121, 34, -119]     # derived from the L*.json sidecar
    with zipfile.ZipFile(tp.path) as z:
        names = set(z.namelist())
    assert ".mip/mosaic/L4.hgt" in names and ".mip/mosaic/L4.json" in names
    assert ".mip/mosaic/L6.hgt" in names                   # every level rides in one pack
    assert "pack_meta.json" in names
    assert "N32/N32W120.hgt" not in names                  # NOT the native tiles


def test_build_mosaic_pack_absent_returns_none(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)   # no .mip/mosaic built
    assert make_terrain.build_mosaic_pack(
        src, out_dir=tmp_path / "b", edition="2024ed", url_base=f"{ORIGIN}/packs") is None


def test_updater_installs_mosaic_pack_by_explicit_id(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mosaic(src)
    store = LocalStore(tmp_path / "r2")
    tp = make_terrain.build_mosaic_pack(
        src, out_dir=tmp_path / "build", edition="2024ed", url_base=f"{ORIGIN}/packs")
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, [tp], generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    # A device opts in with an explicit pack id (always tracked), no region needed.
    cfg = Config(base_url=ORIGIN, root=tmp_path / "pi", packs=("terrain-mosaic",))
    up = Updater(cfg, pub, remote=LocalDirRemote(store.root), today=TODAY)
    assert up._tracked_ids(Manifest.from_bytes(store.get_bytes("manifest.json"))) == ["terrain-mosaic"]
    up.update()
    tiles = tmp_path / "pi" / "terrain" / "tiles"
    assert (tiles / ".mip" / "mosaic" / "L4.hgt").exists()
    assert (tiles / ".mip" / "mosaic" / "L4.json").exists()
    assert not (tiles / "pack_meta.json").exists()         # metadata not unpacked into the tree


def test_terrain_only_tracked_when_region_opted_in(tmp_path):
    store, pub, _ = build_store_with_terrain(tmp_path)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    # default config (no regions) does NOT track terrain
    no_region = Updater(Config(base_url=ORIGIN, root=tmp_path / "p0"), pub, today=TODAY)
    assert no_region._tracked_ids(m) == []
    # opting into us-west tracks it
    yes = make_updater(tmp_path, pub, store.root)
    assert yes._tracked_ids(m) == ["terrain-us-west"]


def test_mosaic_auto_tracked_with_any_terrain_region(tmp_path):
    """The national terrain-mosaic pack is pulled automatically whenever any real
    terrain region is tracked -- no separate opt-in -- but never on its own."""
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mosaic(src)
    store = LocalStore(tmp_path / "r2")
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    packs.append(make_terrain.build_mosaic_pack(
        src, out_dir=tmp_path / "build", edition="2024ed", url_base=f"{ORIGIN}/packs"))
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    m = Manifest.from_bytes(store.get_bytes("manifest.json"))
    # no terrain regions -> mosaic NOT tracked (it only rides along)
    none_up = Updater(Config(base_url=ORIGIN, root=tmp_path / "p0"), pub, today=TODAY)
    assert none_up._tracked_ids(m) == []
    # opting into us-west auto-adds the mosaic
    yes = make_updater(tmp_path, pub, store.root)
    assert yes._tracked_ids(m) == ["terrain-mosaic", "terrain-us-west"]


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


def test_deselecting_terrain_region_removes_its_tiles(tmp_path):
    """Unchecking a terrain region removes its exclusive tiles (keeping any a
    remaining region still provides); deselecting all reclaims the whole tree."""
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
    piroot = tmp_path / "pi"
    tiles = piroot / "terrain" / "tiles"

    def updater(regions):
        return Updater(Config(base_url=ORIGIN, root=piroot, regions=regions),
                       pub, remote=LocalDirRemote(store.root), today=TODAY)

    updater(("us-west", "us-central")).update()         # install both
    assert (tiles / "N32" / "N32W120.hgt").exists() and (tiles / "N35" / "N35W095.hgt").exists()

    up2 = updater(("us-west",))                          # deselect us-central
    up2.update()
    assert (tiles / "N32" / "N32W120.hgt").exists()      # us-west kept
    assert not (tiles / "N35" / "N35W095.hgt").exists()  # us-central tile gone
    assert up2.inventory.get("terrain-us-central") is None
    assert set(json.loads((tiles / ".regions.json").read_text())) == {"us-west"}

    updater(()).update()                                 # deselect all terrain
    assert not tiles.exists()                            # whole tree reclaimed


def _write_wmask(hgt_path, side):
    """Fixture-only stand-in for pyEfis MP10a's build_water_masks.py: a
    row-packed 1-bit-per-pixel bitmask, no header, written as ``<name>.wmask``
    next to its ``.hgt`` sibling. Content is arbitrary here -- this pins that
    make_terrain rides the file through verbatim, not the mask math itself."""
    bits = np.zeros((side, side), dtype=np.uint8)
    bits[side // 2:, side // 2:] = 1
    content = np.packbits(bits, axis=1).tobytes()
    hgt_path.with_suffix(".wmask").write_bytes(content)
    return content


def test_region_pack_rides_wmask_siblings_for_native_and_mip_tiles(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mip_pyramid(src, US_WEST_TILES, levels=2)
    expected = {}
    for n in US_WEST_TILES:
        ns = _ns(n)
        expected[f"{ns}/{n}.wmask"] = _write_wmask(src / ns / f"{n}.hgt", side=3601)
        for level in (1, 2):
            mp = src / ".mip" / str(level) / ns / f"{n}.hgt"
            expected[f".mip/{level}/{ns}/{n}.wmask"] = _write_wmask(mp, side=3600 // (2 ** level) + 1)

    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    with zipfile.ZipFile(packs[0].path) as z:
        names = set(z.namelist())
        for arcname, content in expected.items():
            assert arcname in names
            assert z.read(arcname) == content      # byte-identical, no re-encoding


def test_mosaic_pack_rides_wmask_siblings(tmp_path):
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mosaic(src)
    expected = {}
    for lv in (4, 5, 6):
        spd = 225 >> (lv - 4)
        expected[f".mip/mosaic/L{lv}.wmask"] = _write_wmask(
            src / ".mip" / "mosaic" / f"L{lv}.hgt", side=2 * spd + 1)

    tp = make_terrain.build_mosaic_pack(
        src, out_dir=tmp_path / "b", edition="2024ed", url_base=f"{ORIGIN}/packs")
    with zipfile.ZipFile(tp.path) as z:
        names = set(z.namelist())
        for arcname, content in expected.items():
            assert arcname in names
            assert z.read(arcname) == content


def test_packs_unchanged_when_wmask_absent(tmp_path):
    """Absent masks are a permanently supported state (an edition built before
    MP10b, or a level MP10a didn't cover): no .wmask member appears and the
    rest of the pack is exactly what it was before this channel existed."""
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _add_mip_pyramid(src, US_WEST_TILES, levels=2)
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    with zipfile.ZipFile(packs[0].path) as z:
        names = set(z.namelist())
    assert not any(n.endswith(".wmask") for n in names)
    assert names == {
        "N32/N32W120.hgt", "N33/N33W121.hgt",
        ".mip/1/N32/N32W120.hgt", ".mip/1/N33/N33W121.hgt",
        ".mip/2/N32/N32W120.hgt", ".mip/2/N33/N33W121.hgt",
        "pack_meta.json",
    }

    src2 = make_hgt_tree(tmp_path / "hgt2", US_WEST_TILES)
    _add_mosaic(src2)
    tp = make_terrain.build_mosaic_pack(
        src2, out_dir=tmp_path / "b2", edition="2024ed", url_base=f"{ORIGIN}/packs")
    with zipfile.ZipFile(tp.path) as z:
        names2 = set(z.namelist())
    assert not any(n.endswith(".wmask") for n in names2)
    assert names2 == {
        ".mip/mosaic/L4.hgt", ".mip/mosaic/L4.json",
        ".mip/mosaic/L5.hgt", ".mip/mosaic/L5.json",
        ".mip/mosaic/L6.hgt", ".mip/mosaic/L6.json",
        "pack_meta.json",
    }


def test_installer_installs_wmask_siblings_alongside_tiles(tmp_path):
    """End-to-end through the real Pi updater: a .wmask rides into the tile
    tree exactly like its .hgt sibling (no core.py change -- the installer
    already extracts every zip member but pack_meta.json)."""
    src = make_hgt_tree(tmp_path / "hgt", US_WEST_TILES)
    _write_wmask(src / _ns(US_WEST_TILES[0]) / f"{US_WEST_TILES[0]}.hgt", side=3601)
    store = LocalStore(tmp_path / "r2")
    packs = make_terrain.make_terrain_packs(
        src_root=src, out_dir=tmp_path / "build", edition="2024ed",
        url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
    sk, pub = signing.generate_keypair()
    make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                 sign=signing.sign, log=lambda *a: None)
    up = make_updater(tmp_path, pub, store.root)
    up.update()
    assert up.errors == []                              # verify-then-swap raised nothing
    tiles = tmp_path / "pi" / "terrain" / "tiles"
    ns = _ns(US_WEST_TILES[0])
    assert (tiles / ns / f"{US_WEST_TILES[0]}.wmask").exists()
    assert not (tiles / _ns(US_WEST_TILES[1]) / f"{US_WEST_TILES[1]}.wmask").exists()


def test_manifest_signature_verifies_with_and_without_masks(tmp_path):
    """The signed-manifest contract (docs/data_manager_implementation.md) is
    untouched by the ride-along channel: `packtool verify`'s check
    (`signing.verify_file`) passes for a manifest covering a masked pack and
    one covering an unmasked pack, identically."""
    for label, add_mask in (("masked", True), ("bare", False)):
        src = make_hgt_tree(tmp_path / f"hgt-{label}", US_WEST_TILES)
        if add_mask:
            _write_wmask(src / _ns(US_WEST_TILES[0]) / f"{US_WEST_TILES[0]}.hgt", side=3601)
        store = LocalStore(tmp_path / f"r2-{label}")
        packs = make_terrain.make_terrain_packs(
            src_root=src, out_dir=tmp_path / f"build-{label}", edition="2024ed",
            url_base=f"{ORIGIN}/packs", only_regions=["us-west"], log=lambda *a: None)
        sk, pub = signing.generate_keypair()
        make_terrain.update_manifest(store, sk, packs, generated="2026-06-14T00:00:00Z",
                                     sign=signing.sign, log=lambda *a: None)
        trusted = signing.verify_file(store.root / "manifest.json", pub)
        assert trusted   # non-empty trusted comment -> signature verified OK


def test_corrupt_terrain_pack_leaves_tree_untouched(tmp_path):
    store, pub, packs = build_store_with_terrain(tmp_path)
    # tamper the pack bytes in the store
    pk = store.root / "packs" / packs[0].path.name
    pk.write_bytes(pk.read_bytes() + b"corrupt")
    up = make_updater(tmp_path, pub, store.root)
    up.update()
    assert up.errors                                    # sha mismatch recorded
    assert not (tmp_path / "pi" / "terrain" / "tiles" / "N32").exists()
