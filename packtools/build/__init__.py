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

Builder = Callable[[Path, Path], Path]


class BuildError(RuntimeError):
    pass


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


def _build_cifp(input_dir: Path, out_path: Path) -> Path:
    raise BuildError(
        "CIFP pack building is deferred: its indexer is GPL (pyAvTools). "
        "Build CIFP via faa-cifp-data's tooling, or reimplement the index. "
        "See packtools/sources.py.")


#: builder key (Source.builder) -> callable
BUILDERS: dict[str, Builder] = {
    "airports": build_airports,
    "obstacles": build_obstacles,
    "cifp": _build_cifp,
}
