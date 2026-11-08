# SPDX-License-Identifier: Apache-2.0
"""scripts/make_glo30_tiles.py: the GLO-30 fetch window and tree check
(AER-4706: the source tree was fetched for a window narrower than the
regions, and 89 tiles inside alaska and mexico-central-america never
shipped)."""

import importlib.util
from pathlib import Path

from packtools.regions import load_regions, tile_sw_corner

_SPEC = importlib.util.spec_from_file_location(
    "make_glo30_tiles", Path(__file__).resolve().parent.parent / "scripts" / "make_glo30_tiles.py")
mk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mk)

# What the NAS tree was fetched with: fetch_glo30.py's defaults.
OLD_WINDOW = (14, 72, -170, -52)


def _regions():
    return [r for k, r in load_regions().items() if k != "conus"]


def _inside(tile, window):
    lat0, lat1, lon0, lon1 = window
    lat, lon = tile_sw_corner(tile)
    return lat0 <= lat <= lat1 and lon0 <= lon <= lon1


def test_fetch_window_covers_every_listed_tile():
    win = mk.fetch_window(_regions())
    assert [t for t in mk.listed_tiles() if not _inside(t, win)] == []


def test_the_old_fetch_window_misses_tiles_inside_the_regions():
    left_out = [t for t in mk.listed_tiles() if not _inside(t, OLD_WINDOW)]
    assert "N51W177" in left_out   # Adak/Atka side of the alaska widening
    assert "N09W080" in left_out   # Panama
    assert len(left_out) == 89     # 41 alaska + 48 mexico-central-america


def test_fetch_window_is_tight_to_the_exclusive_bbox_edges():
    class R:
        bbox = (7, -177, 72, -52)
    assert mk.fetch_window([R]) == (7, 71, -177, -53)


def test_missing_from_tree(tmp_path):
    (tmp_path / "N51").mkdir()
    (tmp_path / "N51" / "N51W177.hgt").write_bytes(b"\0\0")
    assert mk.missing_from_tree(["N51W177", "N09W080"], tmp_path) == ["N09W080"]


def test_check_tree_exits_nonzero_on_a_gap(tmp_path, capsys):
    out = tmp_path / "tiles.txt"
    out.write_text(f"# header\n{mk.regions_line(_regions())}\nN51W177\nN09W080\n",
                   encoding="ascii")
    (tmp_path / "N51").mkdir()
    (tmp_path / "N51" / "N51W177.hgt").write_bytes(b"\0\0")
    assert mk.main(["--out", str(out), "--check-tree", str(tmp_path)]) == 1
    assert capsys.readouterr().out.split() == ["N09W080", "mexico-central-america"]
    (tmp_path / "N09").mkdir()
    (tmp_path / "N09" / "N09W080.hgt").write_bytes(b"\0\0")
    assert mk.main(["--out", str(out), "--check-tree", str(tmp_path)]) == 0


# AER-5175: the list is the fetch area (fetch_glo30.py --tiles-file) and
# TER-007's expected set, so a region widened without a rerun would fetch
# too little and still pass TER-007 on a short pack.

def test_tile_list_was_built_for_the_regions_in_regions_yaml():
    assert mk.regions_past_list(_regions()) == []


def test_a_region_reaching_past_the_list_is_named(tmp_path):
    from packtools.regions import Region
    out = tmp_path / "tiles.txt"
    out.write_text(mk.regions_line(_regions()) + "\nN51W177\n", encoding="ascii")
    regions = {r.key: r for r in _regions()}
    lat0, lon0, lat1, lon1 = regions["alaska"].bbox
    wider = Region("alaska", "Alaska", (lat0, lon0 - 1, lat1, lon1))
    narrower = Region("alaska", "Alaska", (lat0 + 1, lon0, lat1, lon1))
    new = Region("hawaii", "Hawaii", (18, -161, 23, -154))
    assert mk.regions_past_list([wider, new], out) == ["alaska", "hawaii"]
    assert mk.regions_past_list([narrower], out) == []
    out.write_text("N51W177\n", encoding="ascii")    # a list with no header
    assert mk.regions_past_list([narrower], out) == ["alaska"]


def test_check_tree_refuses_a_list_built_for_narrower_regions(tmp_path, capsys):
    out = tmp_path / "tiles.txt"
    out.write_text("# regions: alaska=51,-170,72,-129\nN51W170\n", encoding="ascii")
    assert mk.main(["--out", str(out), "--check-tree", str(tmp_path)]) == 2
    assert "alaska" in capsys.readouterr().err
