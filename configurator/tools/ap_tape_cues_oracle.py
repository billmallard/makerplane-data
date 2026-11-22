# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6286 (AFC4 twin): dump what the REAL pyEfis tapes paint for the
# autopilot target cues (pyEfis #499): the IASSEL box and bug on
# Airspeed_Tape, the ALTSEL box's capture look and the VSSEL bug on
# Altimeter_Tape, and the VSSEL bug on VSI_PFD. The editor's tape twins
# (apTapeCues / apVsiCue in public/editor.html) are tested against it.
#
# The editor's tapes are approximations with their own scale, so what is
# pinned here is each cue's own shape: the box rect, pen, radius, colours and
# text size, and the bug's outline relative to its apex. Each case builds the
# widget through the registry builder and apply_options, the device's own
# path, and reaches each look through the AFC1 FIX keys (APVERT, APVERTARM,
# APALTCAP, IASSEL, VSSEL). Paint calls are recorded by swapping each module's
# QPainter for a subclass that records and then paints.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis dev with #499, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
#       python3 tools/ap_tape_cues_oracle.py > test/fixtures/ap_tape_cues_pyefis.json

import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import conftest  # noqa: E402  (pyEfis root conftest: in-memory FIX + keys)

from PyQt6.QtGui import QColor, QFontMetricsF, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

import pyavtools.fix as fix  # noqa: E402

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
TAPE_KEYS = [("IAS", "float", 0.0, 1000.0, 2000), ("ALT", "float", -2000, 60000, 2000),
             ("ALTSEL", "int", -1000, 60000, 0), ("VS", "float", -10000, 10000, 2000),
             ("BARO", "float", 0.0, 35.0, 2000)]


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


def _path(pp):
    return [[pp.elementAt(i).x, pp.elementAt(i).y] for i in range(pp.elementCount())]


class _Rec(QPainter):
    ops = None

    def __init__(self, dev=None):
        super().__init__(dev)
        self.who = type(dev).__name__ if dev is not None else ""

    def _pen(self):
        p = self.pen()
        return {"rgba": _rgba(p.color()), "width": p.widthF(), "style": p.style().name}

    def _brush(self):
        b = self.brush()
        return {"style": b.style().name, "rgba": _rgba(b.color())}

    def drawPath(self, path, *a):
        _Rec.ops.append({"op": "path", "who": self.who, "path": _path(path),
                         "pen": self._pen(), "brush": self._brush()})
        return super().drawPath(path, *a)

    def drawRoundedRect(self, rect, rx, ry, *a):
        _Rec.ops.append({"op": "rounded_rect", "who": self.who, "rect": _rect(rect),
                         "rx": rx, "pen": self._pen(), "brush": self._brush()})
        return super().drawRoundedRect(rect, rx, ry, *a)

    def drawText(self, *a):
        if len(a) >= 3 and isinstance(a[2], str):
            f = self.font()
            _Rec.ops.append({"op": "text", "who": self.who, "text": a[2],
                             "rect": _rect(a[0]), "pen": self._pen(),
                             "pixel_size": QFontMetricsF(f).height() and f.pixelSize(),
                             "point_size": f.pointSizeF(), "bold": f.bold(),
                             "family": f.family()})
        return super().drawText(*a)


def _define():
    gen = conftest.fix._fixture_function()
    next(gen) if hasattr(gen, "__next__") else None
    for key, dtype, lo, hi, tol in AFC1_KEYS + TAPE_KEYS:
        try:
            fix.db.get_item(key)
        except KeyError:
            fix.db.define_item(key, key, dtype, lo, hi, "", tol, "")
    fix.db.get_item("IAS").set_aux_value("Vne", 160) if False else None


def _reset(values):
    for key, dtype, _lo, _hi, _tol in AFC1_KEYS:
        item = fix.db.get_item(key)
        item.value = INITIAL[dtype]
        item.old = item.bad = item.fail = False
    for k, v in values.items():
        item = fix.db.get_item(k)
        item.value = v
        item.old = item.bad = item.fail = False


def _build(type_, opts, w, h):
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_options as so
    from pyefis.screens import screenbuilder_preferences as sp
    screen = QWidget()
    screen.encoder_list = []
    cfg = {"type": type_, "options": dict(opts)}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    widget = sf.REGISTRY[type_].builder(screen, cfg, font_percent=font_percent,
                                        font_family=font_family)
    screen.instruments = {0: widget}
    so.apply_options(screen, 0, cfg)
    widget.resize(int(w), int(h))
    screen.resize(int(w), int(h))
    screen.show()
    QApplication.processEvents()
    return screen, widget


def _record(widget, modules):
    saved = [m.QPainter for m in modules]
    _Rec.ops = []
    try:
        for m in modules:
            m.QPainter = _Rec
        widget.grab()
    finally:
        for m, q in zip(modules, saved):
            m.QPainter = q
    return _Rec.ops


STATES = {
    "ias_active": dict(APENG=True, APVERT="IAS", IASSEL=90.0, IAS=110.0),
    "ias_armed": dict(APENG=True, APVERT="PIT", APVERTARM="IAS", IASSEL=90.0, IAS=110.0),
    "ias_unset": dict(APENG=True, APVERT="IAS", IASSEL=0.0, IAS=110.0),
    "alt_capture": dict(APENG=True, APVERT="VS", VSSEL=500, APALTCAP=True,
                        ALT=3500.0, ALTSEL=4000),
    "alt_plain": dict(APENG=True, APVERT="VS", VSSEL=500, ALT=3500.0, ALTSEL=4000),
    "vs_active": dict(APENG=True, APVERT="VS", VSSEL=500, VS=480.0),
    "vs_off": dict(APENG=True, APVERT="ALT", VSSEL=500, VS=480.0),
}


def run(type_, opts, w, h, state):
    from pyefis.instruments import airspeed, altimeter, vsi
    from pyefis.instruments.altimeter import altsel
    _reset(STATES[state])
    screen, widget = _build(type_, opts, w, h)
    ops = _record(widget, [airspeed, altimeter, altsel, vsi])
    out = {"type": type_, "options": opts, "w": int(w), "h": int(h), "state": state,
           "ops": [o for o in ops if o["op"] != "text" or o["who"] in
                   ("QWidget", "AltSelBox")]}
    if type_ == "airspeed_tape":
        out["ias_shown"] = widget.ias_target_shown()
        out["ias_box_zone"] = _rect(widget.ias_box_zone())
        out["ias_box_rect"] = _rect(widget.ias_box_rect())
        out["tas_box_top"] = widget._tas_box_top()
    if type_ == "altimeter_tape":
        box = widget.altsel_display
        out["altsel_shown"] = bool(box is not None and box.isVisible())
        if out["altsel_shown"]:
            out["altsel_geom"] = _rect(box.geometry())
            out["altsel_value_rect"] = _rect(box.value_rect())
            out["altsel_font_pt"] = box._font_pt
        out["vs_bug"] = bool(widget.vs_bug)
    screen.hide()
    return out


def cases():
    out = []
    for w in (50, 70, 100, 140):
        out.append(("airspeed_tape", {}, w, 400, "ias_active"))
        out.append(("airspeed_tape", {}, w, 400, "ias_armed"))
    for extra in ({"ias_bug": False}, {"ias_bug_color": "#ff00ff"},
                  {"touch_edit": False}, {"show_tas": False}):
        out.append(("airspeed_tape", extra, 70, 400, "ias_active"))
    out.append(("airspeed_tape", {}, 70, 400, "ias_unset"))
    for w in (60, 90, 140):
        out.append(("altimeter_tape", {}, w, 520, "alt_capture"))
        out.append(("altimeter_tape", {}, w, 520, "alt_plain"))
        out.append(("altimeter_tape", {"dbkey": "VS", "maxalt": 4000,
                                       "majorDiv": 500, "minorDiv": 100},
                    w, 520, "vs_active"))
    for extra in ({"altsel_capture_color": "#ff00ff"}, {"altsel_box": False},
                  {"alt_bug_color": "#ffff00"}):
        out.append(("altimeter_tape", extra, 90, 520, "alt_capture"))
    vs = {"dbkey": "VS", "maxalt": 4000, "majorDiv": 500, "minorDiv": 100}
    for extra in ({"vs_bug": False}, {"vs_bug_color": "#ff00ff"}):
        out.append(("altimeter_tape", dict(vs, **extra), 90, 520, "vs_active"))
    out.append(("altimeter_tape", vs, 90, 520, "vs_off"))
    out.append(("altimeter_tape", {"vs_bug": True}, 90, 520, "vs_active"))
    for w in (24, 36, 60):
        out.append(("vsi_pfd", {}, w, 300, "vs_active"))
    for extra in ({"vs_bug": False}, {"vs_bug_color": "#ff00ff"}):
        out.append(("vsi_pfd", extra, 36, 300, "vs_active"))
    out.append(("vsi_pfd", {}, 36, 300, "vs_off"))
    return out


def _rev():
    import pyefis
    try:
        return subprocess.run(["git", "-C", pyefis.__file__.rsplit("/src/", 1)[0],
                               "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
    except OSError:
        return ""


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841
    _define()
    json.dump({"generator": "configurator/tools/ap_tape_cues_oracle.py",
               "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
               "cases": [run(*c) for c in cases()]},
              sys.stdout, indent=None, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
