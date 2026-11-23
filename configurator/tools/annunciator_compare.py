# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6348: compose the annunciator side-by-side. Per row, at 2x nearest
# neighbour on black: the real widget rendered offscreen
# (tools/annunciator_oracle.py --render, dev_<tag>.png), the editor twin
# (tools/annunciator_capture.mjs, twin_<tag>.png), and an overlay of the two
# (device red, twin cyan: white where they agree). Under each row it prints,
# for the device and the twin, the pixel span of the caption ink in each
# tile row, so the alignment is a number as well as a picture.
#
#   python3 tools/annunciator_compare.py RENDER_DIR OUT.png TAG:caption ...
#   python3 tools/annunciator_compare.py --glass SHEET.png X,Y DEV.png TWIN.png OUT.png

import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont

SCALE = 2


def on_black(path):
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    return Image.alpha_composite(bg, im).convert("RGB")


def overlay(a, b):
    ga, gb = a.convert("L"), b.convert("L")
    return Image.merge("RGB", (ga, gb, gb))


def ink_span(im, y0, y1):
    """x range of bright pixels (caption and outline) between rows y0..y1."""
    g = im.convert("L").crop((0, y0, im.width, y1)).point(lambda v: 255 if v > 140 else 0)
    box = g.getbbox()
    return (box[0], box[2]) if box else None


def main(rdir, out, tags):
    rows = []
    for t in tags:
        tag, _, cap = t.partition(":")
        dev, twin = on_black(f"{rdir}/dev_{tag}.png"), on_black(f"{rdir}/twin_{tag}.png")
        diff = ImageChops.difference(dev.convert("L"), twin.convert("L")).point(lambda v: 255 if v > 96 else 0)
        n = diff.histogram()[255]
        h = dev.height
        spans = [(ink_span(dev, int(h * k / 2), int(h * (k + 1) / 2)),
                  ink_span(twin, int(h * k / 2), int(h * (k + 1) / 2))) for k in range(2)]
        rows.append((cap, [dev, twin, overlay(dev, twin)], n, dev.width * dev.height, spans))
    w0, h0 = rows[0][1][0].size
    pad, head, foot = 12, 22, 34
    W = 3 * w0 * SCALE + 4 * pad
    H = sum(head + h0 * SCALE + foot for _ in rows) + pad
    sheet = Image.new("RGB", (W, H), (36, 40, 48))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    y = pad
    for cap, ims, n, total, spans in rows:
        d.text((pad, y + 3), cap + "   (left: pyEfis offscreen, middle: editor twin, right: overlay)", fill=(230, 230, 230), font=font)
        y += head
        for i, im in enumerate(ims):
            sheet.paste(im.resize((im.width * SCALE, im.height * SCALE), Image.NEAREST),
                        (pad + i * (w0 * SCALE + pad), y))
        y += h0 * SCALE + 4
        txt = "bright-pixel span per tile row, device / twin: " + "   ".join(
            f"row {k + 1}: {a} / {b}" for k, (a, b) in enumerate(spans))
        d.text((pad, y), txt + f"   differing px (>96 levels): {n} of {total}", fill=(180, 180, 180), font=font)
        y += foot - 4
    sheet.save(out)


def vs_glass(sheet_png, origin, dev_png, twin_png, out):
    """Frame 7 of the ANN1 Beelink sheet (pyEfis docs/images/ann1_annunciator/
    sheet.png, 2x nearest of the glass) beside the device render and the twin
    of the same state, all at 2x. ORIGIN is the block's top-left in the sheet."""
    sheet = Image.open(sheet_png).convert("RGB")
    dev, twin = on_black(dev_png), on_black(twin_png)
    w, h = dev.width * SCALE, dev.height * SCALE
    glass = sheet.crop((origin[0], origin[1], origin[0] + w, origin[1] + h))
    pad, head = 12, 22
    canvas = Image.new("RGB", (3 * w + 4 * pad, h + head + 2 * pad), (36, 40, 48))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    for i, (im, cap) in enumerate(((glass, "Beelink glass (ANN1 sheet, frame 7)"),
                                   (dev.resize((w, h), Image.NEAREST), "pyEfis offscreen"),
                                   (twin.resize((w, h), Image.NEAREST), "editor twin"))):
        x = pad + i * (w + pad)
        d.text((x, pad - 4), cap, fill=(230, 230, 230), font=font)
        canvas.paste(im, (x, pad + head - 4))
    canvas.save(out)


if __name__ == "__main__":
    if sys.argv[1] == "--glass":
        # --glass SHEET.png X,Y DEV.png TWIN.png OUT.png
        vs_glass(sys.argv[2], tuple(int(v) for v in sys.argv[3].split(",")),
                 sys.argv[4], sys.argv[5], sys.argv[6])
    else:
        main(sys.argv[1], sys.argv[2], sys.argv[3:])
