<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# `metafile_2609_sample.xml` test fixture (AER-1610 / PA11)

A trimmed slice of the live FAA d-TPP chart catalog, cycle **2609**
(effective 2026-09-03), downloaded verbatim from
`https://aeronav.faa.gov/d-tpp/2609/xml_data/d-tpp_Metafile.xml` on
2026-09-23 (same live pull PA10's spike, `docs/dtpp_plates_spike.md`,
measured against). Every field on every kept `<record>` is copied unedited;
records and whole `airport_name`/`city_name` blocks were dropped to keep the
fixture small, not altered. FAA d-TPP data is a US Government work (public
domain); see `docs/LICENSE-AUDIT.md`.

## What is in it

| Selection | Why |
|---|---|
| `ADK` (PADK), all 11 real chart records | The build's golden IAP case -- `01244IYLY23.PDF` (ILS Y OR LOC Y RWY 23) is the same plate downloaded live and cross-referenced against real CIFP fix coordinates for `packtools/build/georef.py`'s tests (`tests/fixtures/dtpp/01244IYLY23.PDF`, `tests/test_georef.py`). Also exercises a distinct-pdf-per-record chart type (`IAP`/`APD`/`DP`) and two MIN booklet records own to ADK/EIL (`AKTO.PDF`). |
| `EIL` (PAEI, Eielson AFB), 2 records | One real `useraction=D`/`pdf_name=DELETED_JOB.PDF` withdrawn-chart record (PA10 §1.2's "147 of 24,231 records... a withdrawn-chart placeholder, not a real file" finding) -- the parser must filter this, not fetch it. The second record (`AKTO.PDF`) is the same shared MIN booklet ADK references, proving the distinct-pdf-name dedup across airports. |
| `EDF` (PAED, Elmendorf AFB) + `FBK` (PAFB, Ladd AAF), 1 record each | Both reference `AKRAD.PDF` -- a real regional MIN "RADAR MINIMUMS" booklet shared by 2+ airports (PA10 §1.2's shared-booklet finding), a second, independent shared-pdf case from the `AKTO.PDF` one above. |

Every airport here is real and `military="M"` except `ADK` (`military="N"`) --
useful signal that the parser must not assume civil-only, though this pass
does not filter on it.

## `01244IYLY23.PDF`

The real plate this fixture's `ADK` block's `chartseq 50750` / "ILS Y OR LOC
Y RWY 23" record names, downloaded verbatim from
`https://aeronav.faa.gov/d-tpp/2609/01244IYLY23.PDF` on 2026-09-23. Used by
`packtools/build/plates.py`'s build test to prove the PDF-embedding path
against a real file, not a synthetic one. `tests/test_georef.py`'s ADK case
does *not* open this PDF (its word positions are pinned as literals so that
test needs neither pymupdf nor network) -- regenerate them with:

```python
import pymupdf
page = pymupdf.open("01244IYLY23.PDF")[0]
targets = {"GIDKE", "SALSE", "GUISE", "TICCU", "LONOK", "COMAT"}
by_word = {}
for x0, y0, x1, y1, text, *_ in page.get_text("words"):
    if text in targets:
        by_word.setdefault(text, []).append(((x0 + x1) / 2, (y0 + y1) / 2))
```

## Regenerating or extending

Not scripted (one-off extraction during AER-1610) -- reproduce by
downloading the current cycle's metafile and filtering with
`xml.etree.ElementTree`, e.g. to find a fresh `useraction=D` example or a
shared-booklet pdf_name referenced by exactly N airports:

```python
import xml.etree.ElementTree as ET
from collections import defaultdict
root = ET.parse("d-tpp_Metafile.xml").getroot()
by_pdf = defaultdict(list)
for state in root.findall("state_code"):
    for city in state.findall("city_name"):
        for apt in city.findall("airport_name"):
            for rec in apt.findall("record"):
                if rec.findtext("chart_code") == "MIN":
                    by_pdf[rec.findtext("pdf_name")].append(apt.attrib["apt_ident"])
```
