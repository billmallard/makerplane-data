# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-5272 (G11b twin): dump what the REAL pyEfis moving-map ND mode (G11a
# EXPANDED, G11b CTR MAP, G11c EXPANDED polish, billmallard/pyEfis#134,
# map/nd.py) computes and
# paints, as the fixture the editor's twin (ndGeometry / ndPaintOps /
# rangeRingGeom in public/editor.html) is tested against. Nothing here
# re-types a pyEfis formula:
#
# - geometry comes from nd.geometry / vdev_geometry / tick_bearings;
# - the map transform (ownship anchor, range px) and the range rings come from
#   a real MovingMap's _transform() and a real RangeRingsLayer configured off
#   it, so the ND override of ownship_position and range_px is the widget's;
# - the strokes and the text come from running nd.paint() against a recording
#   painter (the zoom rail oracle's pattern);
# - the corner-block models come from nd.top_left / top_right / bottom_left /
#   vdev over a table of read(key) answers;
# - the G11c backing panels are recorded at nd._backing, which is wrapped so
#   the op carries the rects it was handed (the painted path is their union,
#   which an SVG path with one fill reproduces) and then runs as written;
# - each ring label's keep-out verdict is RangeRingsLayer.label_kept_out over
#   the transform's own label_keep_out.
#
# The pyEfis source is imported from a checkout, never copied:
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev (G11c, #443), PyQt6 installed
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools \
#       python3 tools/nd_mode_oracle.py > test/fixtures/nd_mode_pyefis.json
#
# With --render OUT.png W H MODE it instead saves one no-data frame of the
# widget at 10 NM in that ND mode, the device half of the screenshot
# comparison (tools/range_rings_compare.py composes it with the twin). "No
# data" is the editor's view: LAT/LONG/TRACKM fresh at the mock's values,
# every ND key absent (read -> None), as on a gateway that defines none.

import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import render_instrument as ri  # noqa: E402  (pyEfis tools/: mock FIX, fonts)

from PyQt6.QtWidgets import QApplication  # noqa: E402

SIZES = [(800, 480), (650, 1000), (1024, 600), (300, 240), (1920, 1080),
         (400, 1200), (2000, 300)]

# (options, w, h): the paint + transform cases. Every case runs with no ND
# data (the editor's default view); options vary what the editor can vary.
PAINT_CASES = (
    [({"nd_mode": m}, w, h) for m in ("expanded", "ctr") for w, h in SIZES]
    + [
        ({"nd_mode": "expanded", "range_nm": 25}, 800, 480),
        ({"nd_mode": "ctr", "range_nm": 2.5}, 800, 480),
        ({"nd_mode": "expanded", "ownship_position": 10, "orientation": "north_up"}, 800, 480),
        ({"nd_mode": "ctr", "ownship_position": 90, "orientation": "north_up"}, 650, 1000),
        ({"nd_mode": "expanded", "layer_fixes": True}, 800, 480),
        ({"nd_mode": "ctr", "layer_airports": False, "layer_navaids": False}, 800, 480),
        ({"nd_mode": "ctr", "layer_fixes": True, "layer_navaids": 0}, 800, 480),
        ({"nd_mode": "expanded", "layer_airports": "false"}, 800, 480),
        ({"nd_mode": " CTR "}, 800, 480),
        ({"nd_mode": "Expanded"}, 650, 1000),
        ({"nd_mode": "bogus"}, 800, 480),
        ({"nd_mode": "off"}, 800, 480),
        ({"nd_mode": "off", "ownship_position": 25}, 1024, 600),
        ({}, 800, 480),
        ({"nd_mode": "expanded", "range_ring_fractions": "0.25,0.5,0.75,1",
          "range_ring_label_angle": 45}, 800, 480),
        ({"nd_mode": "ctr", "range_ring_fractions": "0.5,1,1.5"}, 650, 1000),
        # G11c: the only kind of panel where the arc ends short of the
        # corner blocks and r = dx / sin(60) binds.
        ({"nd_mode": "expanded"}, 340, 150),
    ])

# QRectF.intersects over edge cases, for the ring-label keep-out test:
# (x, y, w, h) pairs that overlap, touch, or are empty.
RECT_PAIRS = [
    ((0, 0, 10, 10), (5, 5, 10, 10)),
    ((0, 0, 10, 10), (10, 0, 10, 10)),
    ((0, 0, 10, 10), (0, 10, 10, 10)),
    ((0, 0, 10, 10), (9.999, 9.999, 1, 1)),
    ((0, 0, 10, 10), (2, 2, 0, 5)),
    ((0, 0, 10, 10), (2, 2, 5, 0)),
    ((0, 0, 10, 10), (-5, -5, 5, 5)),
    ((0, 0, 10, 10), (3, 3, 2, 2)),
    ((0, 0, 10, 10), (20, 20, 5, 5)),
]

# read(key) tables for the model functions: (value, old, bad, fail). A key
# not listed reads as None (the gateway does not define it).


def _ok(v):
    return (v, False, False, False)


APPROACH = {
    "GS": _ok(112.0), "TAS": _ok(118.0), "WINDDIR": _ok(300.0),
    "WINDSPD": _ok(14.0), "FPLSTATE": _ok(1), "FPLPHASE": _ok("TERM"),
    "WPNAME": _ok("KSBA"), "WPDIS": _ok(8.84), "WPETE": _ok(284),
    "GSI": _ok(0.4), "GSV": _ok(1.0),
}


def _with(base, **kw):
    d = dict(base)
    d.update(kw)
    return d


READ_CASES = [
    ("none", {}),
    ("approach", APPROACH),
    ("gsflag", _with(APPROACH, GSI=(0.4, False, True, False))),
    ("gsfail", _with(APPROACH, GSI=(0.4, False, False, True))),
    ("gsold", _with(APPROACH, GSI=(0.4, True, False, False))),
    ("noglide", _with(APPROACH, FPLSTATE=_ok(0), WPNAME=_ok(""),
                      WPDIS=_ok(0.0), WPETE=_ok(0), GSV=_ok(0.0))),
    ("gsv_absent", {k: v for k, v in APPROACH.items() if k != "GSV"}),
    ("gsv_absent_bad", _with({k: v for k, v in APPROACH.items() if k != "GSV"},
                             GSI=(0.4, False, True, False))),
    ("gsv_text", _with(APPROACH, GSV=_ok("x"))),
    ("pegged_up", _with(APPROACH, GSI=_ok(2.5))),
    ("pegged_down", _with(APPROACH, GSI=_ok(-1.7))),
    ("gsi_text", _with(APPROACH, GSI=_ok("abc"))),
    ("vectors", _with(APPROACH, FPLPHASE=_ok(" VECTORS "))),
    ("plan_stale", _with(APPROACH, FPLSTATE=(1, True, False, False))),
    ("wpdis_fail", _with(APPROACH, WPDIS=(8.84, False, False, True))),
    ("wpdis_bad", _with(APPROACH, WPDIS=(8.84, False, True, False))),
    ("wpdis_far", _with(APPROACH, WPDIS=_ok(123.5))),
    ("wpdis_tie", _with(APPROACH, WPDIS=_ok(0.25))),
    ("wpete_fail", _with(APPROACH, WPETE=(284, False, False, True))),
    ("wpete_bad", _with(APPROACH, WPETE=(284, False, True, False))),
    ("wpete_text", _with(APPROACH, WPETE=_ok("soon"))),
    ("no_wpete", {k: v for k, v in APPROACH.items() if k != "WPETE"}),
    ("no_wpname", {k: v for k, v in APPROACH.items() if k != "WPNAME"}),
    ("wpname_blank", _with(APPROACH, WPNAME=_ok("   "))),
    ("fplstate_text", _with(APPROACH, FPLSTATE=_ok("x"))),
    ("gs_old", _with(APPROACH, GS=(112.0, True, False, False))),
    ("gs_fail", _with(APPROACH, GS=(112.0, False, False, True))),
    ("gs_text", _with(APPROACH, GS=_ok("fast"))),
    ("gs_half", _with(APPROACH, GS=_ok(112.5), TAS=_ok(113.5))),
    ("tas_only", {"TAS": _ok(118.0)}),
    ("wind_bad", _with(APPROACH, WINDSPD=(14.0, False, True, False))),
    ("wind_fail", _with(APPROACH, WINDDIR=(300.0, False, False, True))),
    ("wind_north", _with(APPROACH, WINDDIR=_ok(359.6), WINDSPD=_ok(0.4))),
    ("wind_dir_only", _with({k: v for k, v in APPROACH.items() if k != "WINDSPD"})),
]

# Fixed clock for the ETA: 2026-11-09 14:07:42 UTC.
NOW = 1794233262.0


class _Recorder:
    """Duck-typed QPainter: records what nd.paint draws, in device px. The
    world transform (translate/rotate, the wind arrow) is applied here, so
    every recorded point is already on screen."""

    def __init__(self):
        from PyQt6.QtGui import QTransform
        self.pen = None
        self.brush = None
        self.font = None
        self.tf = QTransform()
        self.stack = []
        self.ops = []

    def save(self):
        self.stack.append((self.pen, self.brush, self.font, self.tf))

    def restore(self):
        self.pen, self.brush, self.font, self.tf = self.stack.pop()

    def translate(self, pt):
        from PyQt6.QtGui import QTransform
        self.tf = QTransform().translate(pt.x(), pt.y()) * self.tf

    def rotate(self, deg):
        from PyQt6.QtGui import QTransform
        self.tf = QTransform().rotate(deg) * self.tf

    def setBrush(self, b):
        self.brush = b

    def setPen(self, p):
        self.pen = p

    def setFont(self, f):
        self.font = f

    def fontMetrics(self):
        from PyQt6.QtGui import QFontMetrics
        return QFontMetrics(self.font)

    def _pt(self, p):
        q = self.tf.map(p)
        return [q.x(), q.y()]

    def _pen(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPen, QColor
        pen = self.pen
        if isinstance(pen, QColor):
            pen = QPen(pen)
        if not isinstance(pen, QPen) or pen.style() == Qt.PenStyle.NoPen:
            return None
        c = pen.color()
        return {"rgb": c.name(), "width": pen.widthF()}

    def _brush(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QBrush
        if not isinstance(self.brush, QBrush) or self.brush.style() == Qt.BrushStyle.NoBrush:
            return None
        return self.brush.color().name()

    def _fontinfo(self):
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font)
        return {"px": self.font.pixelSize(), "bold": self.font.bold(),
                "ascent": fm.ascent(), "descent": fm.descent(),
                "height": fm.height()}

    def drawPath(self, path):
        from PyQt6.QtGui import QPainterPath
        segs = []
        # Arcs come out as cubic curves: keep each curve's end point only (the
        # control points are Qt's approximation), so the test can check every
        # point lies on the circle and where the arc starts and ends.
        T = QPainterPath.ElementType
        n = path.elementCount()
        for i in range(n):
            e = path.elementAt(i)
            if e.type == T.MoveToElement:
                segs.append(["M", e.x, e.y])
            elif e.type == T.LineToElement:
                segs.append(["L", e.x, e.y])
            elif e.type == T.CurveToElement:
                end = path.elementAt(i + 2)
                segs.append(["C", end.x, end.y])
        self.brush_alpha = (self.brush.color().alpha()
                            if self._brush() is not None else None)
        self.ops.append({"op": "path", "pen": self._pen(), "brush": self._brush(),
                         "segments": segs})

    def drawText(self, a, b, c=None):
        from PyQt6.QtCore import QRectF
        if isinstance(a, QRectF):
            self.ops.append({"op": "text_rect", "pen": self._pen(),
                             "rect": {"x": a.x(), "y": a.y(), "w": a.width(), "h": a.height()},
                             "flags": int(b.value) if hasattr(b, "value") else int(b),
                             "text": c, "font": self._fontinfo()})
        else:
            self.ops.append({"op": "text_at", "pen": self._pen(),
                             "pt": self._pt(a), "text": b, "font": self._fontinfo(),
                             "advance": self.fontMetrics().horizontalAdvance(b)})

    def backing(self, rects, fs):
        """Called by the wrapped nd._backing before it paints: the rects it
        unites and the panel colour its drawPath then fills them with."""
        n = len(self.ops)
        _BACKING(self, rects, fs)
        drawn = self.ops[n:]
        assert len(drawn) == 1 and drawn[0]["op"] == "path", drawn
        del self.ops[n:]
        self.ops.append({"op": "backing", "pen": drawn[0]["pen"],
                         "brush": drawn[0]["brush"],
                         "alpha": self.brush_alpha,
                         "radius": fs * 0.25,
                         "rects": [{"x": r.x(), "y": r.y(), "w": r.width(),
                                    "h": r.height()} for r in rects]})

    def drawRect(self, r):
        self.ops.append({"op": "rect", "pen": self._pen(), "brush": self._brush(),
                         "rect": {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}})

    def drawPolygon(self, poly):
        self.ops.append({"op": "polygon", "pen": self._pen(), "brush": self._brush(),
                         "pts": [self._pt(poly.at(i)) for i in range(poly.count())]})

    def drawLine(self, a, b):
        self.ops.append({"op": "line", "pen": self._pen(),
                         "pts": [self._pt(a), self._pt(b)]})


_BACKING = None


def _wrap_backing():
    """Route nd._backing through the recorder when one is painting."""
    global _BACKING
    from pyefis.instruments.map import nd
    _BACKING = nd._backing

    def backing(p, rects, fs):
        if isinstance(p, _Recorder):
            p.backing(rects, fs)
        else:
            _BACKING(p, rects, fs)
    nd._backing = backing


def _map(opts, w, h):
    from pyefis.instruments.map import MovingMap
    m = MovingMap()
    m.range_nm = 10.0
    m.font_family = "DejaVu Sans Condensed"
    for k, v in opts.items():
        setattr(m, k, v)
    m.resize(w, h)
    return m


def _none(_key):
    return None


def run_paint(opts, w, h):
    from pyefis.instruments.map import layers as map_layers
    from pyefis.instruments.map import nd
    from pyefis.instruments.map.layers import RangeRingsLayer
    m = _map(opts, w, h)
    x = m._transform()
    layer = RangeRingsLayer()
    layer.configure(m)
    rings = []
    for frac, r in zip(layer.fractions, layer.ring_radii_px(x)):
        lr = layer.label_rect(x, r)
        rings.append({"frac": frac, "r": r,
                      "text": map_layers.format_range_nm(x.range_nm * frac),
                      "label": {"x": lr.x(), "y": lr.y(),
                                "w": lr.width(), "h": lr.height()},
                      "kept_out": layer.label_kept_out(x, lr)})
    osp = x.to_screen(m._lat, m._lon)
    out = {"options": opts, "w": w, "h": h,
           "nd_active": m.nd_active(),
           "mode": nd.normalize_mode(m.nd_mode),
           "orientation": m.view_orientation(),
           "anchor_frac": m._anchor_frac(), "range_px": x.range_px,
           "cx": x.cx, "cy": x.cy, "ownship": [osp.x(), osp.y()],
           "rings": rings, "track": m._track,
           "keep_out": [{"x": k.x(), "y": k.y(), "w": k.width(), "h": k.height()}
                        for k in x.label_keep_out],
           "status_chip": m._status_chip_text(False),
           "track_old": m._old.get("TRACKM", True),
           "enabled_layers": None,
           "geometry": None, "ops": []}
    if m.nd_active():
        g = nd.geometry(m.nd_mode, float(w), float(h), x.font_family)
        vg = nd.vdev_geometry(float(w), float(h))
        out["geometry"] = g
        out["arc_reach"] = nd.arc_reach(float(w), float(h))
        out["vdev"] = vg
        out["status_chip_height"] = nd.status_chip_height(float(w))
        out["polished"] = nd.polished(m)
        out["corner_block_bottom"] = nd.corner_block_bottom(float(w), float(h))
        out["block_widths"] = list(nd.block_widths(x.font_family, g["fs"]))
        out["vdev_pointer_y"] = {str(p): nd.vdev_pointer_y(vg, p)
                                 for p in (-1.0, -0.4, 0.0, 0.4, 1.0)}
        out["ticks"] = list(nd.tick_bearings(0.0, g["half"]))
        m._build_layers()
        out["enabled_layers"] = sorted(nd.enabled_layers(m))
        rec = _Recorder()
        nd.paint(m, rec, x, _none, now=NOW)
        out["ops"] = rec.ops
    return out


def run_read(name, table):
    from pyefis.instruments.map import nd

    def read(key):
        return table.get(key)
    tl, arrow = nd.top_left(read)
    col = lambda lines: [[[t, c.name()] for t, c in line] for line in lines]  # noqa: E731
    vd = nd.vdev(read)
    return {"name": name,
            "table": {k: list(v) for k, v in table.items()},
            "top_left": col(tl), "arrow": arrow,
            "top_right": col(nd.top_right(read, now=NOW)),
            "vdev": vd}


def run_paint_data(name, table, w, h, mode):
    """nd.paint with ND data, for the corner blocks' placement and colours."""
    from pyefis.instruments.map import nd
    m = _map({"nd_mode": mode}, w, h)
    x = m._transform()
    m._build_layers()
    rec = _Recorder()
    nd.paint(m, rec, x, lambda k: table.get(k), now=NOW)
    return {"name": name, "mode": mode, "w": w, "h": h,
            "table": {k: list(v) for k, v in table.items()}, "ops": rec.ops}


def render(path, w, h, mode):
    """One frame of the real widget, no ND data, 10 NM, in *mode*."""
    m = _map({"nd_mode": mode}, w, h)
    m._nd_read = _none
    m.show()
    app = QApplication.instance()
    for _ in range(3):
        m.repaint()
        app.processEvents()
    m.grab().save(path)


def _intersects(a, b):
    from PyQt6.QtCore import QRectF
    return QRectF(*a).intersects(QRectF(*b))


def _round(v):
    if isinstance(v, float):
        r = round(v, 6)
        return int(r) if r == int(r) and abs(r) < 1e15 else r
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    return v


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
    _wrap_backing()
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], int(argv[3]), int(argv[4]), argv[5])
    else:
        out = {"generator": "configurator/tools/nd_mode_oracle.py",
               "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
               "now": NOW,
               "paint": [run_paint(*c) for c in PAINT_CASES],
               "rect_intersects": [[list(a), list(b), _intersects(a, b)]
                                   for a, b in RECT_PAIRS],
               "read": [run_read(n, t) for n, t in READ_CASES],
               "paint_data": [run_paint_data(n, t, w, h, mode)
                              for n, t in READ_CASES
                              if n in ("approach", "gsflag", "plan_stale", "vectors")
                              for w, h in ((800, 480), (650, 1000))
                              for mode in ("expanded", "ctr")]}
        # Floats to 1e-6 px (the test's tolerance is 1e-5), one case per line:
        # compact, and a regenerated fixture diffs by case.
        out = _round(out)
        sys.stdout.write("{\n")
        keys = list(out)
        for i, k in enumerate(keys):
            v = out[k]
            sys.stdout.write(" %s: " % json.dumps(k))
            if isinstance(v, list):
                sys.stdout.write("[\n" + ",\n".join(
                    "  " + json.dumps(c, separators=(",", ":")) for c in v) + "\n ]")
            else:
                sys.stdout.write(json.dumps(v))
            sys.stdout.write(",\n" if i < len(keys) - 1 else "\n")
        sys.stdout.write("}\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
