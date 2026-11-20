# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5654: dump what the REAL pyEfis DATA flag (data_status.DataAnnunciation,
# the `data_annunciation` instrument) paints in its amber attention state, as
# the fixture the editor's twin (dataFlagGeom / dataFlagSvg in
# public/editor.html) is tested against.
#
# Each case builds the widget through pyEfis's own builder path
# (apply_preferences resolves the font, build_data_annunciation constructs it)
# and points it at a status.json with one expired pack, so the widget reaches
# amber by its own logic; nothing is forced. pyEfis #468 lights the same
# colour for a pending panel change and does not touch the paint. The paint
# calls are recorded by swapping the module's QPainter for a subclass that
# records each call and then paints it for real, so no paint formula is
# re-typed here. The pyEfis source is imported from a checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools \
#       python3 tools/data_flag_oracle.py > test/fixtures/data_flag_pyefis.json
#
# With --render OUT.png W H [FONT_PERCENT] it instead saves one frame of the
# amber widget at W x H over black, the device half of the side-by-side.

import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import render_instrument as ri  # noqa: E402  (pyEfis tools/: mock FIX, fonts)

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

# (options, widget w, widget h). The first three are the stock boot-screen
# flag (row 2, column 2, 5 x 18 of a 110 x 200 grid) on the three common
# screens; the rest walk the font law, its 8 px floor, Font size, the font
# family override and a flag too narrow for its text.
CASES = [
    ({"target_screen": "DataStatus"}, 173, 49),       # 1920 x 1080 (pyEfis #468's render)
    ({"target_screen": "DataStatus"}, 92, 27),        # 1024 x 600
    ({"target_screen": "DataStatus"}, 115, 36),       # 1280 x 800
    ({}, 220, 150),                                    # the palette thumbnail's size
    ({}, 60, 10),                                      # floor: 7 -> 8 px
    ({}, 60, 11),
    ({}, 60, 12),
    ({}, 80, 20),
    ({}, 100, 30),
    ({}, 140, 41),
    ({}, 300, 80),
    ({"font_percent": 150}, 173, 49),
    ({"font_percent": 50}, 173, 49),
    ({"font_percent": 0.8}, 173, 49),
    ({"font_percent": 0}, 173, 49),
    ({"font_percent": -20}, 173, 49),
    ({"font_percent": 10}, 173, 49),                   # 0.1 -> floor
    ({"font_family": "DejaVu Sans"}, 173, 49),
    ({}, 30, 40),                                      # narrower than "DATA": clipped
    # Exact .5 ties: qRound goes up where Python's round() would go to even.
    ({"font_percent": 150}, 60, 10),                   # 10.5 -> 11
    ({"font_percent": 50}, 80, 30),                    # 10.5 -> 11
    ({"font_percent": 125}, 120, 44),                  # 38.5 -> 39
] + [({}, 200, h) for h in range(13, 170, 6)]          # the font metric law, 9..118 px

EXPIRED = {"ok": True, "packs": [{"id": "navdata-conus", "severity": "amber"}]}
CURRENT = {"ok": True, "packs": [{"id": "navdata-conus", "severity": "none"}]}


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class _RecPainter(QPainter):
    """QPainter that records what DataAnnunciation.paintEvent draws, then
    draws it."""
    ops = None

    def _pen(self):
        p = self.pen()
        return {"rgba": _rgba(p.color()), "width": p.widthF(),
                "cosmetic": p.isCosmetic(),
                "style": p.style().name}

    def drawRoundedRect(self, rect, rx, ry, *a):
        _RecPainter.ops.append({
            "op": "rounded_rect", "rect": _rect(rect), "rx": rx, "ry": ry,
            "pen": self._pen(), "brush": self.brush().style().name,
            "antialias": bool(self.renderHints() & QPainter.RenderHint.Antialiasing)})
        return super().drawRoundedRect(rect, rx, ry, *a)

    def drawText(self, rect, flags, text, *a):
        f = self.font()
        fm = QFontMetricsF(f)
        br = self.boundingRect(rect, flags, text)
        _RecPainter.ops.append({
            "op": "text", "text": text, "rect": _rect(rect),
            "align_center": flags == int(Qt.AlignmentFlag.AlignCenter),
            "pen": self._pen(), "family": f.family(),
            "pixel_size": f.pixelSize(), "bold": f.bold(),
            "laid_out": _rect(br), "ascent": fm.ascent(),
            "descent": fm.descent(), "advance": fm.horizontalAdvance(text)})
        return super().drawText(rect, flags, text, *a)


def _status_file(doc):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh)
    return path


def _build(opts, status):
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_preferences as sp
    screen = QWidget()
    cfg = {"type": "data_annunciation",
           "options": dict(opts, status_path=_status_file(status))}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    w = sf.build_data_annunciation(screen, cfg, font_percent=font_percent,
                                   font_family=font_family)
    return screen, w


def run(opts, w, h, status=EXPIRED):
    from pyefis.instruments import data_status
    screen, flag = _build(opts, status)
    flag.resize(int(w), int(h))
    data_status.QPainter = _RecPainter
    _RecPainter.ops = []
    try:
        flag.grab()
    finally:
        data_status.QPainter = QPainter
    return {"options": opts, "w": int(w), "h": int(h),
            "severity": data_status.worst_severity(flag.status),
            "ops": _RecPainter.ops}


def render(path, w, h, font_percent=None):
    opts = {} if font_percent is None else {"font_percent": float(font_percent)}
    screen, flag = _build(opts, EXPIRED)
    flag.resize(w, h)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    # DrawChildren only: the default also paints the window background, a
    # grey the flag itself never draws.
    flag.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    img.save(path)


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
        render(argv[2], int(argv[3]), int(argv[4]), *argv[5:6])
    else:
        cases = [run(*c) for c in CASES]
        # The usual glass: every pack current, the flag paints nothing.
        cases.append(run({}, 173, 49, CURRENT))
        json.dump({"generator": "configurator/tools/data_flag_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "cases": cases},
                  sys.stdout, indent=1)
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
