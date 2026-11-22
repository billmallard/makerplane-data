# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6286: compose the ap_annunciator side-by-side against the Pi 5 sheet on
# pyEfis #499 (docs/aer-5808-evidence/joint/pi5). Per scene, three rows at
# 1:1: the strip cropped from the Pi 5 glass capture (its sidecar puts
# ModeAnnunciator at x 144, y 0, 1046 x 55), the real widget rendered
# offscreen (tools/ap_annunciator_oracle.py --render) and the editor twin
# (tools/ap_annunciator_capture.mjs). The two renders sit on row 60 of the
# Pi frame stretched to the strip's height: the sky just under the strip,
# above the tapes, so they read against the same ground as the glass. Under
# each pair it prints where the bright text and box pixels start and end in
# each row, so the alignment is a number as well as a picture.
#
#   python3 tools/ap_annunciator_compare.py PI5_DIR RENDER_DIR OUT.png
#
# RENDER_DIR holds dev_<scene>.png (transparent, from the oracle) and
# twin_<scene>.png (on the bg, from the capture script) for each scene below.
# Every run writes bg_<scene>.png there, the capture script's BG; with OUT
# "--bg" it writes only those.

import sys

from PIL import Image, ImageDraw, ImageFont

STRIP = (144, 0, 1190, 55)
SCENES = [("mtn_climb", "climb: HDG, GPS armed, IAS 90, ALT armed"),
          ("ksba_final", "KSBA final: GPS just captured (boxed, flash on), VS -500"),
          ("ksba_disc", "AP disconnect: red boxed AP")]


def ground(pi_frame):
    return pi_frame.crop((STRIP[0], 60, STRIP[2], 61)).resize(
        (STRIP[2] - STRIP[0], STRIP[3] - STRIP[1]))


def ink_columns(img):
    """x ranges of saturated green, red or white pixels (text and boxes)."""
    px = img.convert("RGB").load()
    w, h = img.size
    cols = []
    for x in range(w):
        hit = False
        for y in range(h):
            r, g, b = px[x, y]
            if (g > 200 and r < 120 and b < 120) or (r > 200 and g < 90) or \
                    (r > 215 and g > 215 and b > 215):
                hit = True
                break
        cols.append(hit)
    runs, start = [], None
    for x, on in enumerate(cols + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= 2:
                runs.append((start, x - 1))
            start = None
    return runs


def merge(runs, gap=12):
    out = []
    for a, b in runs:
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def main(pi_dir, render_dir, out_path):
    font = ImageFont.load_default()
    for scene, _caption in SCENES:
        frame = Image.open("%s/%s_single_cue.png" % (pi_dir, scene)).convert("RGB")
        ground(frame).save("%s/bg_%s.png" % (render_dir, scene))
    if out_path == "--bg":
        return
    rows = []
    for scene, caption in SCENES:
        frame = Image.open("%s/%s_single_cue.png" % (pi_dir, scene)).convert("RGB")
        bg = ground(frame)
        glass = frame.crop(STRIP)
        dev = bg.copy()
        d = Image.open("%s/dev_%s.png" % (render_dir, scene)).convert("RGBA")
        dev.paste(d, (0, 0), d)
        twin = Image.open("%s/twin_%s.png" % (render_dir, scene)).convert("RGB")
        g, v, t = (merge(ink_columns(i)) for i in (glass, dev, twin))
        rows.append(("Pi 5 glass - " + caption, glass))
        rows.append(("pyEfis widget, offscreen - same state", dev))
        rows.append(("editor twin - same state", twin))
        rows.append(("ink x-ranges  glass %s\n              widget %s\n              twin   %s"
                     % (g, v, t), None))
        print(scene, "glass", g)
        print(scene, "widget", v)
        print(scene, "twin", t)
    w = STRIP[2] - STRIP[0] + 20
    h = 8 + sum(18 + (57 if im else 30) for _c, im in rows)
    sheet = Image.new("RGB", (w, h), (30, 30, 30))
    dr = ImageDraw.Draw(sheet)
    y = 6
    for cap, im in rows:
        dr.text((10, y), cap, fill=(220, 220, 220), font=font)
        if im is None:
            y += 48
            continue
        y += 16
        sheet.paste(im, (10, y))
        y += 59
    sheet.save(out_path)


if __name__ == "__main__":
    main(*sys.argv[1:4])
