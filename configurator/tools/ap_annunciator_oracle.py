# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6286 (AFC4 twin): dump what the REAL pyEfis ap_annunciator
# (instruments/autopilot/fma.py ModeAnnunciator, pyEfis #499) paints, as the
# fixture the editor's twin (apStripGeom / apStripSvg in public/editor.html)
# is tested against.
#
# Each case builds the widget through pyEfis's builder (apply_preferences
# resolves the font, build_ap_annunciator constructs it) and applies the
# options through apply_options, the device's own path. The look is reached
# through the AFC1 FIX keys the strip reads (state_for below), so the widget's
# own logic picks every cell: the schema preview block becomes APENG / YDENG /
# APLAT / ... / VSSEL, a mode change is a key change after the first reading
# (the flash), a stale key is a key marked old. The widget's clock is pinned
# so the flash phase is known. Paint calls are recorded by swapping the
# module's QPainter for a subclass that records each call and then paints it;
# no paint formula is re-typed here. The pyEfis source is imported from a
# checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis dev with #499, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
#       python3 tools/ap_annunciator_oracle.py > test/fixtures/ap_annunciator_pyefis.json
#
# The fixture also carries Qt's bold glyph advances per pixel size and the
# kern pairs (the `metrics` block): the strip fits its font by
# horizontalAdvance, and that is the sum of the hinted glyph advances plus the
# kern pairs, each rounded (for DejaVu Sans Condensed, exact for 99.7% of
# 2,457 string/size pairs probed at 4-120 px; the rest are 1-2 px out). The twin's
# QT_BOLD_METRICS table now comes from tools/qt_bold_metrics.py over the
# annunciator oracle's wider fixture (AER-6348); the AP test still checks it
# against this one.
#
# With --render OUT.png W H STATE it instead saves one frame of the strip,
# the device half of the side-by-side; OPTIONS is optional JSON merged into
# its options. STATE is a key of STATES.

import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.mock_db.client  # noqa: E402  (pyEfis tests/: in-memory FIX)
import tests.mock_db.scheduler  # noqa: E402

sys.modules["pyavtools.fix.client"] = tests.mock_db.client
sys.modules["pyavtools.scheduler"] = tests.mock_db.scheduler

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QImage,  # noqa: E402
                         QPainter, QRegion)
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

import pyavtools.fix as fix  # noqa: E402

# The Pi 5 sheet on pyEfis #499 draws the strip in B612 (joint.py), so the
# table carries it too; the Pi's own files are Airbus's OTFs, version 1.008.
FAMILIES = ("DejaVu Sans Condensed", "DejaVu Sans", "B612")
#: Qt's advances at this pixel size are the font's own units (2048 per em).
UNITS_PX = 2048
CHARS = " +-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
METRIC_PX = range(1, 161)

# key, dtype, min, max, tol: fix-gateway database/autopilot.yaml, as pyEfis's
# tests/instruments/autopilot/conftest.py defines them.
AFC1_KEYS = [
    ("VSSEL", "int", -30000, 30000, 0),
    ("IASSEL", "float", 0.0, 999.9, 0),
    ("APCMD", "str", None, None, 0),
    ("APCMDSEQ", "int", 0, 2147483647, 0),
    ("APENG", "bool", None, None, 2000),
    ("FDENG", "bool", None, None, 2000),
    ("YDENG", "bool", None, None, 2000),
    ("APLAT", "str", None, None, 2000),
    ("APLATARM", "str", None, None, 2000),
    ("APVERT", "str", None, None, 2000),
    ("APVERTARM", "str", None, None, 2000),
    ("APALTCAP", "bool", None, None, 2000),
    ("APALTREF", "int", -1000, 60000, 2000),
    ("APDISC", "bool", None, None, 2000),
]
INITIAL = {"int": 0, "float": 0.0, "str": "", "bool": False}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _schema_preview():
    from pyefis.screens import screenbuilder_factory as sf
    return dict(sf.REGISTRY["ap_annunciator"].preview)


def preview_values(pv):
    """The FIX values that make the real strip show the preview block."""
    vert = str(pv.get("vert", ""))
    mode, _, target = vert.partition(" ")
    out = dict(APENG=pv.get("ap") == "AP", YDENG=pv.get("yd") == "YD",
               APLAT=pv.get("lat", ""),
               APLATARM=",".join(str(pv.get("lat_arm", "")).split()),
               APVERT=mode,
               APVERTARM=",".join(str(pv.get("vert_arm", "")).split()))
    if mode == "VS" and target:
        out["VSSEL"] = int(target)
    return out


# Each state: (values at build, values after the first reading or None,
# keys marked old after, clock at paint). A change after the build is a mode
# change, so it flashes; the paint clock picks the blink phase (on while
# int(t / 0.5) is even).
STATES = {
    "preview": (None, None, (), 1000.0),
    "lat_flash_on": (dict(APENG=True, YDENG=True, APLAT="HDG", APVERT="VS",
                          VSSEL=500),
                     dict(APLAT="GPS"), (), 1000.0),
    "lat_flash_off": (dict(APENG=True, YDENG=True, APLAT="HDG", APVERT="VS",
                           VSSEL=500),
                      dict(APLAT="GPS"), (), 1000.5),
    "alt_cap": (dict(APENG=True, YDENG=True, APLAT="GPS", APVERT="VS",
                     VSSEL=500, APVERTARM="ALT"),
                dict(APALTCAP=True, APVERTARM=""), (), 1000.0),
    "disconnect_on": (dict(APENG=True, YDENG=True, APLAT="GPS", APVERT="IAS",
                           IASSEL=90.0),
                      dict(APENG=False, APDISC=True), (), 1000.0),
    "disconnect_off": (dict(APENG=True, YDENG=True, APLAT="GPS", APVERT="IAS",
                            IASSEL=90.0),
                       dict(APENG=False, APDISC=True), (), 1000.5),
    "nodata_amber": (dict(APENG=True, APLAT="HDG"), None, ("APLAT",), 1000.0),
    "nodata_grey": (dict(APENG=False, APLAT="HDG"), None, ("APVERT",), 1000.0),
    "long_text": (dict(APENG=True, YDENG=True, APLAT="LOC",
                       APLATARM="LOC,GPS", APVERT="ALT", APALTREF=18000,
                       APVERTARM="ALT,GS"), None, (), 1000.0),
    "vs_negative": (dict(APENG=True, APLAT="TRK", APVERT="VS", VSSEL=-1200,
                         APVERTARM="ALT"), None, (), 1000.0),
    "ap_only": (dict(APENG=True), None, (), 1000.0),
    "hold_ref": (dict(APENG=True, YDENG=True, APLAT="GPS", APVERT="ALT",
                      APALTREF=8500), None, (), 1000.0),
    # The Pi 5 sheet's scenes (pyEfis docs/aer-5808-evidence/joint/joint.py
    # SCENES, before and after), for the side-by-side with the glass.
    "pi_mtn_climb": (dict(APENG=True, YDENG=True, APLAT="HDG", APLATARM="GPS",
                          APVERT="IAS", APVERTARM="ALT", IASSEL=90.0),
                     None, (), 1000.0),
    "pi_ksba_final": (dict(APENG=True, YDENG=True, APLAT="HDG",
                           APLATARM="GPS", APVERT="VS", VSSEL=-500),
                      dict(APLAT="GPS", APLATARM=""), (), 1000.0),
    "pi_ksba_disc": (dict(APENG=True, YDENG=True, APLAT="GPS", APVERT="PIT"),
                     dict(APENG=False, APDISC=True), (), 1000.0),
}


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class _RecPainter(QPainter):
    """QPainter that records what the strip paints, then paints it."""
    ops = None

    def _pen(self):
        p = self.pen()
        return {"rgba": _rgba(p.color()), "width": p.widthF(),
                "style": p.style().name}

    def _rec(self, op, **kw):
        _RecPainter.ops.append(dict(op=op, **kw))

    def fillRect(self, rect, color, *a):
        self._rec("fill_rect", rect=_rect(rect), rgba=_rgba(QColor(color)))
        return super().fillRect(rect, color, *a)

    def drawLine(self, *a):
        self._rec("line", args=[float(v) for v in a], pen=self._pen())
        return super().drawLine(*a)

    def drawRect(self, rect, *a):
        self._rec("rect", rect=_rect(rect), pen=self._pen(),
                  brush=self.brush().style().name)
        return super().drawRect(rect, *a)

    def drawText(self, rect, flags, text, *a):
        f = self.font()
        fm = QFontMetricsF(f)
        br = self.boundingRect(rect, int(flags), text)
        self._rec("text", text=text, rect=_rect(rect), flags=int(flags),
                  pen=self._pen(), family=f.family(),
                  pixel_size=f.pixelSize(), bold=f.bold(),
                  laid_out=_rect(br), ascent=fm.ascent(),
                  descent=fm.descent(), advance=fm.horizontalAdvance(text))
        return super().drawText(rect, flags, text, *a)


def _define():
    fix.initialize({"main": {"FixServer": "localhost", "FixPort": "3490"}})
    for key, dtype, lo, hi, tol in AFC1_KEYS:
        fix.db.define_item(key, key, dtype, lo, hi, "", tol, "")


def _reset():
    for key, dtype, _lo, _hi, _tol in AFC1_KEYS:
        item = fix.db.get_item(key)
        item.value = INITIAL[dtype]
        item.old = item.bad = item.fail = False


def _set(values):
    for k, v in values.items():
        fix.db.get_item(k).value = v


def _build(opts, state, w, h):
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_options as so
    from pyefis.screens import screenbuilder_preferences as sp
    first, after, old, t = STATES[state]
    _reset()
    _set(preview_values(_schema_preview()) if first is None else first)
    screen = QWidget()
    screen.encoder_list = []
    cfg = {"type": "ap_annunciator", "options": dict(opts)}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    strip = sf.build_ap_annunciator(screen, cfg, font_percent=font_percent,
                                    font_family=font_family)
    screen.instruments = {0: strip}
    so.apply_options(screen, 0, cfg)
    clock = Clock(1000.0)
    strip.model.clock = clock
    strip.model.observe()          # the first reading at the pinned clock
    if after:
        _set(after)
    for k in old:
        fix.db.get_item(k).old = True
    clock.t = t
    strip.resize(int(w), int(h))
    return screen, strip


def _cells(strip):
    return {slot: {"text": c.text, "role": c.role, "boxed": c.boxed,
                   "blink": c.blink}
            for slot, c in strip.model.cells().items()}


def run(opts, w, h, state="preview"):
    from pyefis.instruments.autopilot import fma
    screen, strip = _build(opts, state, w, h)
    fma.QPainter = _RecPainter
    _RecPainter.ops = []
    try:
        strip.grab()
    finally:
        fma.QPainter = QPainter
    out = {"options": opts, "w": int(w), "h": int(h), "state": state,
           "family": strip.font_family, "font_percent": strip.font_percent,
           "on": int(strip.model.clock() / fma.BLINK_HALF_S) % 2 == 0,
           "cells": _cells(strip),
           "slot_rects": {k: _rect(r) for k, r in strip.slot_rects().items()},
           "dividers": strip.group_dividers(),
           "ops": _RecPainter.ops}
    strip._timer.stop()
    return out


def metrics():
    """Qt's bold advances per glyph and pixel size, ascent/descent, and the
    kern pairs over CHARS, per family. Units and kern pairs are read from Qt
    at UNITS_PX, where its advances are the font's units, so the same code
    covers a TrueType kern table and an OTF's GPOS pairs."""
    out = {}
    for fam in FAMILIES:
        f = QFont(fam)
        f.setPixelSize(UNITS_PX)
        f.setBold(True)
        fm = QFontMetricsF(f)
        units = [fm.horizontalAdvance(c) for c in CHARS]
        vert = [fm.ascent(), fm.descent()]
        adv = dict(zip(CHARS, units))
        pairs = {}
        for a in CHARS:
            for b in CHARS:
                k = fm.horizontalAdvance(a + b) - adv[a] - adv[b]
                if k:
                    pairs[a + b] = k
        rows = []
        for px in METRIC_PX:
            f = QFont(fam)
            f.setPixelSize(px)
            f.setBold(True)
            fm = QFontMetricsF(f)
            rows.append([px, fm.ascent(), fm.descent()]
                        + [fm.horizontalAdvance(c) for c in CHARS])
        out[fam] = {"units": units, "ascent_units": vert[0],
                    "descent_units": vert[1], "kern": pairs, "rows": rows}
    return {"chars": CHARS, "columns": ["px", "ascent", "descent"]
            + list(CHARS), "families": out}


def cases():
    out = []
    sizes = [(940, 50), (800, 40), (1024, 60), (600, 36), (480, 32),
             (1280, 72), (400, 24), (517, 43), (300, 20), (1500, 90)]
    for w, h in sizes:
        out.append(({}, w, h, "preview"))
        out.append(({"ap_position": "center"}, w, h, "preview"))
    for extra in (
            {"font_percent": 0.4},
            {"font_percent": 0.8},
            {"font_percent": 1.0},
            {"font_family": "DejaVu Sans"},
            {"font_family": "DejaVu Sans", "ap_position": "center"},
            {"active_color": "#ff00ff", "armed_color": "#ffff00",
             "warning_color": "#00ffff", "background_color": "#102030"},
            {"background_opacity": 0},
            {"background_opacity": 0.3},
            {"background_opacity": 1},
            {"background_opacity": 1.7},
            {"background_opacity": -0.5},
            {"background_opacity": "0.5"},
            {"background_color": "#80102030"},
            {"ap_position": "CENTER"},
            {"ap_position": "right"},
            {"mode_flash_s": 3, "touch_ack": False},
            ):
        out.append((extra, 940, 50, "preview"))
    for state in STATES:
        if state == "preview" or state.startswith("pi_"):
            continue
        for w, h in ((940, 50), (600, 36)):
            for pos in ("left", "center"):
                out.append(({"ap_position": pos}, w, h, state))
    for state in ("preview", "pi_mtn_climb", "pi_ksba_final", "pi_ksba_disc"):
        for pos in ("left", "center"):
            out.append(({"font_family": "B612", "ap_position": pos}, 1046, 55,
                        state))
    out.append(({"warning_color": "#ff8000"}, 940, 50, "disconnect_on"))
    out.append(({"font_family": "DejaVu Sans"}, 940, 50, "long_text"))
    return out


def render(path, w, h, state):
    opts = json.loads(os.environ.get("OPTIONS") or "{}")
    screen, strip = _build(opts, state, w, h)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    strip.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    strip._timer.stop()
    img.save(path)


def _rev():
    import pyefis
    src = pyefis.__file__
    try:
        return subprocess.run(["git", "-C", src.rsplit("/src/", 1)[0], "rev-parse",
                               "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def _fonts():
    from PyQt6.QtGui import QFontInfo
    for fam in FAMILIES:
        got = QFontInfo(QFont(fam)).family()
        if got != fam:
            raise SystemExit("font %r resolved to %r; point FONTCONFIG_FILE "
                             "at a dir holding the DejaVu TTFs and B612's "
                             "OTFs" % (fam, got))


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841 (kept alive)
    _fonts()
    _define()
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], int(argv[3]), int(argv[4]), argv[5])
    else:
        json.dump({"generator": "configurator/tools/ap_annunciator_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "schema_preview": _schema_preview(),
                   "metrics": metrics(),
                   "cases": [run(*c) for c in cases()]},
                  sys.stdout, indent=None, separators=(",", ":"))
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
