"""Build terrain packs from an HGT tile tree, grouped by region.

Terrain is the bulk/static data shape: huge, ~never changes, edition-tagged
(never "expires"). Unlike the cyclical navdata pipeline this is run manually
on a workstation (or the Pi) that already holds the HGT tiles — see
docs/data_manager_implementation.md (terrain.yml is dispatch-only).

Each region becomes one zip pack of HGT files laid out as ``<NSdir>/<name>.hgt``
(e.g. ``N32/N32W097.hgt``) plus a ``pack_meta.json`` — the exact tree the
pyEfis SVS reads. Tiles are assigned to regions by their SW corner. A tile
that falls in two regions is written into both packs (identical content;
the Pi unions them into one tile tree).
"""

from __future__ import annotations

import datetime as _dt
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import packmeta
from .manifest import Manifest, PackEntry
from .packmeta import PackMeta
from .regions import Region, load_regions, regions_for_tile, tile_sw_corner

DEFAULT_ATTRIBUTION = "Copernicus GLO-30 (redistribution permitted)"


def find_tiles(src_root: str | Path) -> dict[str, Path]:
    """Map TILE_NAME (e.g. ``N32W097``) -> path for every .hgt under src."""
    src = Path(src_root)
    out: dict[str, Path] = {}
    for p in src.rglob("*.hgt"):
        out[p.stem.upper()] = p
    return out


def _ns_dir(name: str) -> str:
    lat, _lon = tile_sw_corner(name)
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"


def _bbox(names: list[str]) -> list[int]:
    corners = [tile_sw_corner(n) for n in names]
    lats = [c[0] for c in corners]
    lons = [c[1] for c in corners]
    # each tile spans 1 degree from its SW corner
    return [min(lats), min(lons), max(lats) + 1, max(lons) + 1]


@dataclass
class TerrainPack:
    region: str
    path: Path
    entry: PackEntry
    tile_count: int


def build_region_pack(region: str, tiles: list[tuple[str, Path]], *,
                      out_dir: Path, edition: str, url_base: str,
                      attribution: str = DEFAULT_ATTRIBUTION,
                      compress: bool = True) -> TerrainPack:
    """Zip one region's HGT tiles into a signed-able pack with pack_meta."""
    out_dir = Path(out_dir)
    (out_dir / "packs").mkdir(parents=True, exist_ok=True)
    pack_id = f"terrain-{region}"
    pack_path = out_dir / "packs" / f"{pack_id}-{edition}.pack"
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED

    with zipfile.ZipFile(pack_path, "w", mode) as z:
        for name, src in sorted(tiles):
            z.write(src, f"{_ns_dir(name)}/{name}.hgt")

    meta = PackMeta(id=pack_id, kind="terrain", cycle=edition, attribution=attribution)
    packmeta.embed_zip(pack_path, meta)   # adds pack_meta.json into the zip

    entry = PackEntry.from_pack(
        pack_path, meta, url=f"{url_base.rstrip('/')}/{pack_path.name}",
        regions=[region], tiles_bbox=_bbox([n for n, _ in tiles]))
    return TerrainPack(region, pack_path, entry, len(tiles))


def make_terrain_packs(*, src_root: str | Path, out_dir: str | Path,
                       edition: str, url_base: str,
                       regions_path: str | Path | None = None,
                       only_regions: list[str] | None = None,
                       attribution: str = DEFAULT_ATTRIBUTION,
                       compress: bool = True, log=print) -> list[TerrainPack]:
    """Build a terrain pack per region from the HGT tree under ``src_root``."""
    regions = load_regions(regions_path)
    tiles = find_tiles(src_root)
    log(f"found {len(tiles)} HGT tiles under {src_root}")

    by_region: dict[str, list[tuple[str, Path]]] = {}
    for name, path in tiles.items():
        for rkey in regions_for_tile(name, regions):
            if only_regions and rkey not in only_regions:
                continue
            by_region.setdefault(rkey, []).append((name, path))

    packs: list[TerrainPack] = []
    for region, tile_list in sorted(by_region.items()):
        tp = build_region_pack(region, tile_list, out_dir=out_dir,
                               edition=edition, url_base=url_base,
                               attribution=attribution, compress=compress)
        log(f"  {region}: {tp.tile_count} tiles -> {tp.path.name} "
            f"({tp.entry.bytes:,} B, sha {tp.entry.sha256[:12]}…)")
        packs.append(tp)
    if not packs:
        log("no tiles matched any region (check regions.yaml / --only)")
    return packs


def update_manifest(store, secret, packs: list[TerrainPack], *,
                    generated: str, sign, log=print) -> Manifest:
    """Upsert terrain entries into the manifest in ``store`` and re-sign.
    Adds terrain alongside whatever is already published (navdata, water)."""
    from .publish import publish
    return publish(store, secret, [(tp.entry, tp.path) for tp in packs],
                   generated=generated, sign=sign,
                   comment=f"terrain {generated}", log=log)


def _now_stamp(today: _dt.date | None = None) -> str:
    d = today or _dt.date.today()
    return f"{d.isoformat()}T00:00:00Z"
