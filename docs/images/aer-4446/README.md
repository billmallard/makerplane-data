<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-4446 MSA plate spot checks

Crops of the MSA circle from three FAA d-TPP plates for KSBA (Santa Barbara),
cycle 2611, set against the `PS` records the parser reads from the 2611 CIFP
(`CIFP_261029.zip`). FAA charts and CIFP are US Government works (public
domain).

| Image | Plate | CIFP `PS` record (sectors as decoded) |
|---|---|---|
| `ksba_rnav7_msa_rw07.png` | RNAV (GPS) RWY 7, `00378R7.PDF` | KSBA RW07 (runway): 180-180 8100 ft 25 nm, one sector |
| `ksba_ils7_msa_rzs.png` | ILS OR LOC RWY 7, `00378IL7.PDF` | KSBA RZS (VORTAC), magnetic: 030-120 5900, 120-210 8100, 210-300 8800, 300-030 5200, all 25 nm |
| `ksba_vor25_msa_gvo.png` | VOR RWY 25, `00378V25.PDF` | KSBA GVO (VORTAC), magnetic: 140-360 8100, 360-140 4800, all 25 nm |

How to read them: the plate prints each sector's limits as arrows pointing in
at the centre fix, labelled with the bearing *to* the fix. In the RZS circle,
8100 sits at the top between the 120 and 210 arrows: an aircraft north of the
fix flies about 180 to reach it, which falls in the 120-210 sector. So CIFP
codes sector bearings as bearings to the centre, clockwise from start to end,
and the `msa` table stores them that way (`bearing_start`, `bearing_end`).
Every PS record in 2611 is magnetic (`M`); none uses true bearings.

The plates also name each approach's MSA centre, and the CIFP approach
records carry the same reference in the centre-fix columns (cols 107-116) of
one non-RF leg per procedure: KSBA `I07` names RZS and `R07` names RW07.

## Regenerating

```bash
curl -sfLO https://aeronav.faa.gov/d-tpp/2611/00378IL7.PDF
curl -sfLO https://aeronav.faa.gov/d-tpp/2611/00378R7.PDF
curl -sfLO https://aeronav.faa.gov/d-tpp/2611/00378V25.PDF
python3 - <<'EOF'
import pymupdf   # PyMuPDF
for pdf, png, clip in (("00378IL7.PDF", "ksba_ils7_msa_rzs.png", (285, 150, 385, 240)),
                       ("00378R7.PDF", "ksba_rnav7_msa_rw07.png", (10, 260, 120, 340)),
                       ("00378V25.PDF", "ksba_vor25_msa_gvo.png", (10, 330, 120, 410))):
    pymupdf.open(pdf)[0].get_pixmap(dpi=500, clip=pymupdf.Rect(*clip)).save(png)
EOF
```

The clip rectangles are PDF points on page 1, found by locating the MSA
altitude labels with `page.get_text("words")`. A later plate revision may
move the circle, so re-find them before reusing the numbers.
