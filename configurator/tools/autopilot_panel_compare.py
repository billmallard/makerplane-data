# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5812: compose the autopilot_panel device-vs-twin evidence. One block per
# pair, stacked: the device frame (a Pi 5 sheet crop or
# tools/autopilot_panel_oracle.py --render), the editor twin
# (tools/autopilot_panel_capture.mjs), and an overlay with device luminance in
# red and twin luminance in green+blue, so agreement reads white/grey and a
# disagreement shows as a red or cyan fringe. Each block's caption also gives
# the share of pixels whose luminance differs by more than 48 of 255.
#
#   python3 tools/autopilot_panel_compare.py OUT.png SCALE \
#       "LABEL" DEVICE.png TWIN.png ["LABEL" DEVICE.png TWIN.png ...]
#
# A DEVICE argument may be SHEET.png@X,Y,W,H, a crop of a capture sheet.

import sys

from PIL import Image, ImageChops, ImageDraw


def _open(arg):
    path, _, box = arg.partition("@")
    im = Image.open(path).convert("RGB")
    if box:
        x, y, w, h = (int(v) for v in box.split(","))
        im = im.crop((x, y, x + w, y + h))
    return im


def main(out_png, scale, triples):
    blocks = []
    for label, dev_arg, twin_png in triples:
        dev, twin = _open(dev_arg), _open(twin_png)
        assert dev.size == twin.size, (dev_arg, dev.size, twin_png, twin.size)
        ld, lt = dev.convert("L"), twin.convert("L")
        diff = ImageChops.difference(ld, lt).point(lambda v: 255 if v > 48 else 0)
        share = diff.histogram()[255] / (dev.width * dev.height)
        overlay = Image.merge("RGB", (ld, lt, lt))
        imgs = [im.resize((im.width * scale, im.height * scale), Image.NEAREST)
                for im in (dev, twin, overlay)]
        caps = [f"{label} -- device", "configurator twin (editor.html)",
                f"overlay: red = device only, cyan = twin only; "
                f"{100 * share:.1f}% of pixels differ by > 48/255"]
        blocks.append((imgs, caps))
    pad, cap = 12, 22
    cw = max(b[0][0].width for b in blocks)
    H = sum(sum(im.height + cap for im in b[0]) + 2 * pad for b in blocks) + pad
    out = Image.new("RGB", (cw + 2 * pad, H), (40, 40, 40))
    d = ImageDraw.Draw(out)
    y = pad
    for imgs, caps in blocks:
        for im, c in zip(imgs, caps):
            d.text((pad, y + 5), c, fill=(230, 230, 230))
            out.paste(im, (pad, y + cap))
            y += im.height + cap
        y += 2 * pad
    out.save(out_png)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], int(a[1]), list(zip(a[2::3], a[3::3], a[4::3])))
