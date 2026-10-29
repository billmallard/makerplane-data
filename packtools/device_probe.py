# SPDX-License-Identifier: Apache-2.0
"""Device acceptance probe: open a pack with pyEfis's own reader.

Run as a separate process, never imported by packtools itself::

    PYTHONPATH=<pyEfis checkout>/src python -m packtools.device_probe KIND PACK

pyEfis is GPL; this repo's tools are Apache-2.0 and do not vendor it (see
``packtools/build/__init__.py``). The probe reaches the reader the device
runs through a separate pyEfis checkout on ``PYTHONPATH``, in its own
process, the same way the builders run ``pyEfis/tools`` by path. It prints
one JSON object on stdout::

    {"opened": true, "queries": [{"query": "...", "count": 10, "min": 1, "ok": true}]}

or ``{"opened": false, "error": "..."}``. ``packtools.report`` runs it and
turns the result into the ACC-001 check.

The query set is fixed per kind, so the result is comparable across
editions. Each query is a place a full-coverage pack must have data; a
states-limited build published as full coverage (makerplane-data#60) or a
pack missing a whole state (#17) returns nothing there.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

# (label, lat, lon, range_nm). Major airports and cities spread across the
# coverage area; the 10-20 nm radius keeps each query local.
_US_POINTS = [("Denver", 39.86, -104.67), ("Seattle", 47.45, -122.31),
              ("Atlanta", 33.64, -84.43), ("Los Angeles", 34.05, -118.25),
              ("Chicago", 41.88, -87.63)]
_CA_POINTS = [("Toronto", 43.68, -79.63), ("Vancouver", 49.19, -123.18)]
_WATER_POINTS = [("Puget Sound", 47.6, -122.4), ("Lake Michigan", 41.88, -87.5),
                 ("Lake Ontario", 43.6, -79.3)]
_NAVAID_IDENTS = ("DEN", "SEA", "LAX")
_AIRWAY_IDENTS = ("V23", "J80", "V1")


def _q(label: str, count: int, minimum: int = 1, *, exact: bool = False) -> dict:
    ok = count == minimum if exact else count >= minimum
    return {"query": label, "count": count, "min": minimum, "ok": ok}


def _probe_airports(path, points):
    from pyefis.instruments.ai.airport_db import NASRAirportDB
    db = NASRAirportDB(path, paved_only=False)
    if not db.ready:
        return False, []
    return True, [_q(f"airports within 20 nm of {name}",
                     len(list(db.airports_in_range(lat, lon, 20))))
                  for name, lat, lon in points]


def _waypoint_index(**kw):
    from pyefis.flightplan.waypoints import WaypointIndex
    tmp = Path(tempfile.mkdtemp(prefix="probe-"))
    idx = WaypointIndex(user_file=tmp / "user.json", recent_file=tmp / "recent.json", **kw)
    return idx, idx.wait_ready(120)


def probe_navdata(path):
    opened, qs = _probe_airports(path, _US_POINTS)
    if opened:
        idx, ready = _waypoint_index(airports_db_path=path)
        qs.append(_q("ident lookup DEN (Denver Intl)", len(idx.lookup("DEN")) if ready else 0))
    return opened, qs


def probe_airports(path):
    return _probe_airports(path, _CA_POINTS)


def probe_navaids(path):
    idx, ready = _waypoint_index(navaids_db_path=path)
    if not ready:
        return False, []
    return True, [_q(f"ident lookup {i}", len(idx.lookup(i))) for i in _NAVAID_IDENTS]


def probe_obstacles(path):
    from pyefis.instruments.ai.obstacle_db import ObstacleDB
    db = ObstacleDB(path)
    if not db.ready:
        return False, []
    return True, [_q(f"obstacles within 10 nm of {name}",
                     len(list(db.obstacles_in_range(lat, lon, 10))))
                  for name, lat, lon in _US_POINTS]


def probe_procedures(path):
    from pyefis.flightplan.airways import AirwayGraph
    g = AirwayGraph(path)
    if not g.ready:
        return False, []
    try:
        return True, [_q(f"airway legs {i}", len(g.legs(i))) for i in _AIRWAY_IDENTS]
    finally:
        g.close()


def probe_highways(path):
    from pyefis.instruments.ai.highway_db import HighwayDB
    db = HighwayDB(path)
    if not db.ready:
        return False, []
    return True, [_q(f"road lines within 10 nm of {name}",
                     len(list(db.polylines_in_range(lat, lon, 10))))
                  for name, lat, lon in _US_POINTS]


def probe_water(path):
    from pyefis.instruments.ai.water_db import WaterDB
    db = WaterDB(path)
    if not db.ready:
        return False, []
    return True, [_q(f"water polygons within 10 nm of {name}",
                     len(list(db.polygons_in_range(lat, lon, 10))))
                  for name, lat, lon in _WATER_POINTS]


def probe_terrain(path):
    """Extract the smallest piece the SVS reads (one native tile, or the
    coarsest mosaic level) and load it through ``svs.TileCache``."""
    from pyefis.instruments.ai.svs import TileCache
    from packtools.regions import tile_sw_corner
    root = Path(tempfile.mkdtemp(prefix="probe-terrain-"))
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        native = sorted(n for n in names if n.endswith(".hgt") and not n.startswith(".mip/"))
        mosaic = sorted((n for n in names if n.startswith(".mip/mosaic/L") and n.endswith(".hgt")),
                        key=lambda n: int(Path(n).stem[1:]))
        if native:
            member = native[0]
            # The grid side the member's size implies, the side TER-001 checks.
            side = int(round((z.getinfo(member).file_size / 2) ** 0.5))
            for n in (member, member[:-4] + ".wmask"):
                if n in names:
                    z.extract(n, root)
            lat, lon = tile_sw_corner(Path(member).stem)
            tile = TileCache(root).get(lat, lon)
            n = 0 if tile is None else int(tile.shape[0])
            return True, [_q(f"native tile {Path(member).stem} loads with grid side {side}",
                             n, side, exact=True)]
        if mosaic:
            member = mosaic[-1]
            level = int(Path(member).stem[1:])
            stem = member[:-4]
            for ext in (".hgt", ".json", ".wmask"):
                if stem + ext in names:
                    z.extract(stem + ext, root)
            tc = TileCache(root)
            m = tc.get_mosaic(level)
            mask = tc.get_mosaic_mask(level)
            return True, [_q(f"mosaic level {level} loads", 0 if m is None else 1),
                          _q(f"mosaic level {level} water mask loads", 0 if mask is None else 1)]
    return True, [_q("terrain tiles present", 0)]


PROBES = {
    "navdata": probe_navdata, "airports": probe_airports, "navaids": probe_navaids,
    "obstacles": probe_obstacles, "procedures": probe_procedures,
    "highways": probe_highways, "water": probe_water, "terrain": probe_terrain,
}


def run(kind: str, path: str) -> dict:
    probe = PROBES.get(kind)
    if probe is None:
        return {"opened": False, "error": f"no device probe for kind {kind!r}"}
    try:
        opened, queries = probe(path)
    except ImportError as e:
        # The reader could not be loaded at all: an environment problem
        # (no pyEfis on PYTHONPATH, a missing dependency), not a pack defect.
        return {"opened": False, "unavailable": True, "error": f"{type(e).__name__}: {e}"}
    except Exception as e:                       # the reader choked on the pack
        return {"opened": False, "error": f"{type(e).__name__}: {e}"}
    if not opened:
        return {"opened": False, "error": "the pyEfis reader reported not ready"}
    return {"opened": True, "queries": queries}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m packtools.device_probe KIND PACK", file=sys.stderr)
        return 2
    print(json.dumps(run(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
