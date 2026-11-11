<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-5272: moving-map ND mode twin evidence (G11b, EXPANDED MAP + CTR MAP)

A full-screen `moving_map` at 10 NM with `nd_mode: expanded` and
`nd_mode: ctr`, at 800x480 and 650x1000, against pyEfis
`aer-5262/g11b-ctr-map` @ `f989e06a05b625dca3285afc34f975363db59171`
(pyEfis #441; EXPANDED from #420, already on `dev`).

| File | What it shows |
|---|---|
| `compare_<mode>_<w>x<h>.png` | Left: the pyEfis `MovingMap` widget, no map data, no ND data. Middle: the editor twin. Right: an overlay where grey means they agree, red means device only, cyan means twin only |
| `reference_<mode>_<w>x<h>.png` | Left: pyEfis's own evidence render (`tools/render_nd_evidence.py`, `approach` scene, `standard` style, Beelink packs, from `docs/images/aer5085_nd_expanded/` and `docs/images/aer5262_nd_ctr/`). Right: the twin at the same size |
| `pyefis_*.png`, `twin_*.png` | The two halves on their own |

"No data" in the `compare_*` frames is the editor's view: position and track
good (track 000, the twin's pose), and every ND FIX key absent. The
`moving_map` schema carries no category-3 `preview` values, so the twin leaves
the GS/TAS/wind, active-waypoint and vertical-deviation blocks out, as the
device does when the gateway does not define those keys. The `reference_*`
frames show what the device adds on real data: terrain, airports, fixes, the
data blocks, track 072, and the B612 font that render used.

## Regenerate

Ran in the agent sandbox with PyQt6, DejaVu 2.37 TTFs named by
`FONTCONFIG_FILE`, and Playwright's headless Chromium.

```bash
cd configurator
O=tools/renders/AER-5272
PYEFIS=/path/to/pyEfis    # @ f989e06a, PyQt6 installed
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
python3 tools/nd_reference_compose.py \
  $PYEFIS/docs/images/aer5085_nd_expanded/hw/nd_approach_standard.png \
  $O/twin_expanded_800x480.png $O/reference_expanded_800x480.png "pyEfis ..."
# (hw/ = 800x480, panel/ = 650x1000; CTR: aer5262_nd_ctr/<dir>/nd_ctr_approach_standard.png)
```

The test fixture `test/fixtures/nd_mode_pyefis.json` is step 2's oracle without
`--render` (see the header of `tools/nd_mode_oracle.py`).
