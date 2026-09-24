# SPDX-License-Identifier: Apache-2.0
"""Region loading + tile-to-region assignment (the Phase A gap)."""

import pytest

from packtools import regions


def test_load_regions_has_expected_keys():
    r = regions.load_regions()
    for key in ("conus", "us-west", "us-south", "alaska", "canada-west"):
        assert key in r
    assert r["conus"].name == "Continental US"


def test_tile_sw_corner():
    assert regions.tile_sw_corner("N32W097") == (32, -97)
    assert regions.tile_sw_corner("S01E036") == (-1, 36)
    assert regions.tile_sw_corner("n32w097.hgt") == (32, -97)   # case/suffix tolerant


def test_tile_sw_corner_rejects_garbage():
    with pytest.raises(ValueError):
        regions.tile_sw_corner("not-a-tile")


def test_region_contains_is_half_open():
    r = regions.load_regions()["conus"]            # bbox 24,-125,50,-66
    assert r.contains(34.4, -119.8)                # KSBA area
    assert not r.contains(50.0, -100.0)            # lat_max exclusive
    assert not r.contains(10.0, -100.0)            # below lat_min


def test_regions_for_tile_assigns_southwest_us():
    r = regions.load_regions()
    # N32W097 (Dallas-ish) sits in CONUS and a US terrain region.
    keys = regions.regions_for_tile("N32W097", r)
    assert "conus" in keys
    assert any(k.startswith("us-") for k in keys)


def test_regions_for_tile_offshore_is_empty():
    r = regions.load_regions()
    assert regions.regions_for_tile("N00W030", r) == []   # mid-Atlantic


def test_alaska_region_reaches_adak_but_not_chukotka():
    """AER-1990: lon_min -177 (not the old -170, not all the way to -180)
    -- real, measured tradeoff in docs/aer-1990-alaska-region-gap.md."""
    r = regions.load_regions()["alaska"]
    assert r.contains(51.872, -176.676)       # PADK Adak Island, real d-TPP airport
    assert r.contains(52.220, -174.206)       # PAAK Atka
    assert r.contains(63.767, -171.733)       # PAGM Gambell
    assert not r.contains(66.5, -179.5)       # Chukotka Peninsula (Russia), not Alaska
    assert not r.contains(52.712, 174.114)    # PASY Eareckson/Shemya -- across the
                                               # antimeridian, unreachable by any
                                               # lon_min/lon_max bbox, still an open gap


def test_manifest_regions_block_shape():
    block = regions.manifest_regions_block(regions.load_regions())
    assert block["conus"]["bbox"] == [24, -125, 50, -66]
    assert set(block["us-west"]) == {"name", "bbox"}
