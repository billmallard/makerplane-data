# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6356 (CAS3 twins): dump what the REAL pyEfis crew alerting widgets
# (instruments/cas/__init__.py CASWindow and CASMarker, pyEfis #552) paint,
# as the fixture the editor's twins (casWindowGeom / casMarkerGeom /
# casTileSvg in public/editor.html) are tested against.
#
# Each case builds the widget through pyEfis's builder (apply_preferences
# resolves the font, build_cas_window / build_cas_marker construct it) and
# applies the options through apply_options, the device's own path. The look
# is reached through the CAS2 FIX keys the widgets read: a case's slots are
# written to CASMSG1..12, CASCNT and CASSEQ the way the gateway's plugin
# writes them, so the widget's own model parses, sorts and counts them. The
# widget's clock is pinned so the flash phase is known. Paint calls are
# recorded by swapping the cas module's QPainter for a subclass that records
# each call and then paints it; no paint formula is re-typed here. The pyEfis
# source is imported from a checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis with #552, PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
#       python3 tools/cas_oracle.py > test/fixtures/cas_pyefis.json
#
# FONTCONFIG_FILE must name a dir holding the DejaVu TTFs and B612's OTFs
# (the oracle refuses to run if a family falls back).
#
# With --render OUT.png TYPE W H STATE T it instead saves one frame, the
# device half of the side-by-side; OPTIONS is optional JSON merged into its
# options. STATE is a key of WINDOW_STATES or MARKER_STATES.

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
from PyQt6.QtGui import (QColor, QFont, QFontInfo, QFontMetricsF,  # noqa: E402
                         QImage, QPainter, QRegion)
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

import pyavtools.fix as fix  # noqa: E402

FAMILIES = ("DejaVu Sans Condensed", "DejaVu Sans", "B612")

# key, dtype, min, max, tol: fix-gateway database/cas.yaml, as pyEfis's
# tests/instruments/cas/conftest.py defines them.
CAS_KEYS = [("CASMSG{}".format(n), "str", None, None, 3000)
            for n in range(1, 13)] + [
    ("CASCNT", "int", 0, 1000, 3000),
    ("CASTOP", "int", 0, 3, 3000),
    ("CASSEQ", "int", 0, 2147483647, 3000),
    ("CASACK", "int", 0, 2147483647, 0),
]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _schema_preview(type_):
    from pyefis.screens import screenbuilder_factory as sf
    return dict(sf.REGISTRY[type_].preview)


def _many(n, level="2", ack="0"):
    return ["{}|{}|M{}|MESSAGE {}|".format(level, ack, i, i) for i in range(n)]


# Each state: (slots in CASMSG order, CASCNT or None for the slot count).
# None for the slots means the schema preview's `lines`.
WINDOW_STATES = {
    "preview": (None, None),
    "quiet": ([], None),
    "all_acked": (["3|1|APLINK|AP LINK LOST|", "2|1|NAVFAIL|NAV FAIL|",
                   "1|1|CASTEST|CAS TEST|"], None),
    "advisory_unacked": (["1|0|TRAFFIC|TRAFFIC|", "1|1|CASTEST|CAS TEST|"],
                         None),
    "detail": (["2|0|FUELLOW|FUEL LOW|L TANK", "1|1|PITOT|PITOT HEAT|OFF"],
               None),
    "single_warning": (["3|0|OILP|OIL PRESSURE|"], None),
    # Out of order on the bus: the widget's model sorts it (brief 5.2).
    "unsorted": (["1|0|ADV|ADVISORY ONE|", "3|1|WACK|WARNING ACKED|",
                  "2|0|CAU|CAUTION ONE|", "3|0|WNEW|WARNING NEW|"], None),
    # A malformed slot is dropped, never repaired (CASW-SRC-001).
    "malformed": (["4|0|X|BAD LEVEL|", "garbage", "2|0|NAVFAIL|NAV FAIL|",
                   "2|2|X|BAD ACK|", "1|0|X||"], None),
    "overflow": (["3|0|W1|ENGINE FIRE|", "2|0|C1|GEN FAIL|",
                  "2|1|C2|NAV FAIL|", "1|0|A1|TRAFFIC|", "1|1|A2|CAS TEST|",
                  "1|1|A3|PITOT HEAT|", "2|0|C3|FUEL LOW|"], None),
    "overflow_beyond": (_many(12), 15),
    "overflow_acked": (["3|1|W1|ENGINE FIRE|"] + _many(5, "1", "1"), None),
}

# Each marker state: one list whose highest level the marker names.
MARKER_STATES = {
    "preview": (["2|0|NAVFAIL|NAV FAIL|"], None),
    "quiet": ([], None),
    "warning": (["3|0|APLINK|AP LINK LOST|", "2|0|NAVFAIL|NAV FAIL|"], None),
    "warning_acked": (["3|1|APLINK|AP LINK LOST|"], None),
    "caution_acked": (["2|1|NAVFAIL|NAV FAIL|"], None),
    "advisory": (["1|0|TRAFFIC|TRAFFIC|"], None),
    "advisory_acked": (["1|1|CASTEST|CAS TEST|"], None),
    # Highest is a warning, but only the advisory is unacknowledged: it still
    # flashes (CASW-MRK-001).
    "warning_acked_adv_new": (["3|1|W|ENGINE FIRE|", "1|0|A|TRAFFIC|"], None),
}


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class _RecPainter(QPainter):
    """QPainter that records what the widget paints, then paints it."""
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

    def drawRect(self, rect, *a):
        b = self.brush()
        self._rec("rect", rect=_rect(rect), pen=self._pen(),
                  brush=b.style().name, brush_rgba=_rgba(b.color()))
        return super().drawRect(rect, *a)

    def drawRoundedRect(self, rect, *a):
        self._rec("rounded_rect", rect=_rect(rect))
        return super().drawRoundedRect(rect, *a)

    def drawText(self, rect, flags, text, *a):
        f = self.font()
        fm = QFontMetricsF(f)
        br = self.boundingRect(rect, int(flags), text)
        self._rec("text", text=text, rect=_rect(rect), flags=int(flags),
                  pen=self._pen(), family=f.family(),
                  pixel_size=f.pixelSize(), bold=f.bold(),
                  laid_out=_rect(br), ascent=fm.ascent(),
                  descent=fm.descent(), height=fm.height(),
                  advance=fm.horizontalAdvance(text))
        return super().drawText(rect, flags, text, *a)


def _define():
    fix.initialize({"main": {"FixServer": "localhost", "FixPort": "3490"}})
    for key, dtype, lo, hi, tol in CAS_KEYS:
        fix.db.define_item(key, key, dtype, lo, hi, "", tol, "")


_seq = [0]


def _publish(slots, count):
    """Write a list the way the cas plugin does: slots, CASCNT, CASSEQ last."""
    slots = list(slots) + [""] * (12 - len(slots))
    for n, v in enumerate(slots):
        fix.db.get_item("CASMSG{}".format(n + 1)).value = v
    fix.db.get_item("CASCNT").value = (
        count if count is not None else sum(1 for s in slots if s))
    _seq[0] += 1
    fix.db.get_item("CASSEQ").value = _seq[0]
    for key, _d, _lo, _hi, _tol in CAS_KEYS:
        item = fix.db.get_item(key)
        item.old = item.bad = item.fail = False


def _build(type_, opts, state, w, h, t):
    from pyefis.instruments import cas
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_options as so
    from pyefis.screens import screenbuilder_preferences as sp
    if type_ == "cas_window":
        slots, count = WINDOW_STATES[state]
        if slots is None:
            slots = _schema_preview("cas_window")["lines"]
    else:
        slots, count = MARKER_STATES[state]
    cas.reset_shared_model()
    _publish(slots, count)
    screen = QWidget()
    screen.encoder_list = []
    cfg = {"type": type_, "options": dict(opts)}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    builder = sf.build_cas_window if type_ == "cas_window" else sf.build_cas_marker
    widget = builder(screen, cfg, font_percent=font_percent,
                     font_family=font_family)
    screen.instruments = {0: widget}
    so.apply_options(screen, 0, cfg)
    widget._timer.stop()
    widget.clock = Clock(t)
    widget.resize(int(w), int(h))
    # The model reads the list on first use; assert it took the case's list
    # before anything is recorded (a stale or half-read list would silently
    # paint the previous case).
    model = widget.model
    want = [m for m in (cas.cm.parse_slot(s) for s in slots) if m is not None]
    assert model.list_ok and not model.local_only, model.reason
    assert sorted(model.messages(), key=cas.cm.sort_key) == model.messages()
    assert len(model.messages()) == len(want), (state, model.messages())
    return screen, widget


def _messages(widget):
    return [{"level": m.level, "acked": m.acked, "id": m.id, "line": m.line}
            for m in widget.model.messages()]


def run(type_, opts, w, h, state="preview", t=1000.0):
    from pyefis.instruments import cas, tile
    screen, widget = _build(type_, opts, state, w, h, t)
    cas.QPainter = _RecPainter
    _RecPainter.ops = []
    try:
        widget.grab()
    finally:
        cas.QPainter = QPainter
    out = {"type": type_, "options": opts, "w": int(w), "h": int(h),
           "state": state, "t": t, "on": tile.blink_on(t),
           "family": widget.font_family, "font_percent": widget.font_percent,
           "messages": _messages(widget), "count": widget.model.count(),
           "ops": _RecPainter.ops}
    if type_ == "cas_window":
        out["rows"] = [{"text": tl.text, "level": tl.level, "flash": tl.flash,
                        "kind": kind} for tl, kind in widget.rows()]
    else:
        mt = widget.marker_tile()
        out["tile"] = None if mt is None else {
            "text": mt.text, "level": mt.level, "flash": mt.flash}
    return out


def cases():
    out = []
    # The shipped pfd.yaml / radio.yaml placements on a 1920 x 1080 and a
    # 1024 x 600 panel (window 29 x 20 cells, marker 20 x 6, of 200 x 110),
    # then sizes around them.
    win_sizes = [(278, 196), (148, 109), (360, 160), (300, 120), (480, 200),
                 (200, 60), (517, 143), (800, 240), (150, 40), (420, 90)]
    for w, h in win_sizes:
        for t in (1000.0, 1000.5):
            out.append(("cas_window", {}, w, h, "preview", t))
    for state in WINDOW_STATES:
        if state == "preview":
            continue
        for t in (1000.0, 1000.5):
            out.append(("cas_window", {}, 360, 160, state, t))
    for lines in (1, 2, 3, 6, 12, "3", 0, 2.7):
        for state in ("preview", "overflow"):
            out.append(("cas_window", {"lines": lines}, 360, 160, state, 1000.0))
    out.append(("cas_window", {"lines": 2}, 360, 80, "overflow_beyond", 1000.5))
    out.append(("cas_window", {"lines": 2}, 360, 80, "overflow_acked", 1000.0))
    for extra in (
            {"font_percent": 0.5},
            {"font_percent": 1.0},
            {"font_percent": 50},
            {"font_family": "DejaVu Sans"},
            {"font_family": "B612"},
            {"warning_color": "#ff00ff", "caution_color": "#ffff00",
             "advisory_color": "#00ffff", "background_color": "#102030"},
            {"caution_color": "#80ffaa00", "advisory_color": "#abc"},
            {"background_opacity": 0},
            {"background_opacity": 0.3},
            {"background_opacity": 1},
            {"background_opacity": 1.7},
            {"background_opacity": -0.5},
            {"background_opacity": "0.5"},
            {"background_color": "#80102030"},
            {"status_path": "/nonexistent/status.json"},
            ):
        for t in (1000.0, 1000.5):
            out.append(("cas_window", extra, 360, 160, "preview", t))
    out.append(("cas_window", {"font_family": "B612"}, 360, 160, "detail", 1000.0))
    out.append(("cas_window", {"font_family": "DejaVu Sans"}, 200, 60, "overflow", 1000.5))

    mrk_sizes = [(192, 59), (102, 33), (160, 40), (120, 30), (200, 60), (90, 24), (300, 50),
                 (137, 41), (60, 60)]
    for w, h in mrk_sizes:
        for t in (1000.0, 1000.5):
            out.append(("cas_marker", {}, w, h, "preview", t))
    for state in MARKER_STATES:
        if state == "preview":
            continue
        for t in (1000.0, 1000.5):
            out.append(("cas_marker", {}, 160, 40, state, t))
    for extra in (
            {"font_percent": 0.5},
            {"font_family": "DejaVu Sans"},
            {"font_family": "B612"},
            {"warning_color": "#ff00ff", "caution_color": "#ffff00",
             "advisory_color": "#00ffff", "background_color": "#102030"},
            {"background_opacity": 0},
            {"background_opacity": 1.7},
            {"background_opacity": "0.5"},
            ):
        for t in (1000.0, 1000.5):
            out.append(("cas_marker", extra, 160, 40, "preview", t))
    out.append(("cas_marker", {"warning_color": "#ff8000"}, 160, 40, "warning", 1000.5))
    # Every scene of the side-by-side (tools/cas_scenes.json) is a case too,
    # so each picture is also pinned by the test.
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "cas_scenes.json")) as f:
        for sc in json.load(f):
            case = (sc["type"], sc["options"], sc["w"], sc["h"], sc["state"], sc["t"])
            if case not in out:
                out.append(case)
    return out


def render(path, type_, w, h, state, t):
    opts = json.loads(os.environ.get("OPTIONS") or "{}")
    screen, widget = _build(type_, opts, state, w, h, t)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    widget.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
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
    for fam in FAMILIES:
        f = QFont(fam)
        f.setBold(True)
        got = QFontInfo(f).family()
        if got != fam:
            raise SystemExit("font %r resolved to %r; point FONTCONFIG_FILE "
                             "at a dir holding the DejaVu TTFs and B612's "
                             "OTFs" % (fam, got))


def main(argv):
    app = QApplication.instance() or QApplication(argv[:1])  # noqa: F841 (kept alive)
    _fonts()
    _define()
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], argv[3], int(argv[4]), int(argv[5]), argv[6],
               float(argv[7]))
    else:
        json.dump({"generator": "configurator/tools/cas_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "schema_preview": {t: _schema_preview(t)
                                      for t in ("cas_window", "cas_marker")},
                   "cases": [run(*c) for c in cases()]},
                  sys.stdout, indent=None, separators=(",", ":"))
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
