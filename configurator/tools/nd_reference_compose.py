# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5272 (G11b twin): put a pyEfis ND evidence render (real packs, seeded
# ND data, from pyEfis tools/render_nd_evidence.py) beside the editor twin at
# the same size, so a reviewer sees what the twin draws and what it leaves to
# the device (terrain, airports, data blocks fed by the bus).
#
#   python3 tools/nd_reference_compose.py DEVICE.png TWIN.png OUT.png "device caption"

import sys

from PIL import Image, ImageDraw


def main(device_png, twin_png, out_png, caption):
    dev = Image.open(device_png).convert("RGB")
    twin = Image.open(twin_png).convert("RGB").resize(dev.size, Image.LANCZOS)
    w, h = dev.size
    pad, cap = 12, 28
    out = Image.new("RGB", (2 * w + 3 * pad, h + cap + 2 * pad), (40, 40, 40))
    d = ImageDraw.Draw(out)
    for i, (img, label) in enumerate([(dev, caption), (twin, "configurator twin (editor.html)")]):
        x = pad + i * (w + pad)
        out.paste(img, (x, pad + cap))
        d.text((x, pad + 6), label, fill=(230, 230, 230))
    out.save(out_png)


if __name__ == "__main__":
    main(*sys.argv[1:5])
