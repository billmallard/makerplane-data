<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-4445 airspace differential: CIFP-built pack against FAA ADDS

Evidence for `docs/airspace_cifp.md`. Each image overlays one pack row (blue,
thin) on the ADDS sector(s) it was matched to (orange, thick). The title
gives the row's limits, IoU and Hausdorff distance. At this scale the
outlines coincide whenever the Hausdorff distance is under about 0.1 NM; the
numbers carry the precision, the pictures show the shape.

| Image | What it shows |
|---|---|
| `cifp-UC-K2-A-KSBA-A.png`, `-B.png` | KSBA Class C: the 5 NM surface area, and the 1,500 MSL shelf (clockwise 10 NM arc, counter-clockwise 5 NM arc) |
| `cifp-UC-K2-T-KLAX-A.png`, `-D.png` | KLAX Class B, two of its 14 sectors |
| `cifp-UR-PA-M-BIRCH-A.png`, `-C.png` | BIRCH MOA (Alaska, area code `CAN`) |
| `cifp-UC-K6-A-KBGR-A.png` | KBGR Class C sector A: the CIFP arc direction disagrees with ADDS (see the doc) |
| `cifp-UC-K3-T-KMCI-D.png` | KMCI Class B area D: same outline; ADDS has a hole, CIFP a full disk |
| `cifp-UR-PG-R-7201A-A.png` | R-7201A: ADDS's hole is CIFP's zero-height sector B |
| `airspace_adds_diff.csv` | every row: match status, limits, IoU, Hausdorff, matched ADDS feature |
| `airspace_adds_diff_summary.json` | the summary table in the doc |

## Inputs

- FAA CIFP cycle 2611, `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip`
  (sha256 `e3ff2b31a4f6ad3eda1342b83ada1ae399aae7bd8c1e67f3cacd5b288b5e141d`;
  `FAACIFP18` sha256 `877bd911e105b9b1e85fa970c55488f6ad987760814f45c42b84017cd5d6c1c9`).
- ADDS `Class_Airspace` (Class B/C/D) and `Special_Use_Airspace` feature
  services, fetched 2026-10-31 by the script. ADDS states no AIRAC cycle,
  so a difference could also be a currency difference.

## Commands

```bash
curl -sfLO https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip
python3 -c "import zipfile; zipfile.ZipFile('CIFP_261029.zip').extract('FAACIFP18', 'cifp')"
python3 -c "from packtools.build.airspace_cifp import build_airspace_cifp as b; b('cifp', 'asp.sqlite')"
pip install --target pylib shapely matplotlib     # evidence only; not packtools deps
PYTHONPATH=pylib python3 scripts/airspace_adds_diff.py asp.sqlite --cache adds \
    --out docs/images/aer-4445 --plot cifp-UC-K2-A-KSBA-A cifp-UC-K2-A-KSBA-B \
    cifp-UC-K2-T-KLAX-A cifp-UC-K2-T-KLAX-D cifp-UR-PA-M-BIRCH-A cifp-UR-PA-M-BIRCH-C \
    cifp-UC-K6-A-KBGR-A cifp-UC-K3-T-KMCI-D cifp-UR-PG-R-7201A-A
```
