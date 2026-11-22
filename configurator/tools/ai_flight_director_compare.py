#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-6304: compose the flight director side-by-side and measure it.

Inputs: ORACLE_DIR (tools/ai_flight_director_oracle.py: widget grabs),
CAPTURE_DIR (tools/ai_flight_director_capture.mjs: editor renders) and
pyEfis's docs/aer-6052-evidence/styles.png. Writes to OUTDIR:

- ai_fd_sheet.png: per style, three rows at styles.png's four commands:
  styles.png's own cell, the widget re-rendered by the oracle, the editor.
- ai_fd_zoom3x.png: the "both" command (the editor's preview sample) around
  the aircraft symbol, widget left and editor right, 3x nearest-neighbour.
- ai_fd_preview.png: what the editor shows for single_cue, cross_pointer
  and off, beside the option field; below, the virtual_vfr (SVS) preview.

and prints, per cell, how the magenta (FD fill) pixels of widget and editor
overlap (intersection over union, and centroid offset in widget px).

    python3 tools/ai_flight_director_compare.py ORACLE_DIR CAPTURE_DIR STYLES_PNG OUTDIR
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

W, H, CAP = 400, 300, 22
COLS = ("satisfied", "climb", "turn", "both")
STYLES = ("single_cue", "cross_pointer")
# styles.png: 4 columns of 400 x (300 + 40 px caption); rows 0 and 1 are
# single_cue and cross_pointer on the classic symbol.
SHEET_CAP = 40


def font():
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              os.path.join(os.environ.get("FONT_DIR", ""), "DejaVuSans.ttf")):
        if os.path.exists(p):
            return ImageFont.truetype(p, 14)
    return ImageFont.load_default()


def styles_cell(sheet, row, col):
    y = row * (H + SHEET_CAP) + SHEET_CAP
    return sheet.crop((col * W, y, col * W + W, y + H)).convert("RGB")


def editor(path):
    return Image.open(path).convert("RGB").resize((W, H), Image.LANCZOS)


def magenta(img):
    px = img.load()
    return {(x, y) for y in range(img.height) for x in range(img.width)
            if px[x, y][0] > 170 and px[x, y][1] < 90 and px[x, y][2] > 170}


def overlap(a, b):
    ma, mb = magenta(a), magenta(b)
    if not ma and not mb:
        return "no FD pixels on either side"
    iou = len(ma & mb) / len(ma | mb)
    ca = [sum(c) / len(ma) for c in zip(*ma)]
    cb = [sum(c) / len(mb) for c in zip(*mb)]
    return "IoU %.3f, centroid offset (%+.2f, %+.2f) px, %d vs %d px" % (
        iou, cb[0] - ca[0], cb[1] - ca[1], len(ma), len(mb))


def labelled(cells, cols, labels):
    rows = (len(cells) + cols - 1) // cols
    out = Image.new("RGB", (cols * W, rows * (H + CAP)), (24, 24, 24))
    d = ImageDraw.Draw(out)
    f = font()
    for i, (im, lab) in enumerate(zip(cells, labels)):
        x, y = (i % cols) * W, (i // cols) * (H + CAP)
        out.paste(im, (x, y + CAP))
        d.text((x + 6, y + 3), lab, fill=(255, 255, 255), font=f)
    return out


def main(oracle_dir, cap_dir, styles_png, outdir):
    os.makedirs(outdir, exist_ok=True)
    ref = Image.open(styles_png)
    cells, labels = [], []
    for si, style in enumerate(STYLES):
        rows = (("styles.png", lambda c: styles_cell(ref, si, COLS.index(c))),
                ("widget", lambda c: Image.open(os.path.join(
                    oracle_dir, "%s_%s.png" % (style, c))).convert("RGB")),
                ("editor", lambda c: editor(os.path.join(
                    cap_dir, "twin_%s_%s.png" % (style, c)))))
        for who, get in rows:
            for c in COLS:
                cells.append(get(c))
                labels.append("%s / %s: %s" % (who, style, c))
        for c in COLS:
            print("%-13s %-9s widget vs editor: %s" % (style, c, overlap(
                cells[-8 + COLS.index(c)], cells[-4 + COLS.index(c)])))
            print("%-13s %-9s styles.png vs editor: %s" % (style, c, overlap(
                cells[-12 + COLS.index(c)], cells[-4 + COLS.index(c)])))
    labelled(cells, 4, labels).save(os.path.join(outdir, "ai_fd_sheet.png"))

    box = (110, 60, 290, 210)
    zc, zl = [], []
    for style in STYLES:
        for who, im in (("widget", Image.open(os.path.join(
                oracle_dir, "%s_both.png" % style)).convert("RGB")),
                        ("editor", Image.open(os.path.join(
                            cap_dir, "twin_%s_both.png" % style)).convert("RGB"))):
            k = im.width / W
            crop = im.crop(tuple(int(round(v * k)) for v in box))
            crop = crop.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
            zc.append(crop.resize((crop.width * 3, crop.height * 3), Image.NEAREST))
            zl.append("%s / %s: both (3x)" % (who, style))
    zw, zh = zc[0].size
    out = Image.new("RGB", (2 * zw, 2 * (zh + CAP)), (24, 24, 24))
    d = ImageDraw.Draw(out)
    for i, (im, lab) in enumerate(zip(zc, zl)):
        x, y = (i % 2) * zw, (i // 2) * (zh + CAP)
        out.paste(im, (x, y + CAP))
        d.text((x + 6, y + 3), lab, fill=(255, 255, 255), font=font())
    out.save(os.path.join(outdir, "ai_fd_zoom3x.png"))

    pv = [editor(os.path.join(cap_dir, "preview_%s.png" % s))
          for s in ("single_cue", "cross_pointer", "off")]
    blank = Image.new("RGB", (W, H), (24, 24, 24))
    pv += [blank] + [editor(os.path.join(cap_dir, "vfr_%s.png" % s))
                     for s in ("single_cue", "cross_pointer")]
    opt = Image.open(os.path.join(cap_dir, "options_panel.png")).convert("RGB")
    sheet = labelled(pv, 4, ["editor preview: single_cue", "editor preview: cross_pointer",
                             "editor preview: off", "",
                             "virtual_vfr preview: single_cue",
                             "virtual_vfr preview: cross_pointer"])
    sheet.paste(opt.resize((min(W, opt.width), int(opt.height * min(W, opt.width) / opt.width))),
                (3 * W + 10, CAP + 10))
    ImageDraw.Draw(sheet).text((3 * W + 6, 3), "option form (dev schema)", fill=(255, 255, 255),
                               font=font())
    sheet.save(os.path.join(outdir, "ai_fd_preview.png"))
    print("wrote", outdir)


if __name__ == "__main__":
    main(*sys.argv[1:5])
