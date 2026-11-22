#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
"""AER-6304 (AFC5 twin of pyEfis#519): render the REAL pyEfis attitude
indicator with a flight director command on the bus and dump the bars it drew.

Each config builds an AI, applies its options through screenbuilder's own
apply_options (the path a panel YAML takes), seeds PITCH/ROLL and the FD keys
in the mock FIX database and grabs the widget. While it paints, the module's
QPainter is a recording subclass, so the dump is what paintEvent actually drew
(fd_style gating, the off style, the unusual-attitude declutter included), not
a reading of _draw_flight_director's source:

- `<name>.png`, the widget grab;
- `ai_flight_director_oracle.json`: per config, every magenta (#ff00ff)
  rect/polygon in widget px after the painter's transform, with its pen width
  and colour. An empty list means nothing was drawn.

`svs: true` configs force _svs_conformal() on, so the pitch error goes
through the terrain camera's tangent as it does over SVS (there is no GL in
an offscreen render; nothing else of SVS is needed by the bars).

    QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis \\
      python3 tools/ai_flight_director_oracle.py OUTDIR
"""

import json
import os
import subprocess
import sys

PYEFIS = os.environ["PYEFIS"]
sys.path[:0] = [os.path.join(PYEFIS, "src"), PYEFIS]

import tests.mock_db.client  # noqa: E402
import tests.mock_db.scheduler  # noqa: E402

sys.modules["pyavtools.fix.client"] = tests.mock_db.client
sys.modules["pyavtools.scheduler"] = tests.mock_db.scheduler

import pyavtools.fix as fix  # noqa: E402
from PyQt6.QtCore import QPointF  # noqa: E402
from PyQt6.QtGui import QPainter, QPolygonF  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

FD = "#ff00ff"

# name: (w, h, options, PITCH, ROLL, FDPITCH, FDROLL, svs). The first four
# per style are pyEfis docs/aer-6052-evidence/styles.png's columns at its
# 400x300; "both" is the editor's preview sample (4 up, 25 left of attitude).
SHEET = (("satisfied", 2.0, 0.0, 2.0, 0.0), ("climb", 0.0, 0.0, 6.0, 0.0),
         ("turn", 0.0, 0.0, 0.0, 20.0), ("both", 0.0, 10.0, 4.0, -15.0))
CONFIGS = {}
for style in ("single_cue", "cross_pointer"):
    for cap, p, r, fp, fr in SHEET:
        CONFIGS["%s_%s" % (style, cap)] = (400, 300, dict(fd_style=style), p, r, fp, fr, False)
    CONFIGS.update({
        # thickness and gap floors (3 px, 2 px) bind on a small widget
        "%s_small" % style: (180, 120, dict(fd_style=style), 0.0, 10.0, 4.0, -15.0, False),
        # tall, symbol_scale and a raised horizon
        "%s_tall_scaled" % style: (300, 420, dict(fd_style=style, symbol_scale=1.4,
                                                  horizon_position=62), 1.0, -5.0, 3.0, 12.0, False),
        "%s_wide_fov30" % style: (640, 300, dict(fd_style=style, pitchDegreesShown=30,
                                                 symbol_scale=0.7), 0.0, 0.0, -5.0, 8.0, False),
        # pinned at the glass edge; roll bar past full scale; bank wraps at 180
        "%s_pinned" % style: (400, 300, dict(fd_style=style), -5.0, 0.0, 25.0, 45.0, False),
        "%s_pinned_low_raised" % style: (400, 300, dict(fd_style=style, horizon_position=65),
                                         10.0, 0.0, -30.0, -10.0, False),
        "%s_wrap" % style: (400, 300, dict(fd_style=style), 0.0, -40.0, 2.0, 170.0, False),
        # SVS: tangent pitch scale
        "%s_svs" % style: (480, 270, dict(fd_style=style), 0.0, 0.0, 12.0, -10.0, True),
    })
CONFIGS.update({
    "off": (400, 300, dict(fd_style="off"), 0.0, 10.0, 4.0, -15.0, False),
    "unknown_style": (400, 300, dict(fd_style="Bogus"), 0.0, 10.0, 4.0, -15.0, False),
    "default_style": (400, 300, dict(), 0.0, 10.0, 4.0, -15.0, False),
    "upper_case": (400, 300, dict(fd_style="CROSS_POINTER"), 0.0, 10.0, 4.0, -15.0, False),
    # the unusual-attitude declutter hides the bars
    "declutter_bank": (400, 300, dict(fd_style="single_cue"), 0.0, 70.0, 0.0, 30.0, False),
})


class Rec(QPainter):
    """Records magenta rects/polygons in widget px, after the transform."""

    ops = []

    def _rec(self, pts):
        if self.brush().color().name() != FD:
            return
        t = self.transform()
        pen = self.pen()
        Rec.ops.append({"pts": [[q.x(), q.y()] for q in (t.map(QPointF(p)) for p in pts)],
                        "pen_w": pen.widthF(), "pen": pen.color().name(),
                        "join": pen.joinStyle().name})

    def drawRect(self, rect, *a):
        if not a and hasattr(rect, "topLeft"):
            self._rec([rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()])
        return super().drawRect(rect, *a)

    def drawPolygon(self, poly, *a):
        if not a and isinstance(poly, QPolygonF):
            self._rec(list(poly))
        return super().drawPolygon(poly, *a)


def seed(values):
    # Every flag first: the widget drops a PITCH/ROLL value that arrives while
    # any attitude item is still failed, and a freshly defined item starts
    # failed. (pyEfis docs/aer-6052-evidence/render.py sets the value first,
    # so its first frame, "satisfied", was drawn at pitch 0, not 2.)
    for key, _ in values:
        item = fix.db.get_item(key)
        item.old = item.bad = item.fail = False
    for key, value in values:
        fix.db.set_value(key, value)


def render(app, cfg):
    from pyefis.instruments.ai import AI, ai_widget
    from pyefis.screens.screenbuilder_options import apply_options

    w, h, opts, pitch, roll, fdp, fdr, svs = cfg
    ai = AI()

    class Screen:
        instruments = [ai]
        encoder_list = []

    apply_options(Screen(), 0, {"type": "atitude_indicator", "options": dict(opts)})
    if svs:
        ai._svs_conformal = lambda: True
    ai.resize(w, h)
    ai.show()
    app.processEvents()
    seed((("ALAT", 0.0), ("TAS", 110.0), ("FDENG", True), ("PITCH", pitch),
          ("ROLL", roll), ("FDPITCH", fdp), ("FDROLL", fdr)))
    ai.redraw()
    app.processEvents()
    assert (ai._pitchAngle, ai._rollAngle) == (pitch, roll), "pose not applied"
    real = ai_widget.QPainter
    ai_widget.QPainter = Rec
    Rec.ops = []
    try:
        img = ai.grab().toImage()
    finally:
        ai_widget.QPainter = real
    out = {"w": w, "h": h, "options": opts, "pitch": pitch, "roll": roll,
           "fd_pitch": fdp, "fd_roll": fdr, "svs": svs,
           "pitchDegreesShown": ai.pitchDegreesShown, "ops": list(Rec.ops)}
    ai.close()
    return img, out


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    fix.initialize({"main": {"FixServer": "localhost", "FixPort": "3490"}})
    for key, dtype, lo, hi in (("PITCH", "float", -90, 90), ("ROLL", "float", -180, 180),
                               ("ALAT", "float", -1, 1), ("TAS", "float", 0, 500),
                               ("FDENG", "bool", 0, 1), ("FDPITCH", "float", -90, 90),
                               ("FDROLL", "float", -180, 180)):
        fix.db.define_item(key, key, dtype, lo, hi, "", 1000, "")
    commit = subprocess.run(["git", "-C", PYEFIS, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dump = {"pyefis_commit": commit, "configs": {}}
    for name, cfg in CONFIGS.items():
        img, out = render(app, cfg)
        img.save(os.path.join(outdir, "%s.png" % name))
        dump["configs"][name] = out
    with open(os.path.join(outdir, "ai_flight_director_oracle.json"), "w") as f:
        json.dump(dump, f, indent=1, sort_keys=True)
        f.write("\n")
    print("%d configs -> %s" % (len(CONFIGS), outdir))


if __name__ == "__main__":
    main(sys.argv[1])
