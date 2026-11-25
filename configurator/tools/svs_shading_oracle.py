# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Bill Mallard
#
# AER-6498 (SVSSHADE3): dump the REAL pyEfis shared terrain shading rule
# (src/pyefis/instruments/ai/terrain_shading.py, SVSSHADE2, pyEfis #563) as
# the fixture the editor's SVS twin (SVS_HYPSO_STOPS, _svsHypso, _svsBand,
# _svsGate, _svsShading, _svsHazardLight, _svsLight in public/editor.html) is
# tested against.
#
# terrain_shading.py is plain numpy, so it is imported straight from the
# checkout; the SVS GL shader generates its GLSL constants and band rule from
# the same module. The vertex lighting constants (AMBIENT, DIFFUSE, SLOPE_EXAG
# and the sun direction) live only in svs_gl.py's GLSL source, so they are
# read out of that file's text rather than re-typed here. Nothing is copied.
#
#   PYEFIS=/path/to/pyEfis   # billmallard/pyEfis dev with #563
#   python3 tools/svs_shading_oracle.py $PYEFIS > test/fixtures/svs_shading_pyefis.json

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

PYEFIS = Path(sys.argv[1])
AI = PYEFIS / "src" / "pyefis" / "instruments" / "ai"
spec = importlib.util.spec_from_file_location("terrain_shading", AI / "terrain_shading.py")
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

gl = (AI / "svs_gl.py").read_text()


def glsl_float(name):
    m = re.search(r"const float %s\s*=\s*([-0-9.]+);" % name, gl)
    if not m:
        raise SystemExit("svs_gl.py: no const float %s" % name)
    return float(m.group(1))


sun = re.search(r"vec3 sun_dir = normalize\(vec3\(([^)]*)\)\);", gl)
if not sun:
    raise SystemExit("svs_gl.py: sun_dir not found")
# The fragment shader must still take the band rule and the floor from the
# module, and the vertex shader must still light as AMBIENT + DIFFUSE * diff;
# otherwise this fixture no longer describes what the glass paints.
for needle in ("int terrain_band(float clr, float elev)",
               "float hazard_light(int band, float lit)",
               "base = hypso(elev_ft);",
               "lit = hazard_light(band, lit);",
               "v_intensity = AMBIENT + DIFFUSE * diff;",
               "float diff = clamp(dot(normal, sun_dir), 0.0, 1.0);",
               "vec3 normal = vec3(-dE / mag, -dN / mag, 1.0 / mag);"):
    if needle not in gl:
        raise SystemExit("svs_gl.py changed: %r not found" % needle)

rev = subprocess.run(["git", "-C", str(PYEFIS), "rev-parse", "HEAD"],
                     capture_output=True, text=True).stdout.strip()

# hypsometric base: a sweep past both end stops, plus each stop and its
# neighbours (the interpolation edges)
elevs = sorted(set([float(e) for e in range(-2500, 16001, 125)]
                   + [s[0] + d for s in ts.HYPSO_STOPS for d in (-0.5, 0.0, 0.5)]))
r, g, b = ts.hypso_rgb(np.array(elevs))
hypso = [[e, float(r[i]), float(g[i]), float(b[i])] for i, e in enumerate(elevs)]

# band rule: clearances around 0 and both thresholds, elevations either side
# of a gate, the stock thresholds and two custom pairs (one inverted)
bands = []
clrs = [-1000.0, -0.5, 0.0, 0.5, 250.0, 499.5, 500.0, 500.5, 750.0,
        999.5, 1000.0, 1000.5, 2000.0, 1e5]
for green, yellow in ((ts.CLEARANCE_GREEN_FT, ts.CLEARANCE_YELLOW_FT),
                      (2000.0, 300.0), (400.0, 800.0)):
    for gate in (None, 413.5, ts.NO_FIELD_ELEV_GATE_FT):
        for clr in clrs:
            for elev in (0.0, 413.0, 413.5, 414.0, 9000.0):
                bnd = int(ts.band(clr, elev, gate, green, yellow))
                bands.append([clr, elev, gate, green, yellow, bnd])

gates = []
for near in (False, True):
    for fe in (None, 0.0, 13.5, 7837.9):
        for agl in (ts.AIRPORT_GATE_AGL_FT, 0.0, 1000.0):
            gates.append([near, fe, agl, ts.collapse_gate_ft(near, fe, agl)])

modes = [[v, ts.shading_mode(v)] for v in
         (None, "", "relief", "clearance", "Relief", "CLEARANCE", "hypso", "0")]

lights = []
for bnd in (ts.BAND_NONE, ts.BAND_CAUTION, ts.BAND_WARNING, ts.BAND_CONFLICT):
    for lit in (0.35, 0.5, 0.69, 0.7, 0.71, 0.9, 1.0):
        lights.append([bnd, lit, float(ts.hazard_light(np.array([bnd]), np.array([lit]))[0])])

json.dump({
    "generator": "configurator/tools/svs_shading_oracle.py",
    "pyefis_rev": rev,
    "constants": {
        "HYPSO_STOPS": [list(s) for s in ts.HYPSO_STOPS],
        "PALETTES": {k: [list(c) for c in v] for k, v in ts.PALETTES.items()},
        "BAND": {"NONE": ts.BAND_NONE, "CAUTION": ts.BAND_CAUTION,
                 "WARNING": ts.BAND_WARNING, "CONFLICT": ts.BAND_CONFLICT},
        "HAZARD_LIGHT_FLOOR": ts.HAZARD_LIGHT_FLOOR,
        "CLEARANCE_GREEN_FT": ts.CLEARANCE_GREEN_FT,
        "CLEARANCE_YELLOW_FT": ts.CLEARANCE_YELLOW_FT,
        "AIRPORT_GATE_AGL_FT": ts.AIRPORT_GATE_AGL_FT,
        "NO_FIELD_ELEV_GATE_FT": ts.NO_FIELD_ELEV_GATE_FT,
        "DEFAULT_SHADING": ts.DEFAULT_SHADING,
        "AMBIENT": glsl_float("AMBIENT"),
        "DIFFUSE": glsl_float("DIFFUSE"),
        "SLOPE_EXAG": glsl_float("SLOPE_EXAG"),
        "SUN_DIR": [float(x) for x in sun.group(1).split(",")],
    },
    "hypso": hypso,
    "band": bands,
    "gate": gates,
    "shading_mode": modes,
    "hazard_light": lights,
}, sys.stdout, indent=1)
sys.stdout.write("\n")
