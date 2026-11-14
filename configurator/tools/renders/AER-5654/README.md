# AER-5654: the DATA flag twin, amber

The editor's `data_annunciation` twin (`dataFlagGeom` / `dataFlagSvg` in
`public/editor.html`) beside the real pyEfis widget
(`data_status.DataAnnunciation`) in its amber state.

- `compare_x4.png`: one row per stock boot-screen flag size. A 1920 x 1080
  screen gives 173 x 49, 1280 x 800 gives 115 x 36, and 1024 x 600 gives
  92 x 27. Left to right: the device render, the twin, and an overlay (red is
  device only, cyan is twin only). The image is 4x nearest neighbour.
- `pyefis_WxH.png` / `twin_WxH.png`: the 1:1 halves, both over black.
- `editor_before_after_zoom3x.png`: a flag on the canvas, top left of a test
  panel, 3x nearest neighbour. Left is without the twin: the palette thumbnail,
  a black box (`docs/images/aer-5610/existing-panel_flag_zoom3x.png` at
  aa5a827). Right is with the twin: amber, no fill. Both were taken with
  AER-5610's capture tool, which placed the flag automatically. makerplane-data#211
  reverted that, so a builder now places the flag from the palette, and the
  canvas draws it the same way either way.
- `editor_existing-panel.png`: the whole editor for that case.

Measured with `ImageChops.difference` on luminance: 0 of 4140 pixels differ by
more than 64 at 115 x 36. At 173 x 49 the count is 371 of 8477, and at 92 x 27
it is 204 of 2484. All of those are on the glyph edges, because Qt places
hinted, whole-pixel glyph advances and the browser does not. The outline is the
same in all three.

## Regenerate

From `configurator/`, with PyQt6, a pyEfis `dev` checkout, a headless Chromium
and the DejaVu 2.37 TTFs:

```bash
export PYEFIS=/path/to/pyEfis QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools
export PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell FONT_DIR=/path/to/dejavu/ttf
O=tools/renders/AER-5654
for s in "173 49" "115 36" "92 27"; do set -- $s
  python3 tools/data_flag_oracle.py --render $O/pyefis_${1}x${2}.png $1 $2
  node tools/data_flag_twin_capture.mjs $O/twin_${1}x${2}.png $1 $2
done
python3 tools/data_flag_compare.py $O/compare_x4.png 4 \
  $O/pyefis_173x49.png $O/twin_173x49.png $O/pyefis_115x36.png $O/twin_115x36.png \
  $O/pyefis_92x27.png $O/twin_92x27.png
# editor capture: tools/data_flag_capture.mjs at aa5a827 (removed by #211), then
# crop OUT/existing-panel.png (331,150,571,230), 3x
```

Fixture for the test (`test/data_flag_twin.test.mjs`):

```bash
python3 tools/data_flag_oracle.py > test/fixtures/data_flag_pyefis.json
```
