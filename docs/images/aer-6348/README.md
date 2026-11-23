# AER-6348: annunciator twin and tile editor

The configurator half of pyEfis's `annunciator` instrument (ANN1, pyEfis
#551, branch `aer-6347/annunciator`, head 0bb0487e), compared with the device.

| File | What it shows |
|---|---|
| `side_by_side.png` | the ANN1 bench block (`kit/edit_cfg.py`) at the bench's 336 x 157 px, in B612 as on the bench and in the default DejaVu Sans Condensed, lit, off and stale. Per row: the real widget rendered offscreen, the editor twin as Chromium draws it, and an overlay (device red, twin cyan, white where they agree). 2x nearest neighbour. Under each row: the bright-pixel span per tile row for both, and how many pixels differ by more than 96 grey levels (caption antialiasing; Chromium and Qt rasterize glyphs differently). |
| `vs_bench_glass.png` | frame 7 of ANN1's Beelink sheet (`docs/images/ann1_annunciator/sheet.png`: all six tiles lit, VS HIGH's local `warning` drawn advisory) beside the device render and the twin of the same state, all 2x. The glass sits on the PFD (75% black over the scene); the renders sit on black. The crop origin in the sheet (825, 1339) is computed from the tile geometry, so it may be a pixel or two off. |
| `editor.png` | the real `editor.html` with the bench block placed on a 1920 x 1080 PFD and selected: the schema's options with their colour tokens, and the top of the tile editor. |
| `tile_editor.png` | the whole tile editor for the bench block, in four columns: a value tile, an enum map (NAVSRC 0 VLOC1, 1 VLOC2, 2 GPS), the annunciate-flag tile offered caution, and the `VS gt 1500` condition tile whose `warning` is listed as "drawn advisory". |
| `editor_preview_states.png` | the block on the editor canvas with "Preview tiles as" set to lit, off and stale. 2x. |

## Regenerate

Fonts: the DejaVu TTFs (dejavu-fonts-ttf 2.37) and Airbus B612 1.008's OTFs in
one dir that `FONTCONFIG_FILE` names for Qt and `FONT_DIR` names for Chromium.

```sh
PYEFIS=/path/to/pyEfis                     # aer-6347/annunciator (#551)
OUT=/tmp/aer6348; mkdir -p $OUT
export PW=/path/to/playwright-core CHROME=/path/to/chrome-headless-shell FONT_DIR=/path/to/fonts
cd configurator

# The fixture, the font table and the colour tokens:
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
  python3 tools/annunciator_oracle.py > test/fixtures/annunciator_pyefis.json
python3 tools/qt_bold_metrics.py test/fixtures/annunciator_pyefis.json   # -> QT_BOLD_METRICS
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src \
  python3 tools/color_tokens_oracle.py > test/fixtures/color_tokens_pyefis.json

# Device and twin halves (B612 bench block; drop font_family for the default):
BENCH='{"rows":2,"cols":3,"font_family":"B612","tiles":[{"label":"AP","key":"APENG","level":"status","flash_on_change":5},{"label":"YD","key":"YDENG","level":"status"},{"label":"ALT CAP","key":"APALTCAP","level":"advisory"},{"label":"NAV","key":"NAVSRC","level":"info","enum":{"0":"VLOC1","1":"VLOC2","2":"GPS"}},{"label":"ALT ALERT","key":"ALTSEL","flag":"annunciate","level":"caution"},{"label":"VS HIGH","key":"VS","when":"VS gt 15","level":"warning"}]}'
for s in lit off stale; do
  OPTIONS="$BENCH" QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 tools/annunciator_oracle.py --render $OUT/dev_$s.png 336 157 $s
  OPTIONS="$BENCH" STATES=$s node tools/annunciator_capture.mjs $OUT/twin_$s.png 336 157
done
python3 tools/annunciator_compare.py $OUT ../docs/images/aer-6348/side_by_side.png lit:lit off:off stale:stale
python3 tools/annunciator_compare.py --glass $PYEFIS/docs/images/ann1_annunciator/sheet.png 825,1339 \
  $OUT/dev_lit.png $OUT/twin_lit.png ../docs/images/aer-6348/vs_bench_glass.png

# The real editor (schema.json from #551, other assets from the dev site):
PYTHONPATH=$PYEFIS/src python3 -c "import json; from pyefis.editor.schema import build_schema; print(json.dumps(build_schema()))" > $OUT/schema.json
SCHEMA=$OUT/schema.json node tools/annunciator_editor_capture.mjs $OUT
```

The `VS HIGH` condition is `VS gt 15` in the oracle's bench block (its seeds
are 20 and 0) and `VS gt 1500` in the editor capture, as on the bench; the
look is the same.
