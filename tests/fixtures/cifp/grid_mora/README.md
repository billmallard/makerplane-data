<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Grid MORA (`AS`) test fixture (AER-4447)

`HDR01`-`HDR05` plus 13 real `AS` records copied verbatim (no fields
changed) from the live FAA CIFP cycle **2611** (effective 2026-10-29),
downloaded from `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip`
on 2026-10-31. FAA CIFP data is a US Government work (public domain); see
`docs/LICENSE-AUDIT.md`.

Kept apart from `../FAACIFP18` (the 2609 golden-procedure set) so that
fixture's cycle and record counts stay as documented. The builder test
puts the two files together in one input.

## What is in it

| Band (SW corner) | Why |
|---|---|
| `N35W120`, `N36W120`, `N37W120` | Mt Whitney (36.58 N, 118.29 W) and the bands either side |
| `N45W150`, `N46W150`, `N47W150` | Mt Rainier (46.85 N, 121.76 W) and the bands either side |
| `N62W180`, `N63W180`, `N64W180` | Denali (63.07 N, 151.01 W) and the bands either side |
| `N27W120` | open Gulf of Mexico cell (27 N, 93 W): 1,400 ft |
| `N24W090` | (24 N, 82 W): 15,200 ft over the flat Florida Keys, consistent with the Cudjoe Key tethered aerostat; a real value the bounds check must not reject |
| `N04E150` | western Pacific, all 30 cells `UNK` |
| `S14W180` | southern hemisphere, all 30 cells `UNK` |

The three summit groups pin the cell convention. Read with the start
lat/lon as the south-west corner of the first cell and cells stepping east,
each summit's cell is the highest value in its 3x3 neighbourhood: Whitney
and Rainier 16,800 ft, Denali 22,700 ft (tied with its east neighbour; the
summit sits 0.007 deg west of the 151 W line). Read any other way, the
peak lands in the wrong cell. Nothing in 2611
tests the convention south of the equator: every `S` cell is `UNK`.

## Regenerating

```python
lines = open("FAACIFP18", encoding="latin-1").read().split("\n")
recs = {l[13:20]: l for l in lines if l[4:6] == "AS"}
keep = [l for l in lines[:10] if l.startswith("HDR")] + [recs[k] for k in (
    "N04E150", "S14W180", "N35W120", "N36W120", "N37W120", "N45W150", "N46W150",
    "N47W150", "N62W180", "N63W180", "N64W180", "N27W120", "N24W090")]
```
