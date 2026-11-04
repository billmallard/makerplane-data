# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-4758 (G23): dump what the REAL pyEfis MovingMap zoom rail (G22,
# billmallard/pyEfis#205) draws for a table of option sets, as the fixture the
# editor's twin (zoomRailGeom in public/editor.html) is tested against. The
# geometry comes from zoom_rail_geometry(); the strokes and the thumb come
# from running the widget's own _paint_zoom_rail() against a recording
# painter, so no paint formula is re-typed here. The pyEfis source is imported
# from a checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools \
#       python3 tools/zoom_rail_oracle.py > test/fixtures/zoom_rail_pyefis.json
#
# With --render OUT.png W H INCHES it instead saves one no-data frame of the
# widget at 10 NM with the rail on, the device half of the screenshot
# comparison (tools/range_rings_compare.py composes it with the twin).

import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import render_instrument as ri  # noqa: E402  (pyEfis tools/: mock FIX, fonts)

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

# (options, map w, map h, screen w, screen h, diagonal inches). The screen is
# what main: carries (screenWidth/Height, screenDiagonalInches); the rail's
# 20 mm column is sized from it. None = no diagonal configured.
CASES = [
    ({"zoom_rail": True}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True}, 650, 1000, 1920, 1080, 10.1),
    ({"zoom_rail": True}, 1280, 800, 1280, 800, 10.1),
    ({"zoom_rail": True, "zoom_rail_side": "right"}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_nm": 13.7}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_nm": 2}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_nm": 160}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_nm": 1}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_nm": 500}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_ladder": "2,10,50,200,500,2000"}, 650, 1000, 1920, 1080, 21.5),
    ({"zoom_rail": True, "range_ladder": "40,5,20,5"}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_ladder": "10"}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_ladder": "abc,5"}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_ladder": ""}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True, "range_ladder": "5, 20 ,80"}, 1024, 600, 1024, 600, 7.0),
    ({"zoom_rail": True}, 300, 240, 1024, 600, 7.0),
    ({"zoom_rail": True}, 200, 30, 1024, 600, 7.0),
    ({"zoom_rail": True}, 2000, 1200, 2000, 1200, 4.0),
    ({"zoom_rail": True}, 1024, 600, 1024, 600, 200.0),
    ({"zoom_rail": True}, 1024, 600, 1024, 600, None),
    ({"zoom_rail": False}, 1024, 600, 1024, 600, 7.0),
    ({}, 1024, 600, 1024, 600, 7.0),
]


class _Main(QWidget):
    """Stands in for the main window: _px_per_mm walks up to the first parent
    carrying screenDiagonalInches."""

    def __init__(self, sw, sh, inches):
        super().__init__()
        self.screenWidth, self.screenHeight = sw, sh
        self.screenDiagonalInches = inches


class _Recorder:
    """Duck-typed QPainter: records what _paint_zoom_rail draws."""

    def __init__(self):
        self.pen = None
        self.brush = None
        self.ops = []

    def save(self):
        pass

    def restore(self):
        pass

    def setBrush(self, b):
        self.brush = b

    def setPen(self, p):
        self.pen = p

    def _pen(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPen
        if not isinstance(self.pen, QPen) or self.pen.style() == Qt.PenStyle.NoPen:
            return None
        c = self.pen.color()
        return {"rgba": [c.red(), c.green(), c.blue(), c.alpha()],
                "width": self.pen.widthF(),
                "round_cap": self.pen.capStyle() == Qt.PenCapStyle.RoundCap}

    def _brush(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QBrush
        if not isinstance(self.brush, QBrush) or self.brush.style() == Qt.BrushStyle.NoBrush:
            return None
        c = self.brush.color()
        return [c.red(), c.green(), c.blue(), c.alpha()]

    def drawPath(self, path):
        segs = []
        for i in range(path.elementCount()):
            e = path.elementAt(i)
            if e.isMoveTo():
                segs.append([[e.x, e.y]])
            else:
                segs[-1].append([e.x, e.y])
        self.ops.append({"op": "path", "pen": self._pen(), "brush": self._brush(),
                         "segments": segs})

    def drawEllipse(self, c, rx, ry):
        self.ops.append({"op": "ellipse", "pen": self._pen(), "brush": self._brush(),
                         "cx": c.x(), "cy": c.y(), "rx": rx, "ry": ry})


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


def _map(opts, w, h, sw, sh, inches):
    from pyefis.instruments.map import MovingMap
    main = _Main(sw, sh, inches)
    m = MovingMap(main)
    m.range_nm = 10.0
    for k, v in opts.items():
        setattr(m, k, v)
    m.resize(w, h)
    return main, m


def run(opts, w, h, sw, sh, inches):
    from pyefis import display_metrics
    main, m = _map(opts, w, h, sw, sh, inches)
    # The device's second DPI source is Qt's, which the editor cannot know.
    # Pin it off, so a missing or implausible diagonal lands on the nominal
    # 96 the twin falls back to.
    real = display_metrics.dpi_from_qscreen
    display_metrics.dpi_from_qscreen = lambda q: None
    try:
        g = m.zoom_rail_geometry()
        rec = _Recorder()
        m._paint_zoom_rail(rec)
        out = {"options": opts, "w": w, "h": h,
               "screen": {"width": sw, "height": sh, "inches": inches},
               "px_per_mm": m._px_per_mm(), "rail": None, "ops": rec.ops}
        if g is not None:
            out["rail"] = {
                "claim": _rect(g["claim"]), "plus": _rect(g["plus"]),
                "minus": _rect(g["minus"]), "x": g["x"], "y0": g["y0"],
                "y1": g["y1"], "ladder": g["ladder"], "touch": g["touch"],
                "rung_y": [m._rail_rung_y(g, i) for i in range(len(g["ladder"]))],
                "thumb_y": m._rail_thumb_y(g)}
    finally:
        display_metrics.dpi_from_qscreen = real
    return out


def render(path, w, h, inches):
    """One frame of the real widget, no data, 10 NM, rail on, at rest."""
    main, m = _map({"zoom_rail": True}, w, h, w, h, float(inches))
    m.font_family = "DejaVu Sans Condensed"
    main.resize(w, h)
    m.show()
    app = QApplication.instance()
    for _ in range(3):
        m.repaint()
        app.processEvents()
    m.grab().save(path)


def _rev():
    src = sys.modules["pyefis"].__file__
    try:
        return subprocess.run(["git", "-C", src.rsplit("/src/", 1)[0], "rev-parse",
                               "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841 (kept alive)
    ri._load_fonts()
    ri._setup_fix()
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], int(argv[3]), int(argv[4]), argv[5])
    else:
        json.dump({"generator": "configurator/tools/zoom_rail_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "cases": [run(*c) for c in CASES]},
                  sys.stdout, indent=1)
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
