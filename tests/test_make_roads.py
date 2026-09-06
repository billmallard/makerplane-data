# SPDX-License-Identifier: Apache-2.0
"""CONUS roads-pack fetch orchestration (AER-623, RD3). No network: the
downloader is always injected, same DI convention run_cyclical uses for its
fetcher (packtools/run_cyclical.py)."""

import zipfile

import pytest

from packtools import make_roads
from packtools.build import BuildError


def test_parse_states_conus_includes_california_split():
    states = make_roads.parse_states("conus")
    assert "california/norcal" in states
    assert "california/socal" in states
    assert "california" not in states   # the bare slug 302s -- makerplane-data#17
    assert len(states) == len(make_roads.CONUS_STATES)


def test_parse_states_custom_list_normalizes_separators():
    assert make_roads.parse_states("New York, Rhode_Island") == \
        ["new-york", "rhode-island"]


def _write_state_zip(path, layers=make_roads.ROAD_LAYER_EXTS, layer=make_roads.ROAD_LAYER):
    with zipfile.ZipFile(path, "w") as zf:
        for ext in layers:
            zf.writestr(f"{layer}{ext}", b"fake-layer-bytes")
        zf.writestr("gis_osm_buildings_a_free_1.shp", b"not the layer we want")


def test_fetch_state_uses_cache_without_downloading(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / "colorado-latest-free.shp.zip"
    cached.write_bytes(b"PK\x03\x04already-here")

    calls = []
    result = make_roads.fetch_state(
        "colorado", cache_dir,
        downloader=lambda url, dest: calls.append((url, dest)))

    assert result == cached
    assert calls == []   # cached + non-empty -> never touches the network


def test_fetch_state_rejects_geofabrik_redirect_page(tmp_path):
    """Geofabrik answers an unknown/discontinued state slug with a 302 to its
    homepage, not a 404 -- a bare 'california' entry lands here as a small
    HTML file instead of a zip (makerplane-data#17: this is exactly how the
    June 2026 roads build silently shipped zero California roads)."""
    cache_dir = tmp_path / "cache"

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"<html>not found, redirected to homepage</html>")

    with pytest.raises(BuildError, match="non-zip"):
        make_roads.fetch_state("california", cache_dir, downloader=fake_download)

    # the bad download must not linger and be mistaken for a good cache hit
    assert not (cache_dir / "california-latest-free.shp.zip").exists()


def test_extract_road_layer_pulls_expected_files(tmp_path):
    zip_path = tmp_path / "colorado-latest-free.shp.zip"
    _write_state_zip(zip_path)
    dest_dir = tmp_path / "extracted"

    shp = make_roads.extract_road_layer(zip_path, dest_dir)

    assert shp == dest_dir / "colorado" / "gis_osm_roads_free_1.shp"
    assert shp.exists()
    for ext in make_roads.ROAD_LAYER_EXTS:
        assert (dest_dir / "colorado" / f"gis_osm_roads_free_1{ext}").exists()
    # the layer we don't want never gets copied out
    assert not (dest_dir / "colorado" / "gis_osm_buildings_a_free_1.shp").exists()


def test_extract_road_layer_split_state_uses_flattened_dirname(tmp_path):
    zip_path = tmp_path / "california-norcal-latest-free.shp.zip"
    _write_state_zip(zip_path)

    shp = make_roads.extract_road_layer(zip_path, tmp_path / "extracted")

    assert shp.parent.name == "california-norcal"


def test_extract_road_layer_missing_layer_is_a_hard_failure(tmp_path):
    """An empty/wrong extract (makerplane-data#17's failure mode) must come
    back as None, never as a shp path that quietly has no data in it."""
    zip_path = tmp_path / "colorado-latest-free.shp.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("gis_osm_buildings_a_free_1.shp", b"wrong layer entirely")

    assert make_roads.extract_road_layer(zip_path, tmp_path / "extracted") is None


def test_fetch_all_lists_every_failed_state_not_just_the_first(tmp_path):
    def flaky_download(url, dest):
        if "texas" in url:
            raise RuntimeError("connection reset")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_state_zip(dest)

    with pytest.raises(BuildError) as exc:
        make_roads.fetch_all(["colorado", "texas", "wyoming"], tmp_path / "cache",
                             downloader=flaky_download, log=lambda *a: None)

    # texas failed; colorado/wyoming succeeding must not hide that in a
    # partial-success pack (makerplane-data#17's whole lesson)
    assert "texas" in str(exc.value)
    assert "colorado" not in str(exc.value)


def test_fetch_all_deletes_zip_after_extraction_by_default(tmp_path):
    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_state_zip(dest)

    cache_dir = tmp_path / "cache"
    shp_paths = make_roads.fetch_all(["colorado"], cache_dir,
                                     downloader=fake_download, log=lambda *a: None)

    assert len(shp_paths) == 1
    assert not (cache_dir / "colorado-latest-free.shp.zip").exists()
    assert shp_paths[0].exists()   # the extracted layer survives


def test_build_highways_invokes_pyefis_builder_with_every_state(tmp_path, monkeypatch):
    extracted = tmp_path / "cache" / "extracted"
    for state in ("colorado", "texas"):
        d = extracted / state
        d.mkdir(parents=True)
        (d / "gis_osm_roads_free_1.shp").write_bytes(b"fake")

    calls = []
    monkeypatch.setattr("packtools.build._run_tool",
                        lambda script, args: calls.append((script, args)))

    from packtools.build import build_highways
    dest = tmp_path / "highways.sqlite"
    build_highways(extracted, dest)

    assert len(calls) == 1
    script, args = calls[0]
    assert script == "build_highway_db.py"
    assert "--overwrite" in args
    assert str(extracted / "colorado" / "gis_osm_roads_free_1.shp") in args
    assert str(extracted / "texas" / "gis_osm_roads_free_1.shp") in args


def test_build_highways_refuses_empty_input(tmp_path):
    with pytest.raises(BuildError, match="no gis_osm_roads_free_1.shp"):
        make_roads.build_highways(tmp_path / "empty", tmp_path / "out.sqlite")
