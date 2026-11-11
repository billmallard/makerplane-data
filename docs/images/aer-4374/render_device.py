"""Device half of the AER-4374 side-by-side: pyEfis's own
docs/aer-4326-evidence/render_font_percent.py (pyEfis #356, head afeabee),
cut to the tab_section (top, left) and button scenes, with the tab_section
values set to unset / 0.2 / 150 to match tools/font_size_proto.mjs.

Original docstring follows.

Render the six widgets at several Font size (font_percent) values, one
strip per widget, for the AER-4326 evidence (pyEfis #223, #229-#233).

Each panel is built through the real Screen path (YAML options -> builder ->
apply_options), the same path tests/screens/test_font_percent_widgets.py
uses, and grabbed offscreen. Usage, from a pyEfis checkout:

    QT_QPA_PLATFORM=offscreen PYTHONPATH=src python \
        <this file> <out_dir>    (cwd = the pyEfis checkout)
"""
import json
import os
import sys

_REPO = os.getcwd()
sys.path[:0] = [_REPO, os.path.join(_REPO, "src")]
os.chdir(_REPO)   # listbox list files are read relative to the repo

import tools.render_instrument as ri  # noqa: E402  (patches pyavtools first)
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

CHECKLIST = {"checklists": [{
    "title": "Before Takeoff", "category": "normal",
    "sections": [
        {"title": "Engine", "items": [
            {"text": "Mixture", "response": "RICH"},
            {"text": "Magnetos", "response": "CHECK"},
            {"text": "Carb heat", "response": "CHECK"}]},
        {"title": "Controls", "items": [
            {"text": "Flight controls", "response": "FREE"},
            {"text": "Trim", "response": "TAKEOFF"},
            {"text": "Flaps", "response": "SET"}]}]}]}

# (type, options, tabs, panel w, panel h, config_path, values)
SCENES = [
    ("tab_section", {}, [{"label": "Engine", "instruments": []},
                         {"label": "Nav", "instruments": []},
                         {"label": "Fuel", "instruments": []}],
     320, 160, "", [None, 0.2, 150]),
    ("tab_section", {"tab_position": "left"},
     [{"label": "Engine", "instruments": []},
      {"label": "Nav", "instruments": []}],
     320, 160, "", [None, 0.2, 150]),
    ("button", {"dbkey": "TSBTN{id}0", "text": "Units"}, None,
     160, 80, "", [None, 0.5, 0.8]),
]


def _panel(qtbot_widgets, inst_type, options, tabs, w, h, config_path, value):
    from tests.screens.test_screenbuilder import (
        _TestParent, _config_with_instruments)
    from pyefis.screens.screenbuilder import Screen
    opts = dict(options)
    if value is not None:
        opts["font_percent"] = value
    inst = {"type": inst_type, "row": 0, "column": 0,
            "span": {"rows": 10, "columns": 10}, "options": opts}
    if tabs is not None:
        inst["tabs"] = [{"layout": {"rows": 1, "columns": 1}, **t} for t in tabs]
    parent = _TestParent(_config_with_instruments([inst]),
                         config_path=config_path)
    screen = Screen(parent)
    screen.screenName = "TEST"
    screen.resize(w, h)
    screen.init_screen()
    parent.resize(w, h)
    parent.setStyleSheet("background: #202020;")
    parent.show()
    for _ in range(5):
        QApplication.processEvents()
    qtbot_widgets.append(parent)
    return parent.grab()


def main(out_dir):
    app = QApplication.instance() or QApplication([])
    ri._load_fonts()
    ri._setup_fix()
    import pyefis.hmi as hmi
    hmi.initialize({})
    os.makedirs(out_dir, exist_ok=True)
    keep, index = [], []
    label_h, gap = 22, 8
    for n, (t, opts, tabs, w, h, cp, values) in enumerate(SCENES):
        shots = [_panel(keep, t, opts, tabs, w, h, cp, v) for v in values]
        strip = QPixmap(len(shots) * (w + gap) - gap, h + label_h)
        strip.fill(QColor("#ffffff"))
        p = QPainter(strip)
        f = QFont("DejaVu Sans")
        f.setPixelSize(14)
        p.setFont(f)
        for i, (v, pm) in enumerate(zip(values, shots)):
            x = i * (w + gap)
            p.drawText(x, 0, w, label_h, int(Qt.AlignmentFlag.AlignCenter),
                       "unset (today)" if v is None else f"font_percent: {v}")
            p.drawPixmap(x, label_h, pm)
        p.end()
        name = f"{n}-{t}{'-' + opts['tab_position'] if 'tab_position' in opts else ''}.png"
        strip.save(os.path.join(out_dir, name))
        index.append({"file": name, "type": t, "values": values})
    print(json.dumps(index))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/aer-4326-evidence")
