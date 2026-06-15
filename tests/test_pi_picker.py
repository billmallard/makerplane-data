"""On-device pack picker backend (Phase 1): the full-catalog listing the
picker shows, and persisting the user's selection to data.yaml so the picker
replaces hand-editing the file.

These are pure unit tests over a hand-built manifest (no network, no signing);
the end-to-end CLI path (--only / --source against real signed packs) lives in
test_pi_updater.py."""

import datetime as dt
from pathlib import Path

from packtools.manifest import Manifest, PackEntry
from pyefis_data.config import Config, write_config
from pyefis_data.core import Updater

TODAY = dt.date(2026, 6, 15)


def _manifest():
    m = Manifest.new("2026-06-15T00:00:00Z")
    m.upsert(PackEntry(id="airports-conus", kind="navdata", cycle="2606",
                       bytes=5_000_000, sha256="a" * 64,
                       url="https://x/packs/airports-conus-2606.pack",
                       effective="2026-06-11", expires="2026-07-09"))
    m.upsert(PackEntry(id="obstacles-conus", kind="obstacles", cycle="260611",
                       bytes=75_000_000, sha256="a" * 64,
                       url="https://x/packs/obstacles-conus-260611.pack",
                       effective="2026-06-11", expires="2026-08-06"))
    m.upsert(PackEntry(id="terrain-us-west", kind="terrain", cycle="2024ed",
                       bytes=10_000_000_000, sha256="a" * 64,
                       url="https://x/packs/terrain-us-west-2024ed.pack",
                       regions=["us-west"]))
    m.upsert(PackEntry(id="water-na", kind="water", cycle="2026q2",
                       bytes=2_460_000_000, sha256="a" * 64,
                       url="https://x/packs/water-na-2026q2.pack",
                       regions=["conus", "us-west"]))
    return m


def _updater(tmp_path, **cfgkw):
    up = Updater(Config(base_url="https://x", root=tmp_path / "pi", **cfgkw),
                 "pub", today=TODAY)
    up.fetch_manifest = lambda remote=None: _manifest()   # no net / no signing
    return up


def test_catalog_lists_all_packs_with_picker_fields(tmp_path):
    rows = _updater(tmp_path).catalog()            # fresh defaults
    by = {r["id"]: r for r in rows}
    assert set(by) == {"airports-conus", "obstacles-conus",
                       "terrain-us-west", "water-na"}
    # full catalog surfaces bulk packs the default config does NOT track
    assert by["terrain-us-west"]["tracked"] is False
    assert by["airports-conus"]["tracked"] is True
    # picker metadata
    t = by["terrain-us-west"]
    assert t["bytes"] == 10_000_000_000 and t["regions"] == ["us-west"]
    assert t["kind"] == "terrain" and t["name"] == "Terrain"
    assert by["water-na"]["name"] == "Water"
    assert all(r["installed"] is False for r in rows)      # nothing installed yet
    # grouped order: (kind, id)
    assert [r["id"] for r in rows] == [
        "airports-conus", "obstacles-conus", "terrain-us-west", "water-na"]


def test_catalog_tracked_follows_region_opt_in(tmp_path):
    by = {r["id"]: r for r in _updater(tmp_path, regions=("us-west",)).catalog()}
    assert by["terrain-us-west"]["tracked"] is True        # region opted in
    assert by["water-na"]["tracked"] is True                # water-na is tagged us-west


def test_catalog_explicit_packs_tracked(tmp_path):
    by = {r["id"]: r for r in
          _updater(tmp_path, packs=("water-na",), track_kinds=()).catalog()}
    assert by["water-na"]["tracked"] is True
    assert by["airports-conus"]["tracked"] is False        # core not tracked when overridden


def test_catalog_reflects_installed(tmp_path):
    up = _updater(tmp_path)
    up.inventory.set_current("airports-conus", "2606", "a" * 64, kind="navdata")
    by = {r["id"]: r for r in up.catalog()}
    assert by["airports-conus"]["installed"] is True
    assert by["terrain-us-west"]["installed"] is False


def test_write_config_persists_and_merges(tmp_path):
    p = tmp_path / "data.yaml"
    write_config(p, {"packs": ["airports-conus", "water-na"],
                     "track_kinds": [], "regions": []})
    cfg = Config.load(p)
    assert cfg.packs == ("airports-conus", "water-na")
    assert cfg.track_kinds == () and cfg.regions == ()
    # a later write merges, preserving earlier keys
    write_config(p, {"root": "/data/makerplane-data"})
    cfg2 = Config.load(p)
    assert cfg2.packs == ("airports-conus", "water-na")    # preserved
    assert cfg2.root == Path("/data/makerplane-data")


def test_write_config_creates_missing_file(tmp_path):
    p = tmp_path / "sub" / "data.yaml"
    write_config(p, {"packs": ["obstacles-conus"]})
    assert p.exists()
    assert Config.load(p).packs == ("obstacles-conus",)
