# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6348 (ANN2): dump what the REAL pyEfis `annunciator`
# (instruments/annunciator Annunciator, drawing with instruments/tile.py,
# pyEfis #551) paints, as the fixture the editor's twin (annTiles /
# annGeom / annSvg in public/editor.html) is tested against.
#
# Each case builds the widget through pyEfis's builder (apply_preferences
# resolves the font, build_annunciator constructs it) and applies the options
# through apply_options, the device's own path. Every tile is put in one of the
# editor's three preview states through the FIX keys it reads, so the widget's
# own logic decides what it shows:
#
#   lit    flags clear, every key the tile reads at 20 (an enum key at its
#          first mapped value), a `flag` tile's flag set (old/bad/fail after
#          the build, once the tile has seen its key fresh)
#   off    flags clear, every key at 0, the flag clear
#   stale  as off, then every key the tile reads marked old after the build
#          (so the tile has been seen live first)
#
# Flags are cleared before values are set, and the model's applied state is
# recorded beside the paint (`tiles`), so a seeding slip cannot pass as a look.
# Paint calls are recorded by swapping the module's QPainter for a subclass
# that records each call and then paints it; no paint formula is re-typed
# here. The pyEfis source is imported from a checkout, never copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis aer-6347/annunciator (#551)
#   QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
#       python3 tools/annunciator_oracle.py > test/fixtures/annunciator_pyefis.json
#
# The fixture also carries Qt's bold glyph advances per pixel size and the
# kern pairs over printable ASCII (the `metrics` block): the block fits one
# caption size by horizontalAdvance and line height. tools/qt_bold_metrics.py
# turns it into the twin's QT_BOLD_METRICS table.
#
# With --render OUT.png W H STATE it instead saves one frame, the device half
# of the side-by-side; OPTIONS is optional JSON for the instrument's options
# (default: the ANN1 bench block). STATE is lit, off, stale, or a JSON list
# of per-tile states.

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

FAMILIES = ("DejaVu Sans Condensed", "DejaVu Sans", "B612")
#: Qt's advances at this pixel size are the font's own units (2048 per em).
UNITS_PX = 2048
CHARS = "".join(chr(c) for c in range(0x20, 0x7f))
METRIC_PX = range(1, 161)
QUALITY = ("old", "bad", "fail")

# The ANN1 bench block (pyEfis docs/images/ann1_annunciator/kit/edit_cfg.py),
# with NAVSRC's map as hsi/__init__.py has it (0 NAV1, 1 NAV2, 2 GPS).
BENCH = {
    "rows": 2, "cols": 3,
    "tiles": [
        {"label": "AP", "key": "APENG", "level": "status",
         "flash_on_change": 5},
        {"label": "YD", "key": "YDENG", "level": "status"},
        {"label": "ALT CAP", "key": "APALTCAP", "level": "advisory"},
        {"label": "NAV", "key": "NAVSRC", "level": "info",
         "enum": {0: "VLOC1", 1: "VLOC2", 2: "GPS"}},
        {"label": "ALT ALERT", "key": "ALTSEL", "flag": "annunciate",
         "level": "caution"},
        {"label": "VS HIGH", "key": "VS", "when": "VS gt 15",
         "level": "warning"},
    ],
}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _rgba(c):
    return [c.red(), c.green(), c.blue(), c.alpha()]


def _rect(r):
    return {"x": r.x(), "y": r.y(), "w": r.width(), "h": r.height()}


class _RecPainter(QPainter):
    """QPainter that records what the block paints, then paints it."""
    ops = None

    def _pen(self):
        p = self.pen()
        return {"rgba": _rgba(p.color()), "width": p.widthF(),
                "style": p.style().name, "cap": p.capStyle().name,
                "join": p.joinStyle().name,
                "dashes": list(p.dashPattern()) if p.style().name != "SolidLine" else []}

    def _brush(self):
        b = self.brush()
        return {"style": b.style().name, "rgba": _rgba(b.color())}

    def _rec(self, op, **kw):
        _RecPainter.ops.append(dict(op=op, **kw))

    def fillRect(self, rect, color, *a):
        self._rec("fill_rect", rect=_rect(rect), rgba=_rgba(QColor(color)),
                  alpha=QColor(color).alphaF())
        return super().fillRect(rect, color, *a)

    def drawRect(self, rect, *a):
        self._rec("rect", rect=_rect(rect), pen=self._pen(),
                  brush=self._brush(), rx=0, ry=0)
        return super().drawRect(rect, *a)

    def drawRoundedRect(self, rect, rx, ry, *a):
        self._rec("rect", rect=_rect(rect), pen=self._pen(),
                  brush=self._brush(), rx=rx, ry=ry)
        return super().drawRoundedRect(rect, rx, ry, *a)

    def drawText(self, rect, flags, text, *a):
        f = self.font()
        fm = QFontMetricsF(f)
        br = self.boundingRect(rect, int(flags), text)
        self._rec("text", text=text, rect=_rect(rect), flags=int(flags),
                  pen=self._pen(), family=f.family(),
                  pixel_size=f.pixelSize(), bold=f.bold(),
                  laid_out=_rect(br), ascent=fm.ascent(),
                  descent=fm.descent(), height=fm.height(),
                  advances=[fm.horizontalAdvance(t) for t in text.split("\n")])
        return super().drawText(rect, flags, text, *a)


def _keys(tile):
    if not isinstance(tile, dict):
        return []
    out = [str(tile.get("key") or "").strip()]
    ck = tile.get("condition_keys") or []
    if isinstance(ck, str):
        ck = ck.split(",")
    out += [str(k).strip() for k in ck]
    return [k for k in out if k]


def _first_enum(tile):
    e = tile.get("enum")
    if isinstance(e, dict) and e:
        return int(next(iter(e)))
    return None


def _seed(tiles, states):
    """Flags first, then values, as module docstring."""
    keys = []
    for t in tiles:
        for k in _keys(t):
            if k not in keys:
                keys.append(k)
    for k in keys:
        fix.db.define_item(k, k, "int", -100000, 100000, "", 0, "")
        it = fix.db.get_item(k)
        it.old = it.bad = it.fail = it.annunciate = False
    for t, st in zip(tiles, states):
        if not isinstance(t, dict):
            continue
        en = _first_enum(t)
        for k in _keys(t):
            v = en if (en is not None and k == t.get("key")) else (
                20 if st == "lit" else 0)
            fix.db.get_item(k).value = v
        flag = t.get("flag") if isinstance(t.get("flag"), str) else ""
        if st == "lit" and flag == "annunciate" and _keys(t):
            fix.db.get_item(_keys(t)[0]).annunciate = True


#: Every case's widgets stay alive: a later case redefines keys an earlier
#: block is connected to, and a signal into a deleted widget crashes.
_ALIVE = []


def _build(opts, states, w, h):
    from pyefis.screens import screenbuilder_factory as sf
    from pyefis.screens import screenbuilder_options as so
    from pyefis.screens import screenbuilder_preferences as sp
    tiles = opts.get("tiles") or []
    _seed(tiles, states)
    screen = QWidget()
    screen.encoder_list = []
    cfg = {"type": "annunciator", "options": json.loads(json.dumps(opts))}
    prefs = {"gauges": {}, "style": {}, "styles": {}}
    font_percent, font_family = sp.apply_preferences(cfg, prefs)
    block = sf.build_annunciator(screen, cfg, font_percent=font_percent,
                                 font_family=font_family)
    screen.instruments = {0: block}
    _ALIVE.append(screen)
    so.apply_options(screen, 0, cfg)
    block.clock = block.model.clock = Clock(1000.0)
    # A quality flag set before the block saw its key fresh would leave the
    # tile empty (never seen live), so a lit old/bad/fail tile gets its flag
    # after the build, as on the device.
    for t, st in zip(tiles, states):
        flag = t.get("flag") if isinstance(t, dict) else None
        if st == "lit" and flag in QUALITY and _keys(t):
            setattr(fix.db.get_item(_keys(t)[0]), flag, True)
        if st == "stale":
            for k in _keys(t):
                fix.db.get_item(k).old = True
    block.resize(int(w), int(h))
    return screen, block


def _tile(t):
    if t is None:
        return None
    return {"text": t.text, "level": t.level, "boxed": t.boxed,
            "flash": t.flash, "stale": t.stale}


def run(opts, w, h, states):
    from pyefis.instruments import annunciator as ann
    tiles = opts.get("tiles") or []
    if isinstance(states, str):
        states = [states] * len(tiles)
    screen, block = _build(opts, states, w, h)
    ann.QPainter = _RecPainter
    _RecPainter.ops = []
    try:
        block.grab()
    finally:
        ann.QPainter = QPainter
    out = {"options": opts, "w": int(w), "h": int(h), "states": states,
           "family": block.font_family, "font_percent": block.font_percent,
           "grid": list(block.grid()),
           "rects": [None if r is None else _rect(r) for r in block.tile_rects()],
           "tiles": [_tile(t) for t in block.model.tiles()],
           "ops": _RecPainter.ops}
    block._timer.stop()
    return out


def metrics():
    """Qt's bold advances per glyph and pixel size, ascent/descent, and the
    kern pairs over CHARS, per family, read from Qt at UNITS_PX where its
    advances are the font's units (a TrueType kern table and an OTF's GPOS
    pairs alike)."""
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


def _t(label, key, **kw):
    return dict(label=label, key=key, **kw)


# Option sets beyond the bench block. Keys are per case, so no two tiles share
# a key unless the case means them to.
EDGE = [
    # Unusable entries draw nothing; the rest still lay out in their cells.
    {"rows": 2, "tiles": [
        _t("NO KEY", ""), _t("TWO", "E1", when="E1 gt 15", flag="bad"),
        _t("BADFLAG", "E2", flag="stuck"), _t("LIST", "E3", enum=["A", "B"]),
        _t("OK", "E4", level="status"), "not a mapping"]},
    # Levels: unknown and messy spellings, alert asks from every non-alert
    # binding (drawn advisory), the annunciate flag keeping both alert levels.
    {"rows": 2, "cols": 4, "tiles": [
        _t("UNK", "L1", level="purple"), _t("CAPS", "L2", level=" Status "),
        _t("W BARE", "L3", level="warning"), _t("C WHEN", "L4", when="L4 gt 15", level="caution"),
        _t("W OLD", "L5", flag="old", level="warning"), _t("C ENUM", "L6", level="caution", enum={1: "ONE"}),
        _t("W ANN", "L7", flag="annunciate", level="warning"), _t("OFFLVL", "L8", level="off")]},
    # Text: custom on/off text, an empty off text (an empty box), an empty on
    # text, no label (the key), lower case and punctuation, a two-line label.
    {"rows": 2, "cols": 3, "tiles": [
        _t("PUMP", "T1", on_text="PUMP ON", off_text="PUMP OFF", level="status"),
        _t("DOOR", "T2", off_text="", level="advisory"),
        _t("BLANK", "T3", on_text="", level="info"),
        dict(key="NOLABEL", level="status"),
        _t("Trim/Flap 2.5", "T5", level="info"),
        _t("FUEL\nPUMP", "T6", level="status")]},
    # A condition reading other keys, and the quality flags.
    {"rows": 1, "tiles": [
        _t("BOTH", "C1", when="C1 gt 15 and C2 gt 15", condition_keys=["C2"], level="status"),
        _t("OLD", "Q1", flag="old", level="info"),
        _t("BAD", "Q2", flag="bad", level="advisory"),
        _t("FAIL", "Q3", flag="fail", level="status")]},
    # Enum: an unmapped first key is impossible here (seeded to the first
    # mapped value); string keys and a float-looking key.
    {"rows": 1, "tiles": [
        _t("SRC", "N1", level="info", enum={"0": "NAV1", "1": "NAV2", "2": "GPS"}),
        _t("MODE", "N2", level="status", enum={3: "THREE"})]},
]

STYLES = [
    {}, {"gap_px": 0}, {"gap_px": 12}, {"border_width": 1}, {"border_width": 6},
    {"corner_radius": 0}, {"corner_radius": 14}, {"bg_opacity": 0},
    {"bg_opacity": 100}, {"bg_opacity": "40"}, {"bg_opacity": 150},
    {"bg_color": "#102030"}, {"font_percent": 0.5}, {"font_percent": 0.25},
    {"font_percent": 50}, {"font_percent": 0}, {"font_percent": 1.5},
    {"font_family": "DejaVu Sans"}, {"font_family": "B612"},
    {"status_color": "#11aa22", "advisory_color": "#dddddd",
     "info_color": "#2288ff", "off_color": "#777777",
     "caution_color": "#ffcc00", "warning_color": "#cc0000"},
    {"rows": 3, "cols": 2}, {"rows": 1, "cols": 0}, {"rows": 1, "cols": 2},
    {"rows": 2, "cols": 0}, {"rows": 4, "cols": 0},
]

SIZES = [(330, 120), (400, 90), (240, 160), (600, 60), (120, 60), (90, 40),
         (700, 300), (24, 12)]


def cases():
    out = []
    for w, h in SIZES:
        for st in ("lit", "off", "stale"):
            out.append((BENCH, w, h, st))
    for style in STYLES:
        for st in ("lit", "off", "stale"):
            out.append(({**BENCH, **style}, 330, 120, st))
    out.append(({**BENCH, "font_family": "B612"}, 467, 155,
                ["lit", "off", "off", "lit", "lit", "lit"]))
    for opts in EDGE:
        for w, h in ((480, 120), (200, 60)):
            for st in ("lit", "off", "stale"):
                out.append((opts, w, h, st))
    out.append(({"tiles": []}, 330, 120, "lit"))
    out.append(({"rows": 1, "tiles": [_t("VERY LONG CAPTION TEXT", "X1")]},
                300, 40, "lit"))
    return out


def render(path, w, h, states):
    opts = json.loads(os.environ.get("OPTIONS") or "null") or BENCH
    tiles = opts.get("tiles") or []
    states = json.loads(states) if states.startswith("[") else [states] * len(tiles)
    screen, block = _build(opts, states, w, h)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    block.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    block._timer.stop()
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
    fix.initialize({"main": {"FixServer": "localhost", "FixPort": "3490"}})
    if len(argv) > 1 and argv[1] == "--render":
        render(argv[2], int(argv[3]), int(argv[4]), argv[5])
    else:
        from pyefis.editor.schema import build_schema
        schema = build_schema()
        json.dump({"generator": "configurator/tools/annunciator_oracle.py",
                   "pyefis_rev": os.environ.get("PYEFIS_REV") or _rev(),
                   "schema": {"instrument": schema["instruments"]["annunciator"],
                              "annunciator": schema["annunciator"]},
                   "metrics": metrics(),
                   "cases": [run(*c) for c in cases()]},
                  sys.stdout, indent=None, separators=(",", ":"))
        sys.stdout.write("\n")
    sys.stdout.flush()
    # The mock FIX scheduler's timers do not shut down cleanly at exit.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
