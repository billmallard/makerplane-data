#!/usr/bin/env python3
"""Draws nav_source_tab_twin_before_after.png from /tmp/tab_values.json
(produced by compute_values.mjs). Run from the repo root:
    node docs/images/aer-474/compute_values.mjs
    python3 docs/images/aer-474/render_evidence.py
"""
import json
from PIL import Image, ImageDraw, ImageFont

with open("/tmp/tab_values.json") as f:
    v = json.load(f)

W, H = 900, 420
img = Image.new("RGB", (W, H), (24, 24, 28))
d = ImageDraw.Draw(img)

try:
    font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    font_cap = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
except Exception:
    font_label = font_title = font_cap = ImageFont.load_default()


def draw_tab(draw, x, y, w, h, fill, label, label_color):
    r = h // 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill)
    bbox = draw.textbbox((0, 0), label, font=font_label)
    tw = bbox[2] - bbox[0]
    draw.text((x + w / 2 - tw / 2, y + h / 2 - (bbox[3] - bbox[1]) / 2 - bbox[1]),
              label, font=font_label, fill=label_color)


d.text((20, 12), "Configurator JS twin -- nav-source tab (AER-474, lockstep with AER-473/pyEfis#147+#148)",
       font=font_title, fill=(230, 230, 230))
d.text((20, 46), "Synthetic reproduction: this sandbox has no libGL/Chromium GUI stack to screenshot the live",
       font=font_cap, fill=(160, 160, 160))
d.text((20, 66), "configurator page, so tab geometry + colours are drawn with PIL from the ACTUAL ported darkenToContrast()",
       font=font_cap, fill=(160, 160, 160))
d.text((20, 86), "output (compute_values.mjs, evaluated against configurator/public/editor.html), not re-guessed.",
       font=font_cap, fill=(160, 160, 160))

PXSCALE = 6
tabH = 34

d.text((60, 130), "BEFORE (hardcoded white, width tracks label)", font=font_cap, fill=(200, 200, 200))
bx, by = 80, 165
draw_tab(d, bx, by, v["gpsWidthBefore"] * PXSCALE, tabH, v["gpsFill"], "GPS", "#ffffff")
d.text((bx, by + tabH + 6), f'w={v["gpsWidthBefore"]:.1f}u  label=#ffffff', font=font_cap, fill=(150, 150, 150))

by2 = by + 70
draw_tab(d, bx, by2, v["vlocWidthBefore"] * PXSCALE, tabH, v["vlocFill"], "VLOC1", "#ffffff")
d.text((bx, by2 + tabH + 6), f'w={v["vlocWidthBefore"]:.1f}u  label=#ffffff', font=font_cap, fill=(150, 150, 150))
d.text((bx, by2 + tabH + 26), 'width changes as NAVSRC cycles -- tab "breathes"', font=font_cap, fill=(220, 140, 60))

ax = 500
d.text((ax - 20, 130), "AFTER (derived dark-tint label, constant width)", font=font_cap, fill=(200, 200, 200))
ay = 165
draw_tab(d, ax, ay, v["constTw"] * PXSCALE, tabH, v["gpsFill"], "GPS", v["gpsLabelAfter"])
d.text((ax, ay + tabH + 6), f'w={v["constTw"]:.1f}u (constant)  label={v["gpsLabelAfter"]} (contrast 4.52:1)',
       font=font_cap, fill=(150, 150, 150))

ay2 = ay + 70
draw_tab(d, ax, ay2, v["constTw"] * PXSCALE, tabH, v["vlocFill"], "VLOC1", v["vlocLabelAfter"])
d.text((ax, ay2 + tabH + 6), f'w={v["constTw"]:.1f}u (constant)  label={v["vlocLabelAfter"]} (contrast 4.61:1)',
       font=font_cap, fill=(150, 150, 150))
d.text((ax, ay2 + tabH + 26), "same width for every source label -- widest of GPS/VOR{n}/LOC{n}/VLOC{n}",
       font=font_cap, fill=(100, 200, 120))

d.text((20, H - 40), "Colours/widths are the real ported values -- not hand-picked -- from darkenToContrast()",
       font=font_cap, fill=(120, 120, 120))
d.text((20, H - 22), "and the SOURCE_TAB_LABELS width basis added in configurator/public/editor.html.",
       font=font_cap, fill=(120, 120, 120))

img.save("docs/images/aer-474/nav_source_tab_twin_before_after.png")
print("saved docs/images/aer-474/nav_source_tab_twin_before_after.png")
