# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-4461: compose the twin-vs-device evidence image for one scene. Left: the
# pyEfis MovingMap widget render. Middle: the editor twin (map element crop,
# scaled to the same 1024x600). Right: an overlay -- device luminance in the
# red channel, twin luminance in green+blue -- so where they agree it reads
# white/grey and any disagreement shows as a red or cyan fringe.
#
#   python3 tools/range_rings_compare.py DEVICE.png TWIN.png OUT.png

import sys

from PIL import Image, ImageDraw


def main(device_png, twin_png, out_png):
    dev = Image.open(device_png).convert("L")
    twin = Image.open(twin_png).convert("RGB").resize(dev.size, Image.LANCZOS)
    overlay = Image.merge("RGB", (dev, twin.convert("L"), twin.convert("L")))
    w, h = dev.size
    pad, cap = 12, 28
    out = Image.new("RGB", (3 * w + 4 * pad, h + cap + 2 * pad), (40, 40, 40))
    d = ImageDraw.Draw(out)
    for i, (img, label) in enumerate([(Image.open(device_png).convert("RGB"), "pyEfis MovingMap (device)"),
                                      (twin, "configurator twin (editor.html)"),
                                      (overlay, "overlay: red = device only, cyan = twin only")]):
        x = pad + i * (w + pad)
        out.paste(img, (x, pad + cap))
        d.text((x, pad + 6), label, fill=(230, 230, 230))
    out.save(out_png)


if __name__ == "__main__":
    main(*sys.argv[1:4])
