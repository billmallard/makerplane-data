# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5654: compose the DATA flag widget-vs-twin evidence. One row per size:
# the pyEfis DataAnnunciation render (tools/data_flag_oracle.py --render), the
# editor twin (tools/data_flag_twin_capture.mjs), and an overlay with device
# luminance in red and twin luminance in green+blue, so agreement reads
# white/grey and a disagreement shows as a red or cyan fringe. Upscaled by
# SCALE, nearest neighbour, so single pixels stay visible.
#
#   python3 tools/data_flag_compare.py OUT.png SCALE DEVICE1.png TWIN1.png [DEVICE2.png TWIN2.png ...]

import sys

from PIL import Image, ImageDraw


def main(out_png, scale, pairs):
    rows = []
    for dev_png, twin_png in pairs:
        dev = Image.open(dev_png).convert("RGB")
        twin = Image.open(twin_png).convert("RGB")
        assert dev.size == twin.size, (dev_png, dev.size, twin_png, twin.size)
        lum = lambda im: im.convert("L")  # noqa: E731
        overlay = Image.merge("RGB", (lum(dev), lum(twin), lum(twin)))
        rows.append([im.resize((im.width * scale, im.height * scale), Image.NEAREST)
                     for im in (dev, twin, overlay)])
    pad, cap = 12, 22
    cw = max(r[0].width for r in rows)
    out = Image.new("RGB", (3 * cw + 4 * pad,
                            sum(r[0].height + cap + pad for r in rows) + pad), (40, 40, 40))
    d = ImageDraw.Draw(out)
    y = pad
    for (dev_png, _), row in zip(pairs, rows):
        w, h = row[0].width // scale, row[0].height // scale
        labels = [f"pyEfis DataAnnunciation, amber, {w}x{h}",
                  "configurator twin (editor.html)",
                  "overlay: red = device only, cyan = twin only"]
        for i, (img, label) in enumerate(zip(row, labels)):
            x = pad + i * (cw + pad)
            d.text((x, y + 4), label, fill=(230, 230, 230))
            out.paste(img, (x, y + cap))
        y += row[0].height + cap + pad
    out.save(out_png)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], int(a[1]), list(zip(a[2::2], a[3::2])))
