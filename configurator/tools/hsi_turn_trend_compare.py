#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-6089 (ATT1b twin): compose the side-by-side sheet. One row per config
in the oracle JSON: pyEfis widget (tools/hsi_turn_trend_oracle.py) | editor
preview (tools/hsi_turn_trend_svg.mjs, rasterised here with QtSvg at the
widget's size). Optionally the pyEfis Pilot's Guide figure
(docs/images/hsi/turn_rate_trend.png) on top, and a nearest-neighbour zoom of
the band around the lubber where the trend lives.

QtSvg, not a browser, rasterises the twin: geometry is the editor's own SVG,
but text shaping and antialiasing are Qt's.

    QT_QPA_PLATFORM=offscreen [FONT_DIR=/path/to/dejavu] \\
      python3 tools/hsi_turn_trend_compare.py ORACLE_DIR OUTDIR [GUIDE_PNG] [ZOOM]
"""

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication


def rasterise(svg_path, size, png_path):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor("black"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(svg_path).render(p, QRectF(0, 0, size, size))
    p.end()
    img.save(png_path)


def main(oracle_dir, outdir, guide=None, zoom=3):
    os.makedirs(outdir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841
    d = os.environ.get("FONT_DIR")
    for f in sorted(os.listdir(d)) if d else ():
        if f.endswith(".ttf"):
            QFontDatabase.addApplicationFont(os.path.join(d, f))
    oracle = json.load(open(os.path.join(oracle_dir, "hsi_turn_trend_oracle.json")))
    s = oracle["size"]
    names = list(oracle["configs"])
    for n in names:
        rasterise(os.path.join(oracle_dir, "twin_%s.svg" % n), s,
                  os.path.join(oracle_dir, "twin_%s.png" % n))
    cols = (("pyEfis widget", "%s.png"), ("editor preview", "twin_%s.png"))
    pad, lab = 12, 20
    try:
        font = ImageFont.truetype(os.path.join(d or "", "DejaVuSans.ttf"), 14)
    except OSError:
        font = ImageFont.load_default()

    def tile(pat, name):
        return Image.open(os.path.join(oracle_dir, pat % name)).convert("RGB")

    def caption(name):
        c = oracle["configs"][name]
        o = c["options"]
        view = "arc view" if o.get("orientation") == "arc" else "rose"
        return "%s, %s, ROT %+g deg/s" % (view, o.get("readout_layout"), c["rot"])

    top = 0
    g = Image.open(guide).convert("RGB") if guide else None
    width = pad + len(cols) * (s + pad)
    if g is not None:
        if g.width > width - 2 * pad:
            g = g.resize((width - 2 * pad, round(g.height * (width - 2 * pad) / g.width)), Image.LANCZOS)
        top = lab + g.height + pad
    sheet = Image.new("RGB", (width, pad + top + len(names) * (lab + s + pad)), (40, 40, 40))
    dr = ImageDraw.Draw(sheet)
    if g is not None:
        dr.text((pad, pad), "Pilot's Guide figure (pyEfis docs/images/hsi/turn_rate_trend.png)",
                fill=(230, 230, 230), font=font)
        sheet.paste(g, (pad, pad + lab))
    for r, name in enumerate(names):
        y = pad + top + r * (lab + s + pad)
        for c, (title, pat) in enumerate(cols):
            x = pad + c * (s + pad)
            dr.text((x, y), "%s: %s" % (title, caption(name)), fill=(230, 230, 230), font=font)
            sheet.paste(tile(pat, name), (x, y + lab))
    sheet.save(os.path.join(outdir, "hsi_turn_trend_sheet.png"))

    # The trend's own band, around the lubber, zoomed: widget left, twin right.
    zsheet = None
    rows = []
    for name in names:
        c = oracle["configs"][name]
        cy = c["band_top_y"] if "band_top_y" in c else c["cy"] - c["r"]
        yc = cy / 100.0 * s
        rows.append((name, (int(s * 0.25), max(0, int(yc - s * 0.08)), int(s * 0.75), int(yc + s * 0.06))))
    cw = (rows[0][1][2] - rows[0][1][0]) * zoom
    ch = max(b[3] - b[1] for _, b in rows) * zoom
    zsheet = Image.new("RGB", (pad + 2 * (cw + pad), pad + len(rows) * (lab + ch + pad)), (40, 40, 40))
    dz = ImageDraw.Draw(zsheet)
    for r, (name, box) in enumerate(rows):
        y = pad + r * (lab + ch + pad)
        for ci, (title, pat) in enumerate(cols):
            x = pad + ci * (cw + pad)
            dz.text((x, y), "%s: %s (%dx)" % (title, caption(name), zoom), fill=(230, 230, 230), font=font)
            crop = tile(pat, name).crop(box)
            zsheet.paste(crop.resize((crop.width * zoom, crop.height * zoom), Image.NEAREST), (x, y + lab))
    zsheet.save(os.path.join(outdir, "hsi_turn_trend_zoom%dx.png" % zoom))
    print("sheet + zoom -> %s" % outdir)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None,
         int(sys.argv[4]) if len(sys.argv) > 4 else 3)
