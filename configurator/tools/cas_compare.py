# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6356: compose the cas_window / cas_marker side-by-side. Per scene of
# tools/cas_scenes.json: the real widget rendered offscreen
# (tools/cas_oracle.py --render, composited on black), the editor twin
# (tools/cas_capture.mjs, on black) and their difference (white where any
# channel differs by more than 64), each upscaled ZOOM x nearest-neighbour so
# a pixel offset stays visible. Under each scene it prints the share of
# pixels that differ by more than 64 in any channel.
#
#   python3 tools/cas_compare.py RENDER_DIR OUT.png [ZOOM]
#
# RENDER_DIR holds dev_<name>.png and twin_<name>.png for each scene.

import json
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))


def main(render_dir, out_path, zoom="2"):
    zoom = int(zoom)
    scenes = json.load(open(os.path.join(HERE, "cas_scenes.json")))
    font = ImageFont.load_default()
    rows = []
    for s in scenes:
        d = Image.open("%s/dev_%s.png" % (render_dir, s["name"])).convert("RGBA")
        dev = Image.new("RGB", d.size, (0, 0, 0))
        dev.paste(d, (0, 0), d)
        twin = Image.open("%s/twin_%s.png" % (render_dir, s["name"])).convert("RGB")
        diff = ImageChops.difference(dev, twin)
        mask = diff.point(lambda v: 255 if v > 64 else 0).convert("L")
        hist = mask.histogram()
        share = 100.0 * sum(hist[1:]) / (mask.width * mask.height)
        mask_rgb = Image.merge("RGB", (mask, mask, mask))
        print("%-18s %dx%d  differing %.2f%%" % (s["name"], s["w"], s["h"], share))
        rows.append((s, [im.resize((im.width * zoom, im.height * zoom), Image.NEAREST)
                         for im in (dev, twin, mask_rgb)], share))
    width = 20 + max(sum(im.width for im in ims) + 20 * 2 for _s, ims, _p in rows)
    height = 10 + sum(34 + ims[0].height for _s, ims, _p in rows)
    sheet = Image.new("RGB", (width, height), (40, 40, 40))
    dr = ImageDraw.Draw(sheet)
    y = 8
    for s, ims, share in rows:
        dr.text((10, y), "%s  (%dx%d device px, shown %dx)  -  pyEfis widget | editor twin | "
                "diff >64: %.2f%% of pixels" % (s["caption"], s["w"], s["h"], zoom, share),
                fill=(230, 230, 230), font=font)
        y += 18
        x = 10
        for im in ims:
            sheet.paste(im, (x, y))
            x += im.width + 20
        y += ims[0].height + 16
    sheet.save(out_path)


if __name__ == "__main__":
    main(*sys.argv[1:4])
