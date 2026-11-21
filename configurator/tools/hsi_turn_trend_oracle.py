#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-6089 (ATT1b twin of pyEfis#520): render the REAL pyEfis HSI with a
turn rate on ROT and dump the turn-rate trend it drew.

The widget is built at the editor preview's sample pose (heading 87, bug 50,
course 110) and painted once. Then its own _draw_turn_trend_rose /
_draw_turn_trend_arc run again on a recording painter, so the dump is what
the widget draws, not a reading of its source:

- `<name>.png`, the widget grab (square, so it lines up with the twin's
  0..100 viewBox);
- `hsi_turn_trend_oracle.json`: per config, every mark line, the arc (as its
  circle and sweep on the rose, as its polyline points in the arc view) and
  the arrowhead polygon, with pen widths, all in 0..100 viewBox units; the
  ring (rose) or the band's y at the lubber (arc view) they hang off.

    QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis [FONT_DIR=/path/to/dejavu] \\
      python3 tools/hsi_turn_trend_oracle.py OUTDIR [SIZE]
"""

import json
import os
import subprocess
import sys
import time

PYEFIS = os.environ["PYEFIS"]
sys.path.insert(0, PYEFIS)
sys.path.insert(0, os.path.join(PYEFIS, "src"))

from tools import render_instrument as ri  # noqa: E402  (installs the mock FIX db)

import pyavtools.fix as fix  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

# (name, rot deg/s, options). Standard rate is the preview's own sample; the
# 5 deg/s configs exercise the capped arc and its arrowhead.
CONFIGS = (
    ("rose_top_panel_r3", 3.0, dict(readout_layout="top_panel")),
    ("rose_corners_r3", 3.0, dict(readout_layout="corners")),
    ("rose_top_panel_l5", -5.0, dict(readout_layout="top_panel")),
    ("arc_top_panel_r3", 3.0, dict(readout_layout="top_panel", orientation="arc")),
    ("arc_corners_r5", 5.0, dict(readout_layout="corners", orientation="arc")),
)


def load_font_dir():
    """FONT_DIR (optional): register every .ttf in it, for a host without
    DejaVu where render_instrument looks for it. Text only; no geometry in
    the dump depends on it."""
    from PyQt6.QtGui import QFontDatabase
    d = os.environ.get("FONT_DIR")
    for f in sorted(os.listdir(d)) if d else ():
        if f.endswith(".ttf"):
            QFontDatabase.addApplicationFont(os.path.join(d, f))


def _settle(app, n=30):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def _seed(rot):
    for key, val, lo, hi in (("HEAD", 87.0, 0.0, 359.9), ("HEADBUG", 50.0, 0.0, 359.9),
                             ("COURSE", 110.0, 0.0, 359.9), ("NAVSRC", 2.0, 0.0, 3.0),
                             ("CDI", 0.3, -1.0, 1.0), ("GSI", -0.2, -1.0, 1.0),
                             ("TOFROM", 1.0, 0.0, 3.0), ("TRACK", 78.0, 0.0, 359.9),
                             ("CDIV", 1.0, 0.0, 3.0), ("GSV", 1.0, 0.0, 3.0),
                             ("ROT", rot, -30.0, 30.0)):
        fix.db.define_item(key, key, "float", lo, hi, "deg", 50000, "")
        fix.db.set_value(key, val)
        it = fix.db.get_item(key)
        it.bad = it.fail = it.old = False


class Rec(QPainter):
    """Records the trend's primitives in widget pixels."""

    def __init__(self, dev):
        super().__init__(dev)
        self.ops = []

    def _w(self):
        return self.pen().widthF()

    def drawLine(self, ln):
        self.ops.append({"op": "line", "w": self._w(), "color": self.pen().color().name(),
                         "pts": [[ln.x1(), ln.y1()], [ln.x2(), ln.y2()]]})

    def drawArc(self, rect, start, span):
        self.ops.append({"op": "arc", "w": self._w(), "color": self.pen().color().name(),
                         "cap": self.pen().capStyle().name,
                         "cx": rect.center().x(), "cy": rect.center().y(),
                         "r": rect.width() / 2.0, "start": start / 16.0, "span": span / 16.0})

    def drawPolyline(self, poly):
        self.ops.append({"op": "polyline", "w": self._w(), "color": self.pen().color().name(),
                         "cap": self.pen().capStyle().name,
                         "pts": [[p.x(), p.y()] for p in poly]})

    def drawPolygon(self, poly):
        self.ops.append({"op": "polygon", "color": self.brush().color().name(),
                         "pts": [[p.x(), p.y()] for p in poly]})


def _scale(ops, k):
    out = []
    for o in ops:
        o = dict(o)
        if "w" in o:
            o["w"] *= k
        if "pts" in o:
            o["pts"] = [[x * k, y * k] for x, y in o["pts"]]
        for f in ("cx", "cy", "r"):
            if f in o:
                o[f] *= k
        out.append(o)
    return out


def render(app, size, rot, opts):
    from pyefis.instruments.hsi import HSI

    _seed(rot)
    host = QWidget()
    host.setStyleSheet("background: black")
    host.resize(size, size)
    h = HSI(host, cdi_enabled=True, gsi_enabled=True,
            font_family="DejaVu Sans Condensed")
    for k, v in dict(heading_bug_enabled=True, track_indicator_enabled=True,
                     source_auto_color=True, **opts).items():
        setattr(h, k, v)
    h.setGeometry(0, 0, size, size)
    host.show()
    _settle(app, 50)
    h.viewport().repaint()
    _settle(app)
    assert h._turn_trend_shown(), "trend hidden: ROT not live"
    assert abs(h._rot - rot) < 1e-9, "ROT not seeded"
    img = host.grab().toImage()

    scratch = QImage(size, size, QImage.Format.Format_ARGB32)
    rec = Rec(scratch)
    if opts.get("orientation") == "arc":
        h._draw_turn_trend_arc(rec)
    else:
        h._draw_turn_trend_rose(rec)
    rec.end()
    k = 100.0 / size
    out = {"rot": rot, "font_size": h.fontSize * k, "ops": _scale(rec.ops, k)}
    if opts.get("orientation") == "arc":
        out.update(band_top_y=h._arc_params()[1] * k)   # band y at the lubber
    else:
        out.update(r=h.r * k, cx=h.cx * k, cy=h.cy * k)
    host.close()
    return img, out


def main(outdir, size):
    os.makedirs(outdir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    load_font_dir()
    ri._load_fonts()
    ri._setup_fix()
    import pyefis.hmi as hmi
    if hmi.actions is None:
        hmi.initialize({})
    commit = subprocess.run(["git", "-C", PYEFIS, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dump = {"size": size, "pyefis_commit": commit, "configs": {}}
    for name, rot, opts in CONFIGS:
        img, out = render(app, size, rot, opts)
        img.save(os.path.join(outdir, "%s.png" % name))
        dump["configs"][name] = {"options": opts, **out}
    with open(os.path.join(outdir, "hsi_turn_trend_oracle.json"), "w") as f:
        json.dump(dump, f, indent=1, sort_keys=True)
        f.write("\n")
    print("%d configs at %d px -> %s" % (len(CONFIGS), size, outdir))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 425)
