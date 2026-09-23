# SPDX-License-Identifier: Apache-2.0
"""FAA d-TPP plate records + PDFs -> one sqlite pack (AER-1610/PA11).

A sqlite pack (BLOB payload), like navdata/water/obstacles -- not the zip
container terrain/highways use. sqlite handles a region's worth of PDF
blobs (hundreds of MB - low GB, docs/dtpp_plates_spike.md §1.3) without
difficulty and keeps this a single self-describing file with the same
``pack_meta`` embedding every other sqlite pack already uses, rather than
inventing a second zip-with-embedded-index convention for one new kind.

Three tables:
  * ``plate_files`` -- each distinct PDF exactly once (the *distinct-file*
    basis PA10 measured at ~4.7 GB nationally, not the duplicated-basis
    upper bound §1.3 also reports). A shared regional booklet (e.g. a MIN
    "RADAR MINIMUMS" PDF referenced by 200 airports) still lands in this
    table exactly once.
  * ``plates`` -- one row per catalog record (``packtools.dtpp.PlateRecord``),
    several of which may point at the same ``plate_files.pdf_name``.
  * ``plate_georef`` -- one row per PDF this build *attempted* to
    georeference (IAP charts only, see below), whether or not the attempt
    succeeded. ``status`` distinguishes "derived, here is a residual",
    "tried and failed the ambiguity/degeneracy checks", and (by simply
    having no row here at all) "not attempted this pass" -- a real signal
    for PA12 and for auditing coverage, not just a bare NULL.

Georeferencing is attempted for ``chart_code == "IAP"`` records only in
this pass: those are the charts ownship gates on (guardrail 5), they are
one-PDF-per-procedure (unlike the shared MIN/STR/HOT/LAH booklets, which
are also multi-page-per-airport in ways this pass does not have a
page-to-airport mapping for), and PA1's procedures pack gives an exact
per-airport fix vocabulary to cross-reference against.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from ..dtpp import PlateRecord, distinct_pdf_names
from .georef import GeoResult, derive_plate_georef

_SCHEMA = """
CREATE TABLE plate_files (
    pdf_name    TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    data        BLOB NOT NULL
);
CREATE TABLE plates (
    id          INTEGER PRIMARY KEY,
    state       TEXT NOT NULL,
    city        TEXT NOT NULL,
    apt_ident   TEXT NOT NULL,
    icao_ident  TEXT NOT NULL,
    military    INTEGER NOT NULL,
    chart_code  TEXT NOT NULL,
    chart_name  TEXT NOT NULL,
    procuid     TEXT,
    pdf_name    TEXT NOT NULL REFERENCES plate_files(pdf_name),
    amdt_num    TEXT,
    amdt_date   TEXT,
    cycle       TEXT NOT NULL
);
CREATE INDEX idx_plates_airport ON plates(icao_ident, chart_code);
CREATE INDEX idx_plates_pdf ON plates(pdf_name);

CREATE TABLE plate_georef (
    pdf_name        TEXT PRIMARY KEY REFERENCES plate_files(pdf_name),
    status          TEXT NOT NULL,
    residual_nm     REAL,
    control_points  INTEGER NOT NULL,
    transform_a     REAL,
    transform_b     REAL,
    transform_tx    REAL,
    transform_ty    REAL,
    origin_lat      REAL,
    origin_lon      REAL,
    detail          TEXT NOT NULL
);
"""

#: Chart types georeferencing is attempted for this pass -- see module docstring.
_GEOREF_CHART_CODES = ("IAP",)


class PlatesBuildError(RuntimeError):
    pass


def _extract_words_pypdf(pdf_path: Path) -> dict[str, list[tuple[float, float]]]:
    """Real word-position extraction. Lazy import -- like ``requests`` in
    ``fetch.py``, only the real build path needs pypdf installed; tests
    inject a fake extractor and never hit this. pypdf (BSD) over pymupdf
    (dual-licensed AGPL-3.0/commercial) -- see docs/LICENSE-AUDIT.md."""
    import pypdf  # optional dependency; see pyproject.toml [plates] extra

    out: dict[str, list[tuple[float, float]]] = {}
    reader = pypdf.PdfReader(str(pdf_path))
    page = reader.pages[0]

    def visitor(text: str, cm, tm, font_dict, font_size) -> None:
        # pypdf hands the whole string rendered by one text-show operator,
        # not one word at a time like pymupdf's get_text("words") -- split
        # on whitespace and give every resulting word the run's text origin.
        # Coarser than a per-word bbox centre, but the fix idents this feeds
        # (packtools/build/georef.py) print as their own standalone runs on
        # every real plate checked so far (tests/test_georef.py).
        x, y = tm[4], tm[5]
        for word in text.split():
            out.setdefault(word, []).append((x, y))

    page.extract_text(visitor_text=visitor)
    return out


def build_plates(records: list[PlateRecord], pdf_dir: str | Path, out_path: str | Path, *,
                 cycle: str, fix_index: dict[str, dict[str, tuple[float, float]]] | None = None,
                 extract_words=_extract_words_pypdf, log=lambda *a: None) -> Path:
    """Build one region's plates pack from parsed catalog records and their
    already-downloaded PDFs.

    ``pdf_dir`` holds every PDF ``records`` reference, named exactly as
    ``pdf_name`` (``packtools.dtpp.distinct_pdf_names``) -- the fetch step
    that populates it runs before this, per pack kind's existing fetch/build
    split. ``fix_index`` maps ICAO ident -> {fix_id: (lat, lon)} (every fix
    any procedure at that airport references, built from the already-built
    ``procedures`` pack -- see ``packtools/make_plates.py``); pass ``None``
    to skip georeferencing entirely (every IAP chart then simply has no
    ``plate_georef`` row, same as any chart type this pass never attempts).
    """
    if not records:
        raise PlatesBuildError("no plate records given -- refusing to ship an empty pack")
    pdf_dir = Path(pdf_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    con = sqlite3.connect(str(out_path))
    try:
        con.executescript(_SCHEMA)

        for pdf_name in distinct_pdf_names(records):
            pdf_path = pdf_dir / pdf_name
            if not pdf_path.is_file():
                raise PlatesBuildError(
                    f"{pdf_name} is referenced by the metafile but missing from "
                    f"{pdf_dir} -- expected every referenced PDF to have been "
                    f"fetched before build_plates() runs.")
            data = pdf_path.read_bytes()
            con.execute(
                "INSERT INTO plate_files (pdf_name, sha256, bytes, data) VALUES (?, ?, ?, ?)",
                (pdf_name, hashlib.sha256(data).hexdigest(), len(data), data))

        for r in records:
            con.execute(
                "INSERT INTO plates (state, city, apt_ident, icao_ident, military, "
                "chart_code, chart_name, procuid, pdf_name, amdt_num, amdt_date, cycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r.state, r.city, r.apt_ident, r.icao_ident, int(r.military),
                 r.chart_code, r.chart_name, r.procuid, r.pdf_name,
                 r.amdt_num, r.amdt_date, cycle))

        n_attempted = n_ok = 0
        if fix_index is not None:
            seen_pdfs: set[str] = set()
            for r in records:
                if r.chart_code not in _GEOREF_CHART_CODES or r.pdf_name in seen_pdfs:
                    continue
                control_fixes = fix_index.get(r.icao_ident)
                if not control_fixes:
                    continue
                seen_pdfs.add(r.pdf_name)
                n_attempted += 1
                word_positions = extract_words(pdf_dir / r.pdf_name)
                result: GeoResult = derive_plate_georef(word_positions, control_fixes)
                t = result.transform
                con.execute(
                    "INSERT INTO plate_georef (pdf_name, status, residual_nm, "
                    "control_points, transform_a, transform_b, transform_tx, "
                    "transform_ty, origin_lat, origin_lon, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r.pdf_name, result.status, result.residual_nm, result.control_points,
                     t.a if t else None, t.b if t else None, t.tx if t else None,
                     t.ty if t else None, t.origin_lat if t else None,
                     t.origin_lon if t else None, result.detail))
                if result.status == "ok":
                    n_ok += 1
                log(f"  georef {r.pdf_name} ({r.icao_ident} {r.chart_name}): "
                    f"{result.status}"
                    + (f" residual={result.residual_nm:.3f}nm" if result.residual_nm is not None else ""))

        con.commit()
    finally:
        con.close()

    log(f"plates: {len(records)} record(s), {n_ok}/{n_attempted} IAP plate(s) georeferenced")
    return out_path
