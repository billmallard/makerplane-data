# SPDX-License-Identifier: Apache-2.0
"""CONUS highways-pack input builder (AER-623, RD3).

Downloads each Geofabrik state's roads-layer shapefile
(``gis_osm_roads_free_1``) and hands the collected files to pyEfis's
``build_highway_db.py`` (via ``packtools.build.build_highways``), which
writes ``flags`` (tunnel/bridge bits) and ``ref`` alongside ``fclass`` and
geometry when the input carries those fields -- RD3a, pyEfis PR #165.
Packing/signing/upload is unchanged: ``packtool build-pack`` on the result
(docs/roads.md).

The RD3 brief names this workstation script ``work/build_na_roads.py``;
``work/`` is gitignored scratch (see .gitignore, and ``run_cyclical``'s own
``--work`` default), so the reviewable, reusable half lives here instead and
``work/`` stays what it already is elsewhere in this repo -- the download
cache (``--cache-dir`` default below).

Same shape as the water pipeline: download each state's Geofabrik "free
shapefile" bundle once, keep only the layer this pack needs, delete the
rest. California ships no combined state bundle -- Geofabrik answers
``california-latest-free.shp.zip`` with a 302 to its homepage, not the file
(the bundle was discontinued); it is split into norcal/socal subregion
extracts instead. That is the same accommodation pyEfis's
``tools/fetch_geofabrik_water.py`` makes in its ``CONUS_STATES`` list for
the water pack, mirrored here rather than imported -- fetch tooling lives in
pyEfis and this repo doesn't vendor it (CLAUDE.md "tool-sharing shim").
``makerplane-data#17`` is the incident this whole module guards against: the
June 2026 roads build used a bare ``california`` entry, hit that same 302,
and silently shipped a pack with zero California roads.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from . import fetch
from .build import BuildError, build_highways

GEOFABRIK_BASE = "https://download.geofabrik.de/north-america/us"

CONUS_STATES = [
    "alabama", "arizona", "arkansas",
    "california/norcal", "california/socal", "colorado",
    "connecticut", "delaware", "district-of-columbia", "florida",
    "georgia", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new-hampshire", "new-jersey", "new-mexico",
    "new-york", "north-carolina", "north-dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode-island", "south-carolina",
    "south-dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west-virginia", "wisconsin", "wyoming",
]

ROAD_LAYER = "gis_osm_roads_free_1"
ROAD_LAYER_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def parse_states(spec: str) -> list[str]:
    """``'conus'`` -> the full :data:`CONUS_STATES` list; otherwise a comma
    list of Geofabrik state slugs (accepts spaces/underscores like '-')."""
    spec = spec.strip().lower()
    if spec == "conus":
        return list(CONUS_STATES)
    return [p.strip().replace(" ", "-").replace("_", "-")
            for p in spec.split(",") if p.strip()]


def state_zip_url(state: str) -> str:
    return f"{GEOFABRIK_BASE}/{state}-latest-free.shp.zip"


def _cache_name(state: str) -> str:
    return state.replace("/", "-") + "-latest-free.shp.zip"


def fetch_state(state: str, cache_dir: Path, *, downloader=fetch.download) -> Path:
    """Download one state's shp.zip (idempotent -- skips a cached non-empty
    file). Validates the result is really a zip: Geofabrik answers an
    unknown state slug with a 302 to its homepage, not a 404, so a bad slug
    otherwise lands here as a small HTML file (makerplane-data#17)."""
    cache_dir = Path(cache_dir)
    dest = cache_dir / _cache_name(state)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    downloader(state_zip_url(state), dest)
    with open(dest, "rb") as fh:
        magic = fh.read(4)
    if not magic.startswith(b"PK"):
        size = dest.stat().st_size
        dest.unlink()
        raise BuildError(
            f"{state_zip_url(state)} returned a non-zip ({size} bytes; "
            f"magic {magic!r}) -- Geofabrik redirects an unknown state slug "
            "to its homepage instead of 404ing (makerplane-data#17)")
    return dest


def extract_road_layer(zip_path: Path, dest_dir: Path) -> Path | None:
    """Pull just the roads layer out of a state zip into its own subdir
    (named for the state), so the zip can be deleted afterward without
    losing what the build needs. Returns ``None`` if the zip has no roads
    layer at all -- an empty/wrong extract, makerplane-data#17's failure
    mode -- so the caller treats it as a hard error, never a silent skip."""
    zip_path, dest_dir = Path(zip_path), Path(dest_dir)
    state = zip_path.name[: -len("-latest-free.shp.zip")]
    out_dir = dest_dir / state.replace("/", "-")
    out_dir.mkdir(parents=True, exist_ok=True)
    shp_out = out_dir / f"{ROAD_LAYER}.shp"
    if shp_out.exists() and shp_out.stat().st_size > 0:
        return shp_out
    found_shp = False
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for ext in ROAD_LAYER_EXTS:
            layer = f"{ROAD_LAYER}{ext}"
            match = next((n for n in names if n.endswith(layer)), None)
            if match is None:
                continue
            with zf.open(match) as src, open(out_dir / layer, "wb") as dst:
                dst.write(src.read())
            if ext == ".shp":
                found_shp = True
    return shp_out if found_shp else None


def fetch_all(states: list[str], cache_dir: Path, *, keep_zips: bool = False,
              downloader=fetch.download, log=print) -> list[Path]:
    """Download + extract the roads layer for every state; fail loud and
    list every gap rather than silently building a pack short a state
    (makerplane-data#17)."""
    cache_dir = Path(cache_dir)
    extracted_dir = cache_dir / "extracted"
    shp_paths: list[Path] = []
    failed: list[str] = []
    for i, state in enumerate(states, 1):
        log(f"[{i}/{len(states)}] {state}")
        cached = extracted_dir / state.replace("/", "-") / f"{ROAD_LAYER}.shp"
        if cached.exists() and cached.stat().st_size > 0:
            shp_paths.append(cached)
            continue
        try:
            zip_path = fetch_state(state, cache_dir, downloader=downloader)
            shp = extract_road_layer(zip_path, extracted_dir)
        except Exception as e:
            log(f"  ERROR: {state}: {e}")
            failed.append(state)
            continue
        if shp is None:
            log(f"  ERROR: {state}: extract had no {ROAD_LAYER}.shp")
            failed.append(state)
            continue
        shp_paths.append(shp)
        if not keep_zips:
            zip_path.unlink()
    if failed:
        raise BuildError(f"{len(failed)} state(s) failed: {', '.join(failed)}")
    return shp_paths


def build_na_roads(states_spec: str, dest: Path, cache_dir: Path, *,
                    keep_zips: bool = False, overwrite: bool = True,
                    downloader=fetch.download, log=print) -> Path:
    """End to end: parse a ``--states`` spec, fetch every state's roads
    layer, build ``highways.sqlite``. Packing/signing/upload is unchanged --
    ``packtool build-pack`` on the result (docs/roads.md)."""
    states = parse_states(states_spec)
    log(f"plan: {len(states)} state(s)")
    fetch_all(states, cache_dir, keep_zips=keep_zips, downloader=downloader, log=log)
    log(f"building {dest} ...")
    return build_highways(Path(cache_dir) / "extracted", Path(dest), overwrite=overwrite)
