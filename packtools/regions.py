"""Region definitions and tile-to-region assignment.

Loads ``regions.yaml`` and answers two questions the pipeline asks:
  * which regions does this manifest advertise (the ``regions`` block)?
  * which region(s) does a given 1-degree terrain tile belong to?

bbox order everywhere is ``[lat_min, lon_min, lat_max, lon_max]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_REGIONS = _REPO_ROOT / "regions.yaml"

_TILE_RE = re.compile(r"^([NS])(\d{2})([EW])(\d{3})")


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    bbox: tuple[float, float, float, float]  # lat_min, lon_min, lat_max, lon_max

    def contains(self, lat: float, lon: float) -> bool:
        lat_min, lon_min, lat_max, lon_max = self.bbox
        return lat_min <= lat < lat_max and lon_min <= lon < lon_max


def load_regions(path: str | Path | None = None) -> dict[str, Region]:
    path = Path(path) if path else _DEFAULT_REGIONS
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, Region] = {}
    for key, val in (doc.get("regions") or {}).items():
        out[key] = Region(key=key, name=val.get("name", key), bbox=tuple(val["bbox"]))
    return out


def manifest_regions_block(regions: dict[str, Region]) -> dict[str, dict]:
    """The ``regions`` object embedded in the manifest."""
    return {r.key: {"name": r.name, "bbox": list(r.bbox)} for r in regions.values()}


def tile_sw_corner(tile_name: str) -> tuple[int, int]:
    """SW corner (lat, lon) of an SRTM/GLO-style tile name like ``N32W097``."""
    m = _TILE_RE.match(tile_name.upper())
    if not m:
        raise ValueError(f"unrecognised tile name: {tile_name!r}")
    ns, lat, ew, lon = m.groups()
    lat = int(lat) * (1 if ns == "N" else -1)
    lon = int(lon) * (1 if ew == "E" else -1)
    return lat, lon


def regions_for_tile(tile_name: str, regions: dict[str, Region]) -> list[str]:
    """Which region keys a tile belongs to (assigned by SW corner)."""
    lat, lon = tile_sw_corner(tile_name)
    return [r.key for r in regions.values() if r.contains(lat, lon)]
