<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-5396: moving-map ND EXPANDED polish twin evidence (G11c, pyEfis #443)

A full-screen `moving_map` at 10 NM with `nd_mode: expanded` and
`nd_mode: ctr`, at 800x480 and 650x1000, against pyEfis `dev` @
`1541ffe8830c9bfdc88ff5a3303ced20417893cc` (G11c `51ec4547`, pyEfis #443).

| File | What it shows |
|---|---|
| `compare_<mode>_<w>x<h>.png` | Left: the pyEfis `MovingMap` widget, no map data, no ND data. Middle: the editor twin. Right: an overlay where grey means they agree, red means device only, cyan means twin only |
| `pyefis_*.png`, `twin_*.png` | The two halves on their own |

What G11c changes, and where to look:

- `compare_expanded_650x1000.png`: the portrait arc. r 692.1 px, apex at
  y 127.9, +/-22.96 deg (was r 420.0, apex 400.0, +/-40 deg).
- Both EXPANDED frames: the dark backing panels (the chip plus the top-left
  block as one shape, and the option list), and the TRK box filled opaque.
- Both EXPANDED frames: the full-range ring's "10" label is gone. It sat
  under the TRK box, so it is kept out. The half-range "5" stays.
- `compare_ctr_*.png`: CTR MAP, unchanged. No panels, an unfilled box, and
  both labels.

"No data" is the editor's view: position and track good (track 000, the
twin's pose), and every ND FIX key absent. The `moving_map` schema has no
category-3 `preview` values, so the GS/TAS/wind, active-waypoint and
vertical-deviation blocks are not drawn, and their panels are not either. The
top-left panel still draws, because it backs the range chip.

## Regenerate

Ran in the agent sandbox with PyQt6, DejaVu 2.37 TTFs named by
`FONTCONFIG_FILE`, and Playwright's headless Chromium.

```bash
cd configurator
O=tools/renders/AER-5396
PYEFIS=/path/to/pyEfis    # @ dev 1541ffe8, PyQt6 installed
export QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools:$PYEFIS

# 1. Editor twin crops: the unmodified public/editor.html in headless Chromium;
#    /api is mocked and /assets/editor is proxied to pyefis-dev.aerocommons.org.
PW=.../node_modules/playwright-core CHROME=.../chrome-headless-shell \
  node tools/nd_mode_capture.mjs $O

# 2. Device frames and the compositions.
for s in 800x480 650x1000; do for m in expanded ctr; do
  python3 tools/nd_mode_oracle.py --render $O/pyefis_${m}_$s.png ${s%x*} ${s#*x} $m
  python3 tools/range_rings_compare.py $O/pyefis_${m}_$s.png $O/twin_${m}_$s.png $O/compare_${m}_$s.png
done; done
```

The test fixture `test/fixtures/nd_mode_pyefis.json` is step 2's oracle without
`--render` (see the header of `tools/nd_mode_oracle.py`).
