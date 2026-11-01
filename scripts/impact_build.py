#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build one fixture-region pack with ONE tree's builder (TV9, AER-4572).

``packtools.impact`` runs this twice per kind, once with ``PYTHONPATH``
pointing at the PR's base checkout and once at its head, and diffs the two
outputs. The script itself always comes from the head checkout, and so does
the fixture input it feeds the builder, so the two runs differ only in the
builder code they import. It is run with ``python -P`` so its own directory
is not put ahead of that tree on ``sys.path``.

The base tree may predate this script, so the only things it imports from
the tree are the builder entry points the real pipelines call:
``make_roads.build_na_roads`` (highways) and ``build.build_procedures`` /
``build.build_airspace_cifp`` (procedures, airspace).

Exits 0 with the built sqlite at ``--out``; anything else is a failed build
and the last stderr line says why.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
import zlib
from pathlib import Path

# The Geofabrik URL forms make_roads asks for: the "latest" alias and the
# dated snapshot STATE_SNAPSHOT_OVERRIDES pins.
_SLUG = re.compile(r"/north-america/us/(.+?)-(?:latest|\d{6})-free\.shp\.zip$")

# What download.geofabrik.de serves for a slug it does not know: a 302 to its
# home page, which a downloader following redirects saves as HTML
# (makerplane-data#17). Mimicked so a renamed or misspelt slug fails here the
# way it fails against the real mirror.
_NOT_A_STATE = b"<!DOCTYPE html><html><head><title>Geofabrik Download Server</title></head></html>\n"

_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,'
        '298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]]')


def roads_zip(slug: str, centre: list[float], spec: dict) -> bytes:
    """One state's synthetic ``gis_osm_roads_free_1`` layer as a zip, laid out
    like a Geofabrik free-shapefile bundle. Deterministic: the same spec gives
    the same features on both sides of the diff."""
    import shapefile  # pyshp

    lat0, lon0 = centre
    hw = float(spec["half_width_deg"])
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYLINE)
    for name, size in (("osm_id", 12), ("fclass", 28), ("name", 40), ("ref", 20),
                       ("bridge", 1), ("tunnel", 1)):
        w.field(name, "C", size=size)
    rows = [(fc, i) for fc, n in spec["roads"] for i in range(int(n))]
    for k, (fclass, i) in enumerate(rows):
        lat = lat0 - hw + 2 * hw * (k + 0.5) / len(rows)
        # Five vertices with a small kink, so the decimation step keeps some.
        pts = [[lon0 - hw + 2 * hw * j / 4, lat + (0.01 if j == 2 else 0.0)] for j in range(5)]
        w.line([pts])
        w.record(f"{zlib.crc32(slug.encode()) % 10**6:06d}{k:03d}", fclass,
                 f"Fixture {fclass} {i}", f"FX-{k}", "T" if k == 1 else "F",
                 "T" if k == 2 else "F")
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README", "Synthetic fixture, not OpenStreetMap data (TV9).\n")
        zf.writestr("gis_osm_roads_free_1.shp", shp.getvalue())
        zf.writestr("gis_osm_roads_free_1.shx", shx.getvalue())
        zf.writestr("gis_osm_roads_free_1.dbf", dbf.getvalue())
        zf.writestr("gis_osm_roads_free_1.prj", _PRJ)
        zf.writestr("gis_osm_roads_free_1.cpg", "UTF-8")
    return buf.getvalue()


def fixture_downloader(spec: dict):
    """A ``fetch.download`` stand-in serving the synthetic state zips."""
    states = spec["states"]

    def download(url: str, dest) -> None:
        m = _SLUG.search(url)
        slug = m.group(1) if m else None
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if slug in states:
            dest.write_bytes(roads_zip(slug, states[slug], spec))
        else:
            dest.write_bytes(_NOT_A_STATE)
    return download


def build_highways(out: Path, fixtures: Path, work: Path) -> None:
    from packtools import make_roads
    spec = json.loads((fixtures / "impact" / "roads_states.json").read_text(encoding="utf-8"))
    make_roads.build_na_roads("conus", out, work / "roads-cache",
                              downloader=fixture_downloader(spec), log=print)


def build_procedures(out: Path, fixtures: Path, work: Path) -> None:
    from packtools.build import build_procedures as b
    b(fixtures / "cifp", out)


def build_airspace(out: Path, fixtures: Path, work: Path) -> None:
    from packtools.build import build_airspace_cifp as b
    b(fixtures / "cifp_airspace", out)


BUILDS = {"highways": build_highways, "procedures": build_procedures,
          "airspace": build_airspace}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("kind", choices=sorted(BUILDS))
    ap.add_argument("--tree", required=True, help="the checkout whose builder to use")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixtures", required=True, help="the head checkout's tests/fixtures")
    ap.add_argument("--work", required=True, help="scratch dir for downloads")
    a = ap.parse_args(argv)
    import packtools
    tree = Path(a.tree).resolve()
    got = Path(packtools.__file__).resolve()
    if tree not in got.parents:
        print(f"packtools imported from {got}, not from {tree}: check PYTHONPATH",
              file=sys.stderr)
        return 3
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        BUILDS[a.kind](out, Path(a.fixtures), Path(a.work))
    except Exception as e:     # any builder failure is a failed build, reported as such
        print(f"{type(e).__name__}: {e}".replace(str(tree), "<tree>"), file=sys.stderr)
        return 1
    if not out.is_file():
        print("the builder returned without writing its output", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
