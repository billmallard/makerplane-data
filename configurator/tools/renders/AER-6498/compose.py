# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6498: compose compare_<scene>.png -- the SVSSHADE2 bench captures
# (pyEfis docs/images/svsshade2/beelink) beside the editor twin's renders.
#   python3 compose.py <svsshade2/beelink> <twin render dir> <out dir> <DejaVuSans.ttf>
import sys
from PIL import Image, ImageDraw, ImageFont
B, NEW, OUT = sys.argv[1:4]
font = ImageFont.truetype(sys.argv[4], 18)
for s in ["coastal", "mountains", "approach", "final"]:
    cells = [(f"{B}/{s}_svs_dev1.png", "Device, dev (clearance)"),
             (f"{NEW}/{s}_clearance.png", "Editor twin, terrain_shading: clearance"),
             (f"{B}/{s}_svs_brrel.png", "Device, SVSSHADE2 (relief)"),
             (f"{NEW}/{s}_default.png", "Editor twin, default (relief)")]
    L = 28
    sheet = Image.new("RGB", (1600, 2 * (480 + L)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for k, (p, lab) in enumerate(cells):
        x, y = (k % 2) * 800, (k // 2) * (480 + L)
        sheet.paste(Image.open(p).convert("RGB").resize((800, 480)), (x, y + L))
        d.text((x + 6, y + 4), f"{s}: {lab}", fill=(255, 255, 255), font=font)
    sheet.save(f"{OUT}/compare_{s}.png", optimize=True)
