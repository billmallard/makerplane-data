# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5547 (FUEL1 F6, docs/fuel_provider_model.md section 6): measure what the
# REAL pyEfis bar and arc gauges do with a tank label, and what they draw
# when a tank reference is unbound, as the fixture the editor twin and the
# tank-label limit are tested against (test/fixtures/fuel_gauge_pyefis.json).
# Nothing here re-types a pyEfis formula: the widgets are built and painted,
# and the numbers are read back off them.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools:$PYEFIS \
#       python3 tools/fuel_gauge_oracle.py > test/fixtures/fuel_gauge_pyefis.json
#
# DejaVu Sans Condensed must resolve by family name (fontconfig, or the paths
# render_instrument._load_fonts registers); the fixture records the family Qt
# actually used, and the test refuses a fixture measured in a substitute.
#
# Three records:
#   stock      every FUELQn bar on the stock EMS2 screen, built by the real
#              screen builder at the stock resolutions, with its name font and
#              name rectangle, and the per-glyph advances at that font size.
#              The tank-label limit is "fits every stock bar" (fuel_label.js).
#   labels     labels of increasing length drawn by the real widget at each
#              stock size, and at sample custom sizes: the advance Qt measures
#              and the bounding box of the ink the name leaves on the widget.
#   fail       what each gauge type holds after setUnbound() (pyEfis#452):
#              value text, colours, value, bands. The twin's orphan look.
# With --render OUT.png it instead saves the stock labels strip used as the
# PR evidence (the device half; tools/fuel_gauge_compare.mjs is the twin).

import contextlib
import json
import os
import re
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import render_instrument as ri  # noqa: E402  (pyEfis tools/: mock FIX, fonts)

from PyQt6.QtGui import QColor, QFontInfo, QFontMetricsF, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# The stock resolutions pyEfis main/default.yaml lists, plus the 1024x600
# and 800x480 7-inch panels. BUTTONS on and off: EMS2 lays its bars out
# differently with the button column.
RESOLUTIONS = [(1280, 720), (1024, 768), (1920, 1080), (1024, 600), (800, 480)]
SCREEN = "EMS2"

LABELS = ["L TIP", "Tank 1", "L MAIN", "R MAIN", "HEADER", "R TIP AUX",
          "L MAIN AUX", "WWWW", "MMMMM", "LONGLABEL"]
# Sizes a builder might draw a gauge at in the editor (type, w, h, options).
CUSTOM = [
    ("vertical_bar_gauge", 80, 300, {}),
    ("vertical_bar_gauge", 120, 400, {}),
    ("horizontal_bar_gauge", 200, 60, {}),
    ("horizontal_bar_gauge", 300, 90, {}),
    ("arc_gauge", 200, 100, {}),
]
PRINTABLE = "".join(chr(c) for c in range(32, 127))


def _rgba(c):
    c = QColor(c)
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _widget(itype):
    from pyefis.instruments.gauges.arc import ArcGauge
    from pyefis.instruments.gauges.horizontalBar import HorizontalBar
    from pyefis.instruments.gauges.verticalBar import VerticalBar
    return {"vertical_bar_gauge": VerticalBar, "horizontal_bar_gauge": HorizontalBar,
            "arc_gauge": ArcGauge}[itype](min_size=False)


def _settle():
    app = QApplication.instance()
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)


def _name_font(g):
    """The QFont the widget draws its name with, and its pixel size."""
    if hasattr(g, "smallFont"):
        return g.smallFont
    from PyQt6.QtGui import QFont
    f = QFont(g.font_family)
    f.setPixelSize(int(g.nameFontSize))
    return f


def _advances(font):
    fm = QFontMetricsF(font)
    return {ch: fm.horizontalAdvance(ch) for ch in PRINTABLE}


def _ink(g, label, rows):
    """Bounding box [x0, y0, x1, y1] (inclusive) of the pixels the name adds,
    within the top `rows` rows, or None when the name leaves no ink."""
    g.name = ""
    g.repaint()
    base = g.grab().toImage()
    g.name = label
    g.repaint()
    img = g.grab().toImage()
    xs, ys = [], []
    for y in range(min(rows, img.height())):
        for x in range(img.width()):
            if img.pixel(x, y) != base.pixel(x, y):
                xs.append(x)
                ys.append(y)
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _rows(g):
    """Rows that can hold name ink: the name rectangle and the same again
    below it (a wrapped second line would land there)."""
    if hasattr(g, "nameTextRect"):
        r = g.nameTextRect
        return int(r.y() + 2 * r.height()) + 2
    return g.height()


def _label_cases(itype, w, h, options, setup=None):
    g = _widget(itype)
    for k, v in options.items():
        setattr(g, k, v)
    if setup:
        setup(g)
    g.setColors()
    g.resize(w, h)
    g.show()
    g.resizeEvent(None)
    _settle()
    font = _name_font(g)
    fm = QFontMetricsF(font)
    out = []
    for label in LABELS:
        out.append({"label": label, "advance": fm.horizontalAdvance(label),
                    "ink": _ink(g, label, _rows(g))})
    rect = None
    if hasattr(g, "nameTextRect"):
        r = g.nameTextRect
        rect = [r.x(), r.y(), r.width(), r.height()]
    g.close()
    return {"type": itype, "w": w, "h": h, "options": options,
            "name_px": font.pixelSize(), "name_rect": rect,
            "family": QFontInfo(font).family(), "cases": out}, font


def _stock():
    """Every visible FUELQn bar on the stock EMS2 screen at each resolution."""
    from pyefis.instruments.gauges.abstract import AbstractGauge
    from tests.screenshots import generate_screenshots as gs
    from tests.screenshots import gui
    seen = {}
    for W, H in RESOLUTIONS:
        for buttons in (False, True):
            config, prefs = gs._assemble_real_config()
            prefs.setdefault("enabled", {})["BUTTONS"] = buttons
            scr = dict(config["screens"][SCREEN])
            scr["instruments"] = [i for i in scr.get("instruments", [])
                                  if not gs._is_button_chrome(i)]
            gui.initialize({"main": dict(config["main"], screenWidth=W, screenHeight=H),
                            "screens": {"TEST": scr}}, gs.REAL_CONFIG, prefs,
                           fullscreen=False)
            s = gui.mainWindow.scr.object
            s.resize(W, H)
            _settle()
            for g in s.findChildren(AbstractGauge):
                key = str(getattr(g, "_dbkey", "") or "")
                if not (re.fullmatch(r"FUELQ\d+", key) and g.isVisible()):
                    continue
                itype = {"VerticalBar": "vertical_bar_gauge",
                         "HorizontalBar": "horizontal_bar_gauge"}[type(g).__name__]
                r = g.nameTextRect
                geo = (itype, g.width(), g.height(), g.smallFont.pixelSize())
                if geo not in seen:
                    seen[geo] = {
                        "type": itype, "w": g.width(), "h": g.height(),
                        "small_font_percent": g.small_font_percent,
                        "show_units": g.show_units,
                        "name_px": g.smallFont.pixelSize(),
                        "name_rect": [r.x(), r.y(), r.width(), r.height()],
                        "where": []}
                seen[geo]["where"].append(f"{SCREEN} {W}x{H} buttons={buttons} {key}")
            gui.mainWindow.close()
    return sorted(seen.values(), key=lambda s: (s["name_rect"][2] / s["name_px"], s["w"]))


def _fail(itype, w, h):
    g = _widget(itype)
    g.name = "L TIP"
    g.setUnbound()
    g.resize(w, h)
    g.resizeEvent(None)
    return {
        "type": itype, "fail": g.fail, "unbound": g.unbound,
        "value_text": g.valueText, "value": g._value,
        "bands": {k: getattr(g, k) for k in ("lowWarn", "lowAlarm", "highWarn", "highAlarm")},
        "colors": {k: _rgba(getattr(g, k)) for k in
                   ("safeColor", "warnColor", "alarmColor", "textColor", "penColor",
                    "valueColor", "bgColor")},
    }


def _stock_setup(s):
    def setup(g):
        g.small_font_percent = s["small_font_percent"]
        g.show_units = s["show_units"]
    return setup


def render(path):
    """The stock-size strip: each label on the smallest stock bar, unbound
    look, over the device's black screen, 3x nearest-neighbour."""
    s = _stock()[0]
    g = _widget(s["type"])
    _stock_setup(s)(g)
    g.setUnbound()
    g.resize(s["w"], s["h"])
    g.show()
    g.resizeEvent(None)
    top = int(s["name_rect"][3] * 2.5)
    gap = 6
    strip = QImage((s["w"] + gap) * len(LABELS), top, QImage.Format.Format_RGB32)
    strip.fill(QColor(0, 0, 0))
    p = QPainter(strip)
    for i, label in enumerate(LABELS):
        g.name = label
        g.repaint()
        p.drawImage(i * (s["w"] + gap), 0, g.grab().toImage().copy(0, 0, s["w"], top))
    p.end()
    strip.scaled(strip.width() * 3, strip.height() * 3).save(path)


def _rev():
    src = sys.modules["pyefis"].__file__
    try:
        return subprocess.run(["git", "-C", src.rsplit("/src/", 1)[0], "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841 (kept alive)
    ri._load_fonts()
    ri._setup_fix()
    import pyefis.hmi as hmi
    if hmi.actions is None:
        hmi.initialize({})
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2])
        return
    # The screen builder prints its config walk to stdout; keep stdout for JSON.
    with contextlib.redirect_stdout(sys.stderr):
        doc = measure()
    json.dump(doc, sys.stdout, indent=1)
    sys.stdout.write("\n")
    sys.stdout.flush()


def measure():
    stock = _stock()
    advances, labels = {}, []
    for s in stock:
        rec, font = _label_cases(s["type"], s["w"], s["h"], {}, _stock_setup(s))
        rec["stock"] = True
        labels.append(rec)
        s["family"] = rec["family"]
        advances[str(font.pixelSize())] = _advances(font)
    for itype, w, h, opts in CUSTOM:
        rec, font = _label_cases(itype, w, h, opts)
        rec["stock"] = False
        labels.append(rec)
        advances[str(font.pixelSize())] = _advances(font)
    return {"generator": "configurator/tools/fuel_gauge_oracle.py",
            "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
            "font_family": "DejaVu Sans Condensed",
            "stock": stock, "advances": advances, "labels": labels,
            "fail": [_fail(t, w, h) for t, w, h in
                     (("vertical_bar_gauge", 65, 327),
                      ("horizontal_bar_gauge", 200, 60),
                      ("arc_gauge", 200, 100))]}


if __name__ == "__main__":
    main(sys.argv)
