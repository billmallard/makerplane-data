#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-5553 (HSIBRG1 twin): render the REAL pyEfis HSI at boot for each
bearing-pointer config the editor preview twins, and dump what it drew.

A static preview shows the panel as it boots: bearingN_enabled only makes a
pointer available, and each starts Off (pyEfis#464). So no taps here -- the
widget is built, painted once, and read back:

- `<name>.png`, the widget grab (square, so it lines up with the twin's
  0..100 viewBox);
- `hsi_bearing_oracle.json`: per config, the BRG tab rows (rects in widget px
  and as a fraction of the widget), their labels, the tab font size, the
  readout panel's rect, and each corner label's text and rect.

    QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis \\
      python3 tools/hsi_bearing_oracle.py OUTDIR [SIZE]
"""

import json
import os
import sys
import time

PYEFIS = os.environ["PYEFIS"]
sys.path.insert(0, PYEFIS)
sys.path.insert(0, os.path.join(PYEFIS, "src"))

from tools import render_instrument as ri  # noqa: E402  (installs the mock FIX db)

import pyavtools.fix as fix  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

# (name, options). Every config the editor preview draws a bearing feature in.
CONFIGS = (
    ("top_panel_brg1", dict(readout_layout="top_panel", bearing1_enabled=True)),
    ("top_panel_brg12", dict(readout_layout="top_panel", bearing1_enabled=True,
                             bearing2_enabled=True)),
    ("top_panel_none", dict(readout_layout="top_panel")),
    ("corners_brg12", dict(readout_layout="corners", bearing1_enabled=True,
                           bearing2_enabled=True)),
    ("split_brg1", dict(readout_layout="split", bearing1_enabled=True)),
    ("none_brg2", dict(readout_layout="none", bearing2_enabled=True)),
    ("arc_top_panel_brg12", dict(readout_layout="top_panel", orientation="arc",
                                 bearing1_enabled=True, bearing2_enabled=True)),
    ("arc_corners_brg12", dict(readout_layout="corners", orientation="arc",
                               bearing1_enabled=True, bearing2_enabled=True)),
)


def _settle(app, n=30):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def _seed():
    # The editor preview's sample values (buildHSI): heading 87, bug 50,
    # course 110, CDI on GPS.
    for key, val in (("HEAD", 87.0), ("HEADBUG", 50.0), ("COURSE", 110.0),
                     ("NAVSRC", 2.0), ("NAVTYPE", 0.0), ("CDI", 0.3),
                     ("GSI", -0.2), ("TOFROM", 1.0), ("TRACK", 78.0),
                     ("BRG1", 30.0), ("BRG2", 250.0),
                     ("BRG1SRC", 0.0), ("BRG2SRC", 1.0),
                     # CDI/GS have a signal, so no NAV/GS flag the preview lacks.
                     ("CDIV", 1.0), ("GSV", 1.0)):
        hi = 3.0 if key in ("NAVSRC", "NAVTYPE", "BRG1SRC", "BRG2SRC",
                            "TOFROM", "CDIV", "GSV") else 359.9
        lo = -1.0 if key in ("CDI", "GSI") else 0.0
        fix.db.define_item(key, key, "float", lo, hi, "deg", 50000, "")
        fix.db.set_value(key, val)
        it = fix.db.get_item(key)
        it.bad = it.fail = it.old = False


def _frac(r, w, h):
    return None if r is None else [r[0] / w, r[1] / h, r[2] / w, r[3] / h]


def render(app, size, opts):
    from pyefis.instruments.hsi import HSI

    _seed()
    host = QWidget()
    host.setStyleSheet("background: black")
    host.resize(size, size)
    h = HSI(host, cdi_enabled=True, gsi_enabled=True,
            font_family="DejaVu Sans Condensed")
    for k, v in dict(heading_bug_enabled=True, track_indicator_enabled=True,
                     source_auto_color=True, **opts).items():
        setattr(h, k, v)
    if opts.get("orientation") == "arc":
        h.orientation = "arc"
    h.setGeometry(0, 0, size, size)
    host.show()
    _settle(app, 50)

    h.viewport().repaint()
    _settle(app)
    assert not any(h._brgOn.values()), "a pointer was on at boot"
    img = host.grab().toImage()
    tab_rows = {str(n): _frac(r, size, size)
                for n, r in sorted(h._brg_tab_rects.items())}

    # Read the tab's labels and the font size it settled on: call the real
    # _draw_bearing_tab once more on a recording painter, at the panel
    # geometry _draw_readouts hands it under top_panel.
    seen, panel = [], None
    if h.readout_layout == "top_panel":
        g = h._gutter or (size * 0.16)
        ph, pw = g * 0.76, h.fontSize * 12.5
        px, py = size / 2.0 - pw / 2.0, (g - ph) / 2.0
        panel = _frac((px, py, pw, ph), size, size)

        class Rec(QPainter):
            def drawText(self, *a):
                if len(a) == 3 and isinstance(a[2], str):
                    seen.append({"label": a[2], "px": self.font().pixelSize(),
                                 "bold": self.font().bold()})
                return super().drawText(*a)

        scratch = QImage(size, size, QImage.Format.Format_ARGB32)
        rec = Rec(scratch)
        h._draw_bearing_tab(rec, px + pw, py, ph)
        rec.end()
    out = {
        "font_size": h.fontSize,
        "panel": panel,
        "tab_rows": tab_rows,
        "tab_text": seen,
        "corner": {},
    }
    for n in (1, 2):
        txt, invalid = h._bearing_corner_label(n)
        out["corner"][str(n)] = {"text": txt,
                                 "rect": _frac(h._brg_label_rect.get(n), size, size)}
    host.close()
    return img, out


def main(outdir, size):
    os.makedirs(outdir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    ri._load_fonts()
    ri._setup_fix()
    import pyefis.hmi as hmi
    if hmi.actions is None:
        hmi.initialize({})
    dump = {"size": size, "configs": {}}
    for name, opts in CONFIGS:
        img, out = render(app, size, opts)
        img.save(os.path.join(outdir, "%s.png" % name))
        dump["configs"][name] = {"options": opts, **out}
    with open(os.path.join(outdir, "hsi_bearing_oracle.json"), "w") as f:
        json.dump(dump, f, indent=1, sort_keys=True)
        f.write("\n")
    print("%d configs at %d px -> %s" % (len(CONFIGS), size, outdir))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 400)
