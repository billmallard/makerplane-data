# SPDX-License-Identifier: Apache-2.0
"""packtool CLI — genkey -> build-pack -> verify, plus failure exit codes."""

import json
import sqlite3

import pytest

from packtools import cli
from packtools.packmeta import read as read_packmeta


def _make_sqlite(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE airports (icao TEXT)")
    con.execute("INSERT INTO airports VALUES ('KSBA')")
    con.commit()
    con.close()


def test_genkey_build_verify_roundtrip(tmp_path, capsys):
    keys = tmp_path / "keys"
    assert cli.main(["genkey", "--out", str(keys)]) == 0
    assert (keys / "minisign.pub").exists()
    assert (keys / "minisign.sec").exists()

    src = tmp_path / "airports.sqlite"
    _make_sqlite(src)
    out = tmp_path / "work"
    rc = cli.main(["build-pack", str(src), "--id", "airports-conus",
                   "--kind", "navdata", "--date", "2026-06-14",
                   "--attribution", "FAA NASR", "--regions", "conus",
                   "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0
    assert (out / "manifest.json").exists()
    # named by the computed AIRAC cycle for 2026-06-14
    assert (out / "packs" / "airports-conus-2606.pack").exists()

    rc = cli.main(["verify", str(out / "manifest.json"),
                   "--pub", str(keys / "minisign.pub")])
    assert rc == 0


def test_build_pack_license_flags_embed_in_pack_meta(tmp_path):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "water.sqlite"
    _make_sqlite(src)
    out = tmp_path / "work"
    rc = cli.main(["build-pack", str(src), "--id", "water-conus",
                   "--kind", "water", "--cycle", "2026q2",
                   "--attribution", "OpenStreetMap contributors (ODbL)",
                   "--license", "ODbL-1.0",
                   "--license-url", "https://opendatacommons.org/licenses/odbl/1-0/",
                   "--regions", "conus",
                   "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0
    meta = read_packmeta(out / "packs" / "water-conus-2026q2.pack")
    assert meta.license == "ODbL-1.0"
    assert meta.license_url == "https://opendatacommons.org/licenses/odbl/1-0/"


def test_build_airspace_pack_end_to_end(tmp_path):
    # AER-547 acceptance: build_airspace() -> build-pack --kind airspace
    # carries openAIP's license through PackMeta and the manifest's
    # PackEntry, round-trippable end to end -- no live network, no R2.
    from packtools import sources
    from packtools.build.airspace import build_airspace
    from packtools.manifest import Manifest

    geo_dir = tmp_path / "geojson"
    geo_dir.mkdir()
    feature = {
        "type": "Feature",
        "properties": {
            "_id": "f1", "name": "CALGARY CTR", "type": 4, "icaoClass": "C",
            "country": "ca",
            "lowerLimit": {"value": 0, "unit": "FT", "referenceDatum": "SFC"},
            "upperLimit": {"value": 3500, "unit": "FT", "referenceDatum": "MSL"},
        },
        "geometry": {"type": "Polygon", "coordinates": [[
            [-114.0, 51.0], [-113.9, 51.0], [-113.9, 51.1], [-114.0, 51.1],
            [-114.0, 51.0]]]},
    }
    (geo_dir / "ca_asp.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [feature]}))

    built = tmp_path / "built" / "airspace-ca.sqlite"
    build_airspace(geo_dir, built)

    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src_openaip = sources.AIRSPACE_SOURCES["airspace-ca"]
    out = tmp_path / "work"
    rc = cli.main(["build-pack", str(built), "--id", "airspace-ca",
                   "--kind", "airspace", "--cycle", "r1",
                   "--attribution", src_openaip.attribution,
                   "--license", src_openaip.license,
                   "--license-url", src_openaip.license_url,
                   "--regions", "ca",
                   "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0

    meta = read_packmeta(out / "packs" / "airspace-ca-r1.pack")
    assert meta.license == "CC-BY-NC-4.0"
    assert meta.license_url == "https://creativecommons.org/licenses/by-nc/4.0/"

    m = Manifest.read(out / "manifest.json")
    entry = next(p for p in m.packs if p.id == "airspace-ca")
    assert entry.license == "CC-BY-NC-4.0"
    assert entry.license_url == "https://creativecommons.org/licenses/by-nc/4.0/"
    assert entry.effective is None    # non-cyclical, like water/terrain


def test_verify_detects_tampering(tmp_path):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "obstacles.sqlite"
    _make_sqlite(src)
    out = tmp_path / "work"
    cli.main(["build-pack", str(src), "--id", "obstacles-conus",
              "--kind", "obstacles", "--date", "2026-06-14",
              "--sec", str(keys / "minisign.sec"), "--out", str(out)])

    manifest = out / "manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"obstacles", b"0bstacles"))
    rc = cli.main(["verify", str(manifest), "--pub", str(keys / "minisign.pub")])
    assert rc == 2          # signature no longer matches the bytes


def test_build_pack_noncyclical_requires_cycle(tmp_path):
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "t.sqlite"
    _make_sqlite(src)
    with pytest.raises(SystemExit):
        cli.main(["build-pack", str(src), "--id", "terrain-na", "--kind", "terrain",
                  "--date", "2026-06-14", "--sec", str(keys / "minisign.sec"),
                  "--out", str(tmp_path / "w")])


def test_build_pack_noncyclical_cycle_only_ok(tmp_path):
    # --cycle alone is sufficient for a non-cyclical kind (water/terrain/highways);
    # no --effective needed. Regression for the build-pack && bug.
    keys = tmp_path / "keys"
    cli.main(["genkey", "--out", str(keys)])
    src = tmp_path / "w.sqlite"
    _make_sqlite(src)
    out = tmp_path / "out"
    rc = cli.main(["build-pack", str(src), "--id", "water-conus", "--kind", "water",
                   "--cycle", "2026q2", "--attribution", "OSM ODbL", "--regions", "conus",
                   "--date", "2026-06-14", "--sec", str(keys / "minisign.sec"), "--out", str(out)])
    assert rc == 0
    from packtools.manifest import Manifest
    m = Manifest.read(out / "manifest.json")
    e = next(p for p in m.packs if p.id == "water-conus")
    assert e.cycle == "2026q2" and e.effective is None     # non-cyclical entry
