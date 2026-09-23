# SPDX-License-Identifier: Apache-2.0
"""Per-region d-TPP plate pack orchestrator (AER-1610/PA11).

``packtools/run_cyclical.py``'s ``CyclicalRunner`` builds one pack per
``Source``; plates need several packs (one per ``regions.yaml`` region,
``docs/dtpp_plates_spike.md`` §3) from one shared upstream fetch (the
metafile plus a large, mostly-shared-across-regions PDF set), which does
not fit that one-``Source``-one-pack shape. So, like terrain and water,
plates gets its own manually-dispatched orchestrator here rather than a
``packtools.sources.Source`` entry -- see ``packtools/packmeta.py``'s
``KINDS`` comment for why "plates" is not yet in
``packtools.sources.SOURCES`` either.

Region assignment reuses the same OurAirports-vs-``regions.yaml`` join
``docs/dtpp_plates_spike.md`` §1.3 measured: an airport falls in a region
if its (lat, lon) is inside that region's bbox, and an airport in more than
one overlapping region (``us-west``/``us-central``/``us-south`` do, by
``regions.yaml``'s own design) is packaged into all of them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import packmeta
from .build.plates import build_plates
from .cycles import Cycle
from .dtpp import PlateRecord, distinct_pdf_names, metafile_url, parse_metafile, plate_pdf_url
from .manifest import Manifest, PackEntry
from .packmeta import PackMeta
from .regions import Region, load_regions

DEFAULT_ATTRIBUTION = "FAA Digital Terminal Procedures Publication (d-TPP), AeroNav Products"
DEFAULT_LICENSE = "LicenseRef-us-public-domain"
DEFAULT_LICENSE_URL = "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/"

# d-TPP is FAA/US-only (docs/dtpp_plates_spike.md §6) -- "conus" (the
# navdata-wide box) and the non-US regions.yaml regions (canada-*,
# mexico-central-america) are never plate-pack candidates.
_PLATE_REGIONS = ("us-west", "us-central", "us-east", "us-south", "alaska")


def airport_region_map(airports: list[dict], regions: dict[str, Region], *,
                       only_regions: list[str] | None = None) -> dict[str, list[str]]:
    """ICAO ident -> region key(s) it falls in.

    ``airports`` is the OurAirports ``airports.csv`` row shape
    (``packtools.ourairports.fetch_csvs()[0]``: ``ident``/``latitude_deg``/
    ``longitude_deg``) -- the same worldwide, public-domain source
    ``packtools/ourairports.py`` already uses, and the one
    ``docs/dtpp_plates_spike.md`` §1.3 joined against for its region-size
    measurements."""
    candidates = {k: r for k, r in regions.items()
                 if k in _PLATE_REGIONS and (not only_regions or k in only_regions)}
    out: dict[str, list[str]] = {}
    for a in airports:
        ident = a.get("ident")
        if not ident:
            continue
        try:
            lat, lon = float(a["latitude_deg"]), float(a["longitude_deg"])
        except (TypeError, ValueError, KeyError):
            continue
        hits = [key for key, r in candidates.items() if r.contains(lat, lon)]
        if hits:
            out[ident] = hits
    return out


def load_fix_index(procedures_db: str | Path) -> dict[str, dict[str, tuple[float, float]]]:
    """ICAO ident -> {fix_id: (lat, lon)} for every fix any procedure at
    that airport references, read from an already-built ``procedures``
    pack (``packtools/build/procedures.py``, PA1). Deliberately airport-wide
    rather than restricted to one matched procedure -- see
    ``packtools/build/plates.py``'s module docstring for why."""
    con = sqlite3.connect(str(procedures_db))
    try:
        rows = con.execute(
            "SELECT DISTINCT p.airport, l.fix_id, l.fix_lat, l.fix_lon "
            "FROM legs l JOIN transitions t ON l.transition_id = t.id "
            "JOIN procedures p ON t.proc_id = p.id "
            "WHERE l.fix_id IS NOT NULL AND l.fix_lat IS NOT NULL").fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for airport, fix_id, lat, lon in rows:
        out.setdefault(airport, {})[fix_id] = (lat, lon)
    return out


@dataclass
class PlatePack:
    region: str
    path: Path
    entry: PackEntry
    record_count: int


def make_plate_packs(*, records: list[PlateRecord], region_map: dict[str, list[str]],
                     pdf_dir: str | Path, out_dir: str | Path, cycle: Cycle,
                     url_base: str, fix_index: dict[str, dict[str, tuple[float, float]]] | None = None,
                     attribution: str = DEFAULT_ATTRIBUTION, license: str = DEFAULT_LICENSE,
                     license_url: str = DEFAULT_LICENSE_URL, log=print) -> list[PlatePack]:
    """Split ``records`` by region and build one pack per region that ends
    up with at least one record."""
    by_region: dict[str, list[PlateRecord]] = {}
    unmapped = 0
    for r in records:
        keys = region_map.get(r.icao_ident) or region_map.get(r.apt_ident)
        if not keys:
            unmapped += 1
            continue
        for k in keys:
            by_region.setdefault(k, []).append(r)
    if unmapped:
        # Not necessarily "foreign" -- docs/dtpp_plates_spike.md §6 scopes
        # out Canada/Mexico/Caribbean (no CONUS region box at all), but a US
        # airport can also fall outside every existing regions.yaml box
        # (e.g. Adak Island, PADK, sits west of the "alaska" region's
        # lon_min -- a real, measured gap in regions.yaml itself, not a
        # foreign-coverage question; see docs/plates.md).
        log(f"{unmapped} record(s) at an airport matching no plate region "
            f"(non-US per docs/dtpp_plates_spike.md §6, or a real gap in "
            f"regions.yaml -- see docs/plates.md)")

    out_dir = Path(out_dir)
    packs: list[PlatePack] = []
    for region, region_records in sorted(by_region.items()):
        pack_id = f"plates-{region}"
        pack_path = out_dir / "packs" / f"{pack_id}-{cycle.cycle}.pack"
        build_plates(region_records, pdf_dir, pack_path, cycle=cycle.cycle,
                    fix_index=fix_index, log=log)
        meta = PackMeta(id=pack_id, kind="plates", cycle=cycle.cycle,
                        effective=cycle.effective.isoformat(),
                        expires=cycle.expires.isoformat(),
                        attribution=attribution, license=license, license_url=license_url)
        packmeta.embed_sqlite(pack_path, meta)
        url = f"{url_base.rstrip('/')}/{pack_path.name}"
        entry = PackEntry.from_pack(pack_path, meta, url=url, regions=[region])
        packs.append(PlatePack(region, pack_path, entry, len(region_records)))
        log(f"  {region}: {len(region_records)} record(s) -> {pack_path.name} "
            f"({entry.bytes:,} B, sha {entry.sha256[:12]}...)")
    return packs


def fetch_plate_pdfs(pdf_names: list[str], dest_dir: str | Path, *, cycle: Cycle,
                     downloader=None, log=print) -> Path:
    """Download every named PDF into ``dest_dir`` (skip-if-present, like
    every other fetch step in this pipeline)."""
    from . import fetch as _fetch
    downloader = downloader or _fetch.download
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in pdf_names:
        dest = dest_dir / name
        if dest.exists():
            continue
        log(f"  fetch {plate_pdf_url(cycle, name)}")
        downloader(plate_pdf_url(cycle, name), dest)
    return dest_dir


def run(*, out_dir: str | Path, cycle: Cycle, url_base: str, work_dir: str | Path,
       only_regions: list[str] | None = None, procedures_db: str | Path | None = None,
       ourairports_rows: list[dict] | None = None, ourairports_cache_dir: str | Path | None = None,
       downloader=None, log=print) -> list[PlatePack]:
    """The real, network-touching entry point ``packtool make-plates`` drives.

    Skip-if-present at every fetch step (metafile, each PDF), like the rest
    of this pipeline -- a pre-populated ``work_dir`` (tests; a resumed
    partial run) never re-fetches what is already there.
    """
    from . import fetch as _fetch

    work_dir = Path(work_dir)
    metafile_path = work_dir / f"d-tpp_Metafile_{cycle.cycle}.xml"
    if not metafile_path.exists():
        log(f"fetch {metafile_url(cycle)}")
        (downloader or _fetch.download)(metafile_url(cycle), metafile_path)
    records = parse_metafile(metafile_path)
    log(f"parsed {len(records)} plate record(s), "
        f"{len(distinct_pdf_names(records))} distinct PDF(s)")

    if ourairports_rows is None:
        from . import ourairports
        ourairports_rows, _runways = ourairports.fetch_csvs(cache_dir=ourairports_cache_dir)
    region_map = airport_region_map(ourairports_rows, load_regions(), only_regions=only_regions)

    wanted = [r for r in records
             if region_map.get(r.icao_ident) or region_map.get(r.apt_ident)]
    pdf_dir = work_dir / "pdfs"
    fetch_plate_pdfs(distinct_pdf_names(wanted), pdf_dir, cycle=cycle,
                     downloader=downloader, log=log)

    fix_index = load_fix_index(procedures_db) if procedures_db else None

    return make_plate_packs(records=wanted, region_map=region_map, pdf_dir=pdf_dir,
                            out_dir=out_dir, cycle=cycle, url_base=url_base,
                            fix_index=fix_index, log=log)


def update_manifest(store, secret, packs: list[PlatePack], *, generated: str, sign, log=print) -> Manifest:
    """Upsert plate entries into the manifest in ``store`` and re-sign."""
    from .publish import publish
    return publish(store, secret, [(p.entry, p.path) for p in packs],
                   generated=generated, sign=sign, comment=f"plates {generated}", log=log)
