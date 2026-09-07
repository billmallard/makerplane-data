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

import time
import zipfile
from pathlib import Path

from . import fetch
from .build import BuildError, build_highways

GEOFABRIK_BASE = "https://download.geofabrik.de/north-america/us"

# A 50-state sequential fetch runs long enough (~80 minutes for CONUS) that
# Geofabrik's mirror serving a transient 502/503/read-timeout to one or two
# states somewhere in the run is routine, not exceptional (makerplane-data#60:
# 9/50 states failed this way in one CONUS build, none of them a real bad
# slug). Retrying those a few times with backoff is cheap next to redoing the
# whole 80-minute job; a state that is still down after retrying is still a
# hard failure via fetch_all's fail-loud-and-list-every-gap contract
# (makerplane-data#17) -- this only absorbs the transient case.
_FETCH_RETRIES = 3
_FETCH_BACKOFF = 5.0

# fetch_state's own retry/backoff rides out a blip on one state (tens of
# seconds). The 9-state failure above was clustered -- several consecutive
# states erroring within the same few minutes -- which reads as Geofabrik
# having a rough patch, not nine independent coin flips; a same-minute
# per-state retry can't outlast that, but the ~70 remaining minutes of a
# CONUS run fetching everything else is exactly the cooldown a rough patch
# needs. So fetch_all gives failed states one more pass *after* the rest of
# the run finishes, with its own pause first in case failures were clustered
# at the very end and no time has elapsed at all.
_RETRY_PASS_PAUSE = 60.0

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


def fetch_state(state: str, cache_dir: Path, *, downloader=fetch.download,
                retries: int = _FETCH_RETRIES, backoff: float = _FETCH_BACKOFF,
                sleep=time.sleep, log=lambda *a: None) -> Path:
    """Download one state's shp.zip (idempotent -- skips a cached non-empty
    file). Validates the result is really a zip: Geofabrik answers an
    unknown state slug with a 302 to its homepage, not a 404, so a bad slug
    otherwise lands here as a small HTML file (makerplane-data#17).

    Retries a transient network failure (``OSError`` -- covers every
    ``requests`` exception: HTTPError, ConnectionError, Timeout,
    ChunkedEncodingError) a few times with backoff before giving up; a
    non-network failure (e.g. the non-zip check below) is never retried."""
    cache_dir = Path(cache_dir)
    dest = cache_dir / _cache_name(state)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    for attempt in range(1, retries + 1):
        try:
            downloader(state_zip_url(state), dest)
            break
        except OSError as e:
            if attempt == retries:
                raise
            log(f"  WARN: {state}: {e} -- retry {attempt}/{retries - 1}")
            sleep(backoff * attempt)
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
              downloader=fetch.download, log=print,
              retry_pause: float = _RETRY_PASS_PAUSE, sleep=time.sleep) -> list[Path]:
    """Download + extract the roads layer for every state; fail loud and
    list every gap rather than silently building a pack short a state
    (makerplane-data#17).

    A state still failing after :func:`fetch_state`'s own retries gets one
    more attempt in a second pass once every other state has been tried --
    long enough for a clustered run of transient Geofabrik errors
    (makerplane-data#60) to clear that a same-minute retry can't outlast."""
    cache_dir = Path(cache_dir)
    extracted_dir = cache_dir / "extracted"

    def try_one(state: str) -> Path:
        cached = extracted_dir / state.replace("/", "-") / f"{ROAD_LAYER}.shp"
        if cached.exists() and cached.stat().st_size > 0:
            return cached
        zip_path = fetch_state(state, cache_dir, downloader=downloader, log=log)
        shp = extract_road_layer(zip_path, extracted_dir)
        if shp is None:
            raise BuildError(f"extract had no {ROAD_LAYER}.shp")
        if not keep_zips:
            zip_path.unlink()
        return shp

    shp_paths: list[Path] = []
    failed: list[str] = []
    for i, state in enumerate(states, 1):
        log(f"[{i}/{len(states)}] {state}")
        try:
            shp_paths.append(try_one(state))
        except Exception as e:
            log(f"  ERROR: {state}: {e}")
            failed.append(state)

    if failed:
        log(f"{len(failed)} state(s) failed on the first pass "
            f"({', '.join(failed)}) -- retrying after {retry_pause:.0f}s "
            "(makerplane-data#60)")
        sleep(retry_pause)
        still_failed: list[str] = []
        for i, state in enumerate(failed, 1):
            log(f"[retry {i}/{len(failed)}] {state}")
            try:
                shp_paths.append(try_one(state))
            except Exception as e:
                log(f"  ERROR: {state}: {e}")
                still_failed.append(state)
        failed = still_failed

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
