#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-5553 (HSIBRG1 twin): compose the side-by-side sheet. One row per
config in the oracle JSON: pyEfis widget (tools/hsi_bearing_oracle.py) |
editor preview after | editor preview before (tools/hsi_bearing_capture.mjs
with PREFIX twin / base). Editor crops are resized to the widget's size so
the columns line up. Also writes a nearest-neighbour zoom of the top-right
corner (the BRG tab) for the top_panel configs.

    python3 tools/hsi_bearing_compare.py ORACLE_DIR TWIN_DIR OUTDIR [ZOOM]
"""

import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont


def main(oracle_dir, twin_dir, outdir, zoom=3):
    os.makedirs(outdir, exist_ok=True)
    oracle = json.load(open(os.path.join(oracle_dir, "hsi_bearing_oracle.json")))
    s = oracle["size"]
    names = list(oracle["configs"])
    cols = (("pyEfis widget", oracle_dir, "%s.png"),
            ("editor, this PR", twin_dir, "twin_%s.png"),
            ("editor, before", twin_dir, "base_%s.png"))
    pad, lab = 12, 20
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    def tile(d, pat, name):
        return Image.open(os.path.join(d, pat % name)).convert("RGB").resize((s, s), Image.LANCZOS)

    sheet = Image.new("RGB", (pad + len(cols) * (s + pad), pad + len(names) * (lab + s + pad)), (40, 40, 40))
    dr = ImageDraw.Draw(sheet)
    for r, name in enumerate(names):
        y = pad + r * (lab + s + pad)
        for c, (title, d, pat) in enumerate(cols):
            x = pad + c * (s + pad)
            dr.text((x, y), "%s: %s" % (title, name), fill=(230, 230, 230), font=font)
            sheet.paste(tile(d, pat, name), (x, y + lab))
    sheet.save(os.path.join(outdir, "hsi_bearing_sheet.png"))

    # The BRG tab: top-right quarter of the top band, zoomed.
    tp = [n for n in names if oracle["configs"][n]["options"].get("readout_layout") == "top_panel"]
    box = (int(s * 0.55), 0, s, int(s * 0.17))
    cw, ch = (box[2] - box[0]) * zoom, (box[3] - box[1]) * zoom
    zsheet = Image.new("RGB", (pad + 2 * (cw + pad), pad + len(tp) * (lab + ch + pad)), (40, 40, 40))
    dz = ImageDraw.Draw(zsheet)
    for r, name in enumerate(tp):
        y = pad + r * (lab + ch + pad)
        for c, (title, d, pat) in enumerate(cols[:2]):
            x = pad + c * (cw + pad)
            dz.text((x, y), "%s: %s (%dx)" % (title, name, zoom), fill=(230, 230, 230), font=font)
            zsheet.paste(tile(d, pat, name).crop(box).resize((cw, ch), Image.NEAREST), (x, y + lab))
    zsheet.save(os.path.join(outdir, "hsi_bearing_tab_zoom%dx.png" % zoom))
    print("%d configs -> %s" % (len(names), outdir))


if __name__ == "__main__":
    main(*sys.argv[1:4], *(int(a) for a in sys.argv[4:5]))
