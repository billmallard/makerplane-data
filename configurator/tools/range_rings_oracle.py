# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-4461: dump what the REAL pyEfis RangeRingsLayer (instruments/map/layers,
# G13 #136) computes for a table of option sets, as the fixture the editor's
# range-ring twin (rangeRingGeom in public/editor.html) is tested against.
# The pyEfis source is imported by path from a checkout, never copied here.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis @ dev, PyQt6 installed
#   PYTHONPATH=$PYEFIS/src python3 tools/range_rings_oracle.py \
#       > test/fixtures/range_rings_pyefis.json

import json
import subprocess
import sys
from types import SimpleNamespace

from pyefis.instruments.map import MapTransform
from pyefis.instruments.map import layers as map_layers
from pyefis.instruments.map.layers import RangeRingsLayer

# (options, widget w, widget h). Options are raw YAML values, as the editor
# saves them; an absent key is the widget default.
CASES = [
    ({}, 800, 480),
    ({}, 300, 600),
    ({"range_ring_fractions": "0.5,1"}, 800, 480),
    ({"range_ring_fractions": "0.25,0.5,0.75,1"}, 800, 480),
    ({"range_ring_fractions": "1, 0.5,0.5 ,"}, 640, 480),
    ({"range_ring_fractions": "abc,1"}, 800, 480),
    ({"range_ring_fractions": ""}, 800, 480),
    ({"range_ring_fractions": "3,0.5,2"}, 800, 480),
    ({"range_ring_fractions": "0,-1"}, 800, 480),
    ({"range_ring_fractions": 0.5}, 800, 480),
    ({"range_ring_fractions": None}, 800, 480),
    ({"range_ring_fractions": "1e-1,.3"}, 800, 480),
    ({"range_ring_label_angle": 90}, 800, 480),
    ({"range_ring_label_angle": 450}, 800, 480),
    ({"range_ring_label_angle": -90}, 800, 480),
    ({"range_ring_label_angle": "135"}, 800, 480),
    ({"range_ring_label_angle": "x"}, 800, 480),
    ({"range_ring_labels": False}, 800, 480),
    ({"range_ring_color": "#ff0000"}, 800, 480),
    ({"range_ring_color": "red"}, 800, 480),
    ({"range_ring_color": "#80ff8000"}, 800, 480),
    ({"range_ring_color": "#0f0"}, 800, 480),
    ({"range_ring_color": "bogus!"}, 800, 480),
    ({"range_ring_color": ""}, 800, 480),
    ({"range_ring_opacity": 0}, 800, 480),
    ({"range_ring_opacity": 100}, 800, 480),
    ({"range_ring_opacity": 150}, 800, 480),
    ({"range_ring_opacity": "40"}, 800, 480),
    ({"range_ring_opacity": 33}, 800, 480),
    ({"range_ring_width": 2.5}, 800, 480),
    ({"range_ring_width": -3}, 800, 480),
    ({"range_ring_width": 0}, 300, 600),
    ({"range_ring_label_size": 14}, 800, 480),
    ({"range_ring_label_size": 12.7}, 800, 480),
    ({"range_ring_label_size": 0}, 200, 200),
    ({"ownship_position": 0}, 800, 480),
    ({"ownship_position": 100}, 800, 480),
    ({"ownship_position": 25, "range_nm": 1,
      "range_ring_fractions": "0.25,0.5,1.25,1.75"}, 1024, 600),
    ({"range_nm": 12.5, "range_ring_fractions": "0.5,1,2"}, 800, 480),
    ({"range_nm": 2.5, "range_ring_fractions": "0.1,0.5,1"}, 800, 480),
    ({"range_nm": 25}, 800, 480),
    ({"range_nm": 7, "range_ring_fractions": "0.33,0.66,1"}, 800, 480),
    ({"range_ring_fractions": "0.2,0.4,0.6,0.8,1", "range_ring_label_angle": 225,
      "range_ring_color": "#00ffcc", "range_ring_opacity": 80,
      "range_ring_width": 1.5, "range_ring_label_size": 18}, 1024, 600),
]


def run(opts, w, h):
    owner = SimpleNamespace(**opts)
    layer = RangeRingsLayer()
    layer.configure(owner)
    range_nm = float(opts.get("range_nm", 10.0))
    anchor = max(0.0, min(100.0, float(opts.get("ownship_position", 50)))) / 100.0
    x = MapTransform(0.0, 0.0, range_nm, 0.0, w, h, anchor)
    pen = layer.pen_color()
    rings = []
    for frac, r in zip(layer.fractions, layer.ring_radii_px(x)):
        lr = layer.label_rect(x, r)
        rings.append({"frac": frac, "r": r,
                      "text": map_layers.format_range_nm(range_nm * frac),
                      "label": {"x": lr.x(), "y": lr.y(),
                                "w": lr.width(), "h": lr.height()}})
    return {"options": opts, "w": w, "h": h,
            "cx": x.cx, "cy": x.cy, "rings": rings,
            "labels": layer.labels, "color": pen.name(), "alpha": pen.alpha(),
            "strokeW": layer.pen_width(x), "labelPx": layer.label_pixel_size(x),
            "chip": map_layers.format_range_nm(range_nm)}


def main():
    src = sys.modules["pyefis"].__file__
    try:
        rev = subprocess.run(["git", "-C", src.rsplit("/src/", 1)[0], "rev-parse",
                              "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = ""
    json.dump({"generator": "configurator/tools/range_rings_oracle.py",
               "pyefis_rev": rev,
               "cases": [run(o, w, h) for o, w, h in CASES]},
              sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
