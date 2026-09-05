# SPDX-License-Identifier: Apache-2.0
"""openAIP airspace builder — static GeoJSON -> sqlite pack (AER-547)."""

import json
import sqlite3

import pytest

from packtools.build import BUILDERS
from packtools.build.airspace import AirspaceBuildError, build_airspace


def _feature(fid, name, country="CA", lower=None, upper=None):
    return {
        "type": "Feature",
        "properties": {
            "_id": fid,
            "name": name,
            "type": 4,
            "icaoClass": "C",
            "country": country,
            "lowerLimit": lower or {"value": 0, "unit": "FT", "referenceDatum": "SFC"},
            "upperLimit": upper or {"value": 3500, "unit": "FT", "referenceDatum": "MSL"},
            "activity": 0,
            "remarks": "test fixture",
            "updatedAt": "2026-08-01T00:00:00Z",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-114.0, 51.0], [-113.9, 51.0], [-113.9, 51.1],
                             [-114.0, 51.1], [-114.0, 51.0]]],
        },
    }


def _write_export(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def test_registered_in_builders():
    assert BUILDERS["airspace"] is build_airspace


def test_build_airspace_writes_rows(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_export(src / "ca_asp.geojson",
                  [_feature("f1", "CALGARY CTR"), _feature("f2", "EDMONTON TMA")])

    out = build_airspace(src, tmp_path / "out" / "airspace-ca.sqlite")
    con = sqlite3.connect(str(out))
    rows = con.execute(
        "SELECT id, name, country, lower_limit, lower_limit_unit, "
        "lower_limit_reference, upper_limit, geometry FROM airspaces "
        "ORDER BY id").fetchall()
    con.close()

    assert [r[0] for r in rows] == ["f1", "f2"]
    assert rows[0][1] == "CALGARY CTR"
    assert rows[0][2] == "CA"
    assert rows[0][3:6] == ("0", "FT", "SFC")
    assert rows[0][6] == "3500"
    geom = json.loads(rows[0][7])
    assert geom["type"] == "Polygon"


def test_build_airspace_keeps_full_properties_json(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_export(src / "ca_asp.geojson", [_feature("f1", "CALGARY CTR")])
    out = build_airspace(src, tmp_path / "out.sqlite")
    con = sqlite3.connect(str(out))
    raw = con.execute("SELECT properties_json FROM airspaces WHERE id='f1'").fetchone()[0]
    con.close()
    props = json.loads(raw)
    assert props["name"] == "CALGARY CTR" and props["icaoClass"] == "C"


def test_bare_scalar_limit_normalizes_to_value_only(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_export(src / "ca_asp.geojson",
                  [_feature("f1", "SFC LAYER", lower="SFC")])
    out = build_airspace(src, tmp_path / "out.sqlite")
    con = sqlite3.connect(str(out))
    row = con.execute(
        "SELECT lower_limit, lower_limit_unit FROM airspaces WHERE id='f1'").fetchone()
    con.close()
    assert row == ("SFC", None)


def test_multiple_geojson_files_fold_into_one_pack(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_export(src / "part1.geojson", [_feature("f1", "A")])
    _write_export(src / "part2.geojson", [_feature("f2", "B")])
    out = build_airspace(src, tmp_path / "out.sqlite")
    con = sqlite3.connect(str(out))
    n = con.execute("SELECT COUNT(*) FROM airspaces").fetchone()[0]
    con.close()
    assert n == 2


def test_no_geojson_files_raises(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(AirspaceBuildError):
        build_airspace(src, tmp_path / "out.sqlite")


def test_empty_feature_collection_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_export(src / "ca_asp.geojson", [])
    with pytest.raises(AirspaceBuildError):
        build_airspace(src, tmp_path / "out.sqlite")


def test_feature_without_country_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    feat = _feature("f1", "NO COUNTRY")
    del feat["properties"]["country"]
    _write_export(src / "ca_asp.geojson", [feat])
    with pytest.raises(AirspaceBuildError):
        build_airspace(src, tmp_path / "out.sqlite")


def test_feature_without_geometry_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    feat = _feature("f1", "NO GEOM")
    feat["geometry"] = None
    _write_export(src / "ca_asp.geojson", [feat])
    with pytest.raises(AirspaceBuildError):
        build_airspace(src, tmp_path / "out.sqlite")


def test_feature_without_id_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    feat = _feature("f1", "NO ID")
    del feat["properties"]["_id"]
    _write_export(src / "ca_asp.geojson", [feat])
    with pytest.raises(AirspaceBuildError):
        build_airspace(src, tmp_path / "out.sqlite")
