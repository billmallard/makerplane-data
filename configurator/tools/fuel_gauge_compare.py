# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5547 (FUEL1f): compose the PR evidence from the two halves in DIR.
#   device_labels.png  tools/fuel_gauge_oracle.py --render (the real widget)
#   twin_labels.png    tools/fuel_gauge_capture.mjs (the editor twin)
# compare_labels.png is the device on the left and the twin on the right,
# the twin scaled to the device's height, then both 2x nearest-neighbour.
# The capture's full-page shots are cropped to the canvas and the panel.
#
#   python3 tools/fuel_gauge_compare.py DIR

import sys

from PIL import Image


def main(out):
    d = Image.open(f"{out}/device_labels.png").convert("RGB")
    t = Image.open(f"{out}/twin_labels.png").convert("RGB")
    t = t.resize((round(t.width * d.height / t.height), d.height), Image.NEAREST)
    c = Image.new("RGB", (d.width + 24 + t.width, d.height), (40, 40, 40))
    c.paste(d, (0, 0))
    c.paste(t, (d.width + 24, 0))
    c.resize((c.width * 2, c.height * 2), Image.NEAREST).save(f"{out}/compare_labels.png")
    for name in ("orphan_props", "migrate_hover", "migrate_done"):
        im = Image.open(f"{out}/{name}.png")
        if im.size == (1600, 1000):
            im.crop((240, 95, 1600, 1000)).save(f"{out}/{name}.png")


if __name__ == "__main__":
    main(sys.argv[1])
