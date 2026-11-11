# SPDX-License-Identifier: Apache-2.0
"""Build shim — turn extracted upstream files into an sqlite pack.

INTERIM (Phase A/B decision): the FAA->sqlite build logic lives in the
pyEfis repo (`tools/build_airport_db.py`, `tools/build_obstacle_db.py`).
We do NOT vendor it here — one implementation. Until those scripts are
factored into a standalone `pyefis-tools` package, the CI workflow checks
out pyEfis and points us at its tools/ directory via ``PYEFIS_TOOLS_DIR``;
we invoke the scripts by path in a subprocess.

Each builder takes the directory of extracted upstream files and an output
path, and returns the built sqlite Path. The orchestrator injects an
alternative builder map in tests, so this module is never run there.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .airspace import build_airspace
from .airspace_cifp import build_airspace_cifp
from .errors import BuildError
from .procedures import build_procedures

Builder = Callable[[Path, Path], Path]


def tools_dir() -> Path:
    d = os.environ.get("PYEFIS_TOOLS_DIR")
    if not d:
        raise BuildError(
            "PYEFIS_TOOLS_DIR is not set. Point it at a checkout of "
            "pyEfis/tools (the CI workflow does this). Interim until the "
            "pyefis-tools package exists.")
    p = Path(d)
    if not p.is_dir():
        raise BuildError(f"PYEFIS_TOOLS_DIR does not exist: {p}")
    return p


def pyefis_commit() -> str:
    """The full sha of the pyEfis checkout ``PYEFIS_TOOLS_DIR`` sits in: the
    code a pack built by one of :data:`PYEFIS_BUILDERS` actually ran
    (CERT5a). The workflows check out a branch (``PYEFIS_REF``), not a fixed
    commit, so this is the only record of which one it was. Raises rather
    than guess: a pack that cannot name its builder must not publish."""
    d = tools_dir()
    try:
        out = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BuildError(f"cannot read the pyEfis commit under {d}: {e}") from e
    sha = out.stdout.strip()
    if out.returncode != 0 or len(sha) != 40:
        raise BuildError(f"cannot read the pyEfis commit under {d}: PYEFIS_TOOLS_DIR is "
                         f"not in a git checkout ({out.stderr.strip() or sha!r})")
    return sha


def _run_tool(script: str, args: list[str]) -> None:
    tool = tools_dir() / script
    if not tool.exists():
        raise BuildError(f"build tool not found: {tool}")
    cmd = [sys.executable, str(tool), *args]
    print("  $", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(
            f"{script} failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")


def build_airports(input_dir: Path, out_path: Path) -> Path:
    # US-only FAA NASR. Foreign airports come from separate provider packs
    # (e.g. airports-canada from OurAirports), built independently and merged
    # at runtime by the SVS — not folded in here. See packtools/ourairports.py.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_tool("build_airport_db.py",
              ["--nasr-dir", str(input_dir), "--output", str(out_path)])
    return out_path


def build_obstacles(input_dir: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_tool("build_obstacle_db.py",
              ["--dof-dir", str(input_dir), "--output", str(out_path)])
    return out_path


def build_navaids(input_dir: Path, out_path: Path) -> Path:
    # input_dir holds the NAV + FIX + AWY CSV zips all extracted together
    # (multi-URL source; see packtools/sources.py).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_tool("build_navaid_db.py",
              ["--nasr-dir", str(input_dir), "--output", str(out_path)])
    return out_path


def _build_cifp(input_dir: Path, out_path: Path) -> Path:
    raise BuildError(
        "CIFP pack building is deferred: its indexer is GPL (pyAvTools). "
        "Build CIFP via faa-cifp-data's tooling, or reimplement the index. "
        "See packtools/sources.py.")


def build_highways(input_dir: Path, out_path: Path, *, overwrite: bool = True) -> Path:
    """input_dir holds one subdirectory per state, each with its extracted
    ``gis_osm_roads_free_1.shp`` (packtools.make_roads lays it out this way) --
    multi-state multi-file input, like build_navaids's NAV+FIX+AWY. Not part
    of BUILDERS/the cyclical orchestrator: highways is a manually-triggered,
    non-cyclical rebuild (docs/roads.md), same as terrain/water.

    Every state subdirectory must hold a non-empty roads layer: a state dir
    that is present but empty is a failed extract, and globbing past it
    would silently build a pack short that state (makerplane-data#17)."""
    input_dir = Path(input_dir)
    empty = []
    if input_dir.is_dir():
        for d in sorted(input_dir.iterdir()):
            shp = d / "gis_osm_roads_free_1.shp"
            if d.is_dir() and not (shp.is_file() and shp.stat().st_size > 0):
                empty.append(d.name)
    if empty:
        raise BuildError(
            f"{len(empty)} state extract dir(s) under {input_dir} have no "
            f"gis_osm_roads_free_1.shp: {', '.join(empty)}")
    shapefiles = sorted(input_dir.rglob("gis_osm_roads_free_1.shp"))
    if not shapefiles:
        raise BuildError(f"no gis_osm_roads_free_1.shp found under {input_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["--dest", str(out_path)]
    if overwrite:
        args.append("--overwrite")
    args += [str(p) for p in shapefiles]
    _run_tool("build_highway_db.py", args)
    return out_path


#: The builders that run a pyEfis script. A pack one of them builds records
#: :func:`pyefis_commit` in its pack_meta (CERT5a).
PYEFIS_BUILDERS = frozenset({build_airports, build_obstacles, build_navaids, build_highways})

#: builder key (Source.builder) -> callable
BUILDERS: dict[str, Builder] = {
    "airports": build_airports,
    "obstacles": build_obstacles,
    "navaids": build_navaids,
    "cifp": _build_cifp,
    "airspace": build_airspace,
    "airspace-cifp": build_airspace_cifp,
    "procedures": build_procedures,
}
