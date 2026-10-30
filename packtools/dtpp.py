# SPDX-License-Identifier: Apache-2.0
"""FAA d-TPP chart catalog -- metafile parsing + URL builders (AER-1610/PA11).

Every 28-day AIRAC cycle the FAA publishes ``d-tpp_Metafile.xml``, one XML
record per approach plate/airport diagram/departure procedure/etc it ships
that cycle (docs/dtpp_plates_spike.md §1.2, measured live against cycle
2609: 54 states, 3,197 airports, 24,231 chart records, 16,503 distinct PDF
files). This module turns that XML into :class:`PlateRecord` rows and knows
the two URL shapes (metafile itself, and each named PDF) the same way
``packtools/sources.py`` knows the NASR/CIFP URL shapes -- both keyed off a
:class:`packtools.cycles.Cycle` so the URL is derived, never hard-coded.

Verified live 2026-09-23 against cycle 2609:
  metafile   aeronav.faa.gov/d-tpp/<cycle>/xml_data/d-tpp_Metafile.xml
  plate PDF  aeronav.faa.gov/d-tpp/<cycle>/<pdf_name>

147 of 24,231 records (0.6%) in the live 2609 metafile carry
``useraction=D`` and a sentinel ``pdf_name=DELETED_JOB.PDF`` -- a
withdrawn-chart placeholder, not a real file (it 404s). ``parse_metafile``
filters these the same way the ARINC 424 parser filters unsupported leg
types: at the boundary, not left for a caller to trip over.

Chart records are not one-PDF-per-airport: MIN (takeoff/alternate minimums)
in particular is published as regional booklets shared by up to ~200
airports (``docs/dtpp_plates_spike.md`` §1.2's "shared regional booklet"
finding) -- STR/HOT/LAH have smaller real sharing too. This module does not
deduplicate that for the caller; it returns one :class:`PlateRecord` per
catalog record, several of which may share a ``pdf_name``. That is
deliberate: ``packtools/build/plates.py`` uses the repeated ``pdf_name`` to
store each distinct PDF exactly once (the distinct-file basis PA10 measured
at ~4.7 GB nationally, not the duplicated-basis upper bound).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .cycles import Cycle

_BASE = "https://aeronav.faa.gov/d-tpp"

#: The one withdrawn-chart placeholder every cycle carries a few hundred of
#: (docs/dtpp_plates_spike.md §1.2) -- confirmed live to 404, never fetch it.
DELETED_PDF_NAME = "DELETED_JOB.PDF"


def metafile_url(c: Cycle) -> str:
    return f"{_BASE}/{c.cycle}/xml_data/d-tpp_Metafile.xml"


def plate_pdf_url(c: Cycle, pdf_name: str) -> str:
    return f"{_BASE}/{c.cycle}/{pdf_name}"


@dataclass(frozen=True)
class PlateRecord:
    """One `<record>` under one `<airport_name>` in the metafile -- one
    chart, which may share its `pdf_name` with other records/airports
    (see module docstring)."""
    state: str
    state_name: str
    city: str
    volume: str
    apt_ident: str
    icao_ident: str
    military: bool
    chart_seq: str
    chart_code: str            # IAP | APD | STR | DP | ODP | MIN | HOT | LAH | DAU
    chart_name: str
    pdf_name: str
    procuid: str | None        # links to CIFP's procedure uid where present (IAP/STR/DP/ODP)
    faanfd18: str | None       # SID/STAR computer-navigation-fix coded ident, where present
    civil: str | None
    amdt_num: str | None
    amdt_date: str | None


class DtppParseError(RuntimeError):
    pass


def _text(el: ET.Element, tag: str) -> str | None:
    v = el.findtext(tag)
    return v if v else None


def parse_metafile(source: str | Path | bytes) -> list[PlateRecord]:
    """Parse a d-TPP metafile into :class:`PlateRecord` rows, dropping
    withdrawn-chart placeholders (``useraction=D`` /
    ``pdf_name=DELETED_JOB.PDF``, docs/dtpp_plates_spike.md §1.2).

    ``source`` is a path, raw XML bytes, or an XML string -- whatever
    :func:`xml.etree.ElementTree.parse`/``fromstring`` accept."""
    if isinstance(source, (str, Path)) and not str(source).lstrip().startswith("<"):
        root = ET.parse(source).getroot()
    else:
        root = ET.fromstring(source)
    if root.tag != "digital_tpp":
        raise DtppParseError(f"not a d-TPP metafile: root element is {root.tag!r}, expected digital_tpp")

    out: list[PlateRecord] = []
    for state in root.findall("state_code"):
        state_id = state.get("ID", "")
        state_name = state.get("state_fullname", "")
        for city in state.findall("city_name"):
            city_id = city.get("ID", "")
            volume = city.get("volume", "")
            for apt in city.findall("airport_name"):
                apt_ident = apt.get("apt_ident", "")
                icao_ident = apt.get("icao_ident", "")
                military = apt.get("military") == "M"
                for rec in apt.findall("record"):
                    useraction = (_text(rec, "useraction") or "").strip()
                    pdf_name = _text(rec, "pdf_name") or ""
                    if useraction == "D" or pdf_name == DELETED_PDF_NAME:
                        continue
                    if not pdf_name:
                        continue
                    out.append(PlateRecord(
                        state=state_id, state_name=state_name, city=city_id,
                        volume=volume, apt_ident=apt_ident, icao_ident=icao_ident,
                        military=military,
                        chart_seq=_text(rec, "chartseq") or "",
                        chart_code=_text(rec, "chart_code") or "",
                        chart_name=_text(rec, "chart_name") or "",
                        pdf_name=pdf_name,
                        procuid=_text(rec, "procuid"),
                        faanfd18=_text(rec, "faanfd18"),
                        civil=_text(rec, "civil"),
                        amdt_num=_text(rec, "amdtnum"),
                        amdt_date=_text(rec, "amdtdate"),
                    ))
    return out


def distinct_pdf_names(records: list[PlateRecord]) -> list[str]:
    """The set of PDF files ``records`` actually reference, in first-seen
    order -- what a fetch step downloads, one request per file regardless
    of how many records (airports) share it."""
    seen: dict[str, None] = {}
    for r in records:
        seen.setdefault(r.pdf_name, None)
    return list(seen)
