# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5812 (AFC3 twin): dump what the REAL pyEfis autopilot_panel
# (instruments/autopilot, pyEfis #497) paints, as the fixture the editor's
# twin (apPanelGeom / apPanelSvg in public/editor.html) is tested against.
#
# Each case builds the widget through pyEfis's builder (apply_preferences
# resolves the font, build_autopilot_panel constructs it) and applies the
# options through apply_options, the device's own path (it dispatches
# `layout` through the Prop's setter:setPanelLayout since #497's cb7917b;
# before that `layout: compact` was inert, AER-5807). The schema `preview`
# block is turned into
# the FIX state the widget reads (preview_state below), so the widget reaches
# each look by its own logic. Paint calls are recorded by swapping the
# module's QPainter for a subclass that records each call and then paints it;
# no paint formula is re-typed here. The pyEfis source is imported from a
# checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis with #497, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
#       python3 tools/autopilot_panel_oracle.py > test/fixtures/autopilot_panel_pyefis.json
#
# The fixture also carries Qt's font metrics per pixel size (the `metrics`
# block): fit_to_mask's loop depends on them, and they depend only on the
# rounded pixel size (checked over 397 sizes per family). The twin's
# AP_FONT_METRICS table is generated from it with --metrics-js.
#
# With --render OUT.png LAYOUT W H [FAMILY] it instead saves one frame of the
# panel in the schema's preview state, the device half of the side-by-side;
# OPTIONS is optional JSON merged into its options.

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

FAMILIES = ("DejaVu Sans Condensed", "DejaVu Sans")
MASKS = ("ALT SEL", "VNAV", "000", "-0000", "00000")
METRIC_PX = range(1, 401)

KEYS = {
    "VSSEL": ("int", -30000, 30000), "IASSEL": ("float", 0.0, 999.9),
    "APCMD": ("str", None, None), "APCMDSEQ": ("int", 0, 2147483647),
    "APENG": ("bool", None, None), "FDENG": ("bool", None, None),
    "YDENG": ("bool", None, None), "APLAT": ("str", None, None),
    "APLATARM": ("str", None, None), "APVERT": ("str", None, None),
    "APVERTARM": ("str", None, None), "APALTCAP": ("bool", None, None),
    "APCAPS": ("str", None, None), "APMSG": ("str", None, None),
    "HEADBUG": ("float", 0.0, 359.9), "ALTSEL": ("int", -1000, 60000),
    "HEAD": ("float", 0.0, 359.9), "VS": ("float", -30000, 30000),
    "IAS": ("float", 0.0, 999.0), "ALT": ("float", -2000, 60000),
}
ALL = ("APR", "NAV", "HDG", "TRK", "AP", "FD", "YD", "LVL", "IAS", "VNAV",
       "VS", "ALT")


def _schema_preview():
    from pyefis.screens import screenbuilder_factory as sf
    return dict(sf.REGISTRY["autopilot_panel"].preview)


def preview_state(pv):
    """The FIX values that make the real panel show the preview block: the
    `active` keys lit, the `armed` keys outlined, the `unavailable` keys left
    out of APCAPS, the box strings as targets and `message` as APMSG."""
    act, arm = set(pv.get("active", ())), set(pv.get("armed", ()))
    lat = vert = ""
    if "LVL" in act:
        lat = vert = "LVL"
    for k in ("HDG", "TRK"):
        if k in act:
            lat = k
    if "NAV" in act:
        lat = "GPS"
    for k in ("IAS", "VNAV", "VS", "ALT"):
        if k in act:
            vert = k
    if "APR" in act:
        vert = "GS"
    latarm = [{"NAV": "GPS"}.get(k, k) for k in ("NAV", "HDG", "TRK")
              if k in arm]
    vertarm = [{"APR": "GS"}.get(k, k) for k in ("APR", "IAS", "VNAV", "VS",
                                                 "ALT") if k in arm]

    def num(s, unset):
        s = str(s).strip()
        return unset if s in ("", "---") else float(s)
    return dict(
        APENG="AP" in act, FDENG="FD" in act, YDENG="YD" in act,
        APLAT=lat, APLATARM=",".join(latarm), APVERT=vert,
        APVERTARM=",".join(vertarm), APALTCAP=False,
        APCAPS=",".join(k for k in ALL if k not in pv.get("unavailable", ())),
        APMSG=pv.get("message", ""),
        HEADBUG=num(pv.get("hdg", ""), 0.0), VSSEL=int(num(pv.get("vs", ""), 0)),
        IASSEL=num(pv.get("ias", ""), 0.0),
        ALTSEL=int(num(pv.get("altsel", ""), 0)),
        HEAD=268.0, VS=0.0, IAS=112.0, ALT=6020.0)


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


def _path(pp):
    out = []
    for i in range(pp.elementCount()):
        e = pp.elementAt(i)
        out.append([e.type.name[0], e.x, e.y])   # M(ove), L(ine), C(urve), D(ata)
    return out


class _RecPainter(QPainter):
    """QPainter that records what the panel and its keys and boxes paint,
    then paints it. `who` names the widget the painter was opened on."""
    ops = None

    def __init__(self, dev=None):
        super().__init__(dev)
        self.who = getattr(dev, "name", None) or getattr(dev, "which", None) \
            or "panel"

    def _pen(self, cap=False):
        p = self.pen()
        out = {"rgba": _rgba(p.color()), "width": p.widthF(),
               "style": p.style().name}
        if cap:
            out["cap"] = p.capStyle().name
        return out

    def _brush(self):
        b = self.brush()
        return {"style": b.style().name, "rgba": _rgba(b.color())}

    def _rec(self, op, **kw):
        t = self.worldTransform()
        if t.dx() or t.dy():
            kw.update(dx=t.dx(), dy=t.dy())
        _RecPainter.ops.append(dict(op=op, who=self.who, **kw))

    def fillRect(self, rect, color, *a):
        self._rec("fill_rect", rect=_rect(rect), rgba=_rgba(QColor(color)))
        return super().fillRect(rect, color, *a)

    def drawRoundedRect(self, rect, rx, ry, *a):
        self._rec("rounded_rect", rect=_rect(rect), rx=rx, ry=ry,
                  pen=self._pen(), brush=self._brush())
        return super().drawRoundedRect(rect, rx, ry, *a)

    def drawEllipse(self, rect, *a):
        self._rec("ellipse", rect=_rect(rect), pen=self._pen(),
                  brush=self._brush())
        return super().drawEllipse(rect, *a)

    def drawPath(self, path, *a):
        self._rec("path", path=_path(path), pen=self._pen(cap=True),
                  brush=self._brush())
        return super().drawPath(path, *a)

    def drawText(self, rect, flags, text, *a):
        f = self.font()
        fm = QFontMetricsF(f)
        br = self.boundingRect(rect, flags, text)
        self._rec("text", text=text, rect=_rect(rect), flags=int(flags),
                  pen=self._pen(), family=f.family(),
                  point_size=f.pointSizeF(), pixel_size=f.pixelSize(),
                  bold=f.bold(), laid_out=_rect(br), ascent=fm.ascent(),
                  descent=fm.descent())
        return super().drawText(rect, flags, text, *a)


def _define():
    fix.initialize({"main": {"FixServer": "localhost", "FixPort": "3490"}})
    for key, (dtype, lo, hi) in KEYS.items():
        fix.db.define_item(key, key, dtype, lo, hi, "", 0, "")


def _set(values, failed=()):
    for k, v in values.items():
        fix.db.set_value(k, v)
    for k in KEYS:
        i = fix.db.get_item(k)
        i.old = i.bad = False
        i.fail = k in failed


def _build(opts, pv, w, h):
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_options as so
    from pyefis.screens import screenbuilder_preferences as sp
    # A box shown as "---" has no target: its bug key failed (IASSEL 0 is
    # IAS's own "unset", so that box needs no failure).
    _set(preview_state(pv), [key for box, key in (("hdg", "HEADBUG"),
                                                  ("vs", "VSSEL"),
                                                  ("altsel", "ALTSEL"))
                             if str(pv.get(box, "")).strip() in ("", "---")])
    screen = QWidget()
    screen.encoder_list = []
    cfg = {"type": "autopilot_panel", "options": dict(opts)}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    panel = sf.build_autopilot_panel(screen, cfg, font_percent=font_percent,
                                     font_family=font_family)
    screen.instruments = {0: panel}
    so.apply_options(screen, 0, cfg)
    if "layout" in cfg["options"] and panel.panel_layout != \
            str(cfg["options"]["layout"]).lower():
        raise SystemExit("apply_options left layout at %r: this pyEfis lacks "
                         "#497's setter dispatch (cb7917b)" % panel.panel_layout)
    panel.resize(int(w), int(h))
    return screen, panel


def run(opts, w, h, pv=None):
    from pyefis.instruments import autopilot as ap
    pv = _schema_preview() if pv is None else pv
    screen, panel = _build(opts, pv, w, h)
    ap.QPainter = _RecPainter
    _RecPainter.ops = []
    try:
        panel.grab()
    finally:
        ap.QPainter = QPainter
    kids = {name: _rect(b.geometry()) for name, b in panel.button_widgets.items()}
    boxes = {name: _rect(b.geometry()) for name, b in panel.boxes.items()}
    out = {"options": opts, "w": int(w), "h": int(h), "preview": pv,
           "family": panel.font_family, "buttons": list(panel.button_widgets),
           "button_rects": kids, "box_rects": boxes,
           "message_rect": _rect(panel.message_rect()),
           "ops": _RecPainter.ops}
    panel._timer.stop()
    return out


def metrics():
    """Qt's metrics fit_to_mask reads, per family and rounded pixel size."""
    out = {}
    for fam in FAMILIES:
        rows = []
        for px in METRIC_PX:
            f = QFont(fam)
            f.setPixelSize(px)
            fm = QFontMetricsF(f)
            rows.append([px, fm.ascent(), fm.descent(),
                         fm.boundingRect(MASKS[0]).height()]
                        + [fm.horizontalAdvance(m) for m in MASKS])
        out[fam] = rows
    return {"masks": list(MASKS), "columns": ["px", "ascent", "descent",
                                              "height"] + list(MASKS),
            "families": out}


def fit_cases():
    """fit_to_mask itself over a sweep of boxes, for the JS port of the loop."""
    from pyefis.instruments import helpers
    out = []
    for fam in FAMILIES:
        for m in MASKS:
            for W in (6, 11, 17, 24, 33, 47, 60, 75, 90, 120, 160, 230):
                for H in (4, 7, 9, 13, 18, 24, 31, 40, 55, 72):
                    out.append([fam, m, W, H, helpers.fit_to_mask(W, H, m, fam)])
    return out


def cases():
    pv = _schema_preview()

    def v(**kw):
        return dict(pv, **kw)
    full = [(640, 260), (480, 200), (320, 140), (800, 300), (400, 400),
            (1000, 200), (240, 100), (517, 233)]
    compact = [(900, 110), (640, 90), (1200, 150), (500, 80), (300, 60),
               (777, 123)]
    out = []
    for lay, sizes in (("full", full), ("compact", compact)):
        base = {} if lay == "full" else {"layout": "compact"}
        for w, h in sizes:
            out.append((dict(base), w, h, None))
        w, h = sizes[0]
        for extra in (
                {"buttons": "AP,FD,HDG"},
                {"buttons": ""},
                {"buttons": ",".join(ALL)},
                {"buttons": "ap, hdg ,bogus,ALT"},
                {"buttons": ["ALT", "AP", "TRK"]},
                {"buttons": "APR,NAV,LVL,VNAV"},
                {"touch_edit": False},
                {"font_percent": 150},
                {"font_percent": 50},
                {"font_percent": 0.8},
                {"font_family": "DejaVu Sans"},
                {"active_color": "#ff00ff", "armed_color": "#ffff00",
                 "caution_color": "#ff0000", "key_color": "#303030",
                 "level_color": "#806000", "text_color": "#e0e0e0",
                 "muted_color": "#606060", "line_color": "#00ff80",
                 "target_color": "#ff8000", "bg_color": "#102030"},
                ):
            out.append((dict(base, **extra), w, h, None))
        for p in (
                v(message="NAV: NO NAV SOURCE"),
                v(active=["AP", "FD", "HDG", "IAS"], armed=["ALT"], ias="95"),
                v(active=["AP", "FD", "LVL"], armed=[]),
                v(active=["AP", "NAV", "APR"], armed=[], ias="90"),
                v(active=[], armed=[], unavailable=["APR", "YD", "VNAV", "IAS", "LVL"]),
                v(active=["HDG"], armed=["NAV", "APR", "VS"], hdg="005", vs="-1200"),
                v(hdg="---", vs="0", ias="---", altsel="---"),
                ):
            out.append((dict(base), w, h, p))
        # A greyed key that is also active shows active (device priority).
        out.append((dict(base), w, h, v(unavailable=["AP", "VNAV"])))
        out.append((dict(base, buttons="HDG,TRK,VS"), w, h,
                    v(active=["TRK", "VS"], armed=["HDG"])))
    return out


def render(path, layout, w, h, family=None):
    opts = {} if layout == "full" else {"layout": layout}
    if family:
        opts["font_family"] = family
    opts.update(json.loads(os.environ.get("OPTIONS") or "{}"))
    screen, panel = _build(opts, _schema_preview(), w, h)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    panel.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    panel._timer.stop()
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
                             "at a dir holding the DejaVu TTFs" % (fam, got))


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841 (kept alive)
    _fonts()
    _define()
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], argv[3], int(argv[4]), int(argv[5]), *argv[6:7])
    else:
        json.dump({"generator": "configurator/tools/autopilot_panel_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "schema_preview": _schema_preview(),
                   "metrics": metrics(), "fit_cases": fit_cases(),
                   "cases": [run(*c) for c in cases()]},
                  sys.stdout, indent=None, separators=(",", ":"))
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
