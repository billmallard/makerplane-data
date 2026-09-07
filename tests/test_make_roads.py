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


def test_fetch_state_retries_transient_network_error(tmp_path):
    """502/503/read-timeout from Geofabrik mid-CONUS-build (makerplane-data#60)
    is routine, not a real bad slug -- it must not cost the whole ~80-minute
    build a hard failure the way test_fetch_state_rejects_geofabrik_redirect_page's
    bad-slug case correctly does."""
    cache_dir = tmp_path / "cache"
    attempts = []
    sleeps = []

    def flaky_then_ok(url, dest):
        attempts.append(url)
        if len(attempts) < 3:
            raise ConnectionError("502 Server Error: Bad Gateway")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"PK\x03\x04already-here")

    result = make_roads.fetch_state(
        "colorado", cache_dir, downloader=flaky_then_ok,
        sleep=lambda s: sleeps.append(s))

    assert result == cache_dir / "colorado-latest-free.shp.zip"
    assert len(attempts) == 3
    assert len(sleeps) == 2   # slept between attempts 1->2 and 2->3, not after


def test_fetch_state_gives_up_after_exhausting_retries(tmp_path):
    cache_dir = tmp_path / "cache"

    def always_fails(url, dest):
        raise ConnectionError("503 Server Error: Service Unavailable")

    with pytest.raises(ConnectionError):
        make_roads.fetch_state(
            "idaho", cache_dir, downloader=always_fails, retries=2,
            sleep=lambda s: None)

    # a failed attempt must not leave a bogus file behind for the next run
    assert not (cache_dir / "idaho-latest-free.shp.zip").exists()


def test_fetch_state_does_not_retry_non_network_failures(tmp_path):
    """The redirect-page case is a real bad slug, not a transient blip --
    retrying it would just burn time re-fetching the same wrong page."""
    cache_dir = tmp_path / "cache"
    calls = []

    def fake_download(url, dest):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"<html>not found, redirected to homepage</html>")

    with pytest.raises(BuildError, match="non-zip"):
        make_roads.fetch_state("california", cache_dir, downloader=fake_download,
                               sleep=lambda s: (_ for _ in ()).throw(
                                   AssertionError("should not sleep/retry")))

    assert len(calls) == 1


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
                             downloader=flaky_download, log=lambda *a: None,
                             sleep=lambda *a: None)

    # texas failed on both the first pass and the retry pass; colorado/wyoming
    # succeeding must not hide that in a partial-success pack
    # (makerplane-data#17's whole lesson)
    assert "texas" in str(exc.value)
    assert "colorado" not in str(exc.value)


def test_fetch_all_retries_failed_states_once_after_a_pause(tmp_path):
    """makerplane-data#60: a state that 502s on the first pass but recovers
    by the time the rest of the run has finished must not sink the whole
    build -- fetch_all gives it a second try after retry_pause, not just
    fetch_state's own same-minute backoff."""
    attempts = {"colorado": 0}

    def recovers_on_retry(url, dest):
        if "colorado" in url:
            attempts["colorado"] += 1
            if attempts["colorado"] == 1:
                raise RuntimeError("502 Bad Gateway")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_state_zip(dest)

    paused = []
    shp_paths = make_roads.fetch_all(
        ["colorado", "texas"], tmp_path / "cache", downloader=recovers_on_retry,
        log=lambda *a: None, sleep=paused.append)

    assert len(shp_paths) == 2
    assert attempts["colorado"] == 2
    assert paused == [make_roads._RETRY_PASS_PAUSE]


def test_fetch_all_still_fails_if_retry_pass_also_fails(tmp_path):
    def always_fails(url, dest):
        if "colorado" in url:
            raise RuntimeError("502 Bad Gateway")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_state_zip(dest)

    with pytest.raises(BuildError) as exc:
        make_roads.fetch_all(["colorado", "texas"], tmp_path / "cache",
                             downloader=always_fails, log=lambda *a: None,
                             sleep=lambda *a: None)

    assert "colorado" in str(exc.value)


def test_fetch_all_retry_pass_redownloads_a_zip_that_extracted_empty(tmp_path):
    """A state can 200 with a syntactically valid but content-short zip (no
    OSError, so fetch_state's own retry never fires) -- Geofabrik serving a
    placeholder/short-content package for one state while the daily extract
    batch is mid-regeneration, not a bad slug or a network blip. If that
    upstream glitch clears before the retry pass runs, the retry must
    actually re-fetch, not just re-run extraction on the same cached bad
    zip and fail identically every time."""
    attempts = {"delaware": 0}

    def bad_then_fixed(url, dest):
        attempts["delaware"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        if attempts["delaware"] == 1:
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("README", b"placeholder, no data yet")
        else:
            _write_state_zip(dest)

    shp_paths = make_roads.fetch_all(
        ["delaware"], tmp_path / "cache", downloader=bad_then_fixed,
        log=lambda *a: None, sleep=lambda *a: None)

    assert attempts["delaware"] == 2   # retry pass forced a real re-download
    assert len(shp_paths) == 1
    assert shp_paths[0].exists()


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
