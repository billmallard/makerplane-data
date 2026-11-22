# AER-6356: cas_window and cas_marker twins

The configurator twins of pyEfis `cas_window` and `cas_marker` (CAS3, pyEfis
#552), compared with the real widgets.

| File | What it shows |
|---|---|
| `cas_vs_widget_2x.png` | 11 scenes (`configurator/tools/cas_scenes.json`), each shown 2x nearest-neighbour. Left to right: the real widget rendered offscreen (`tools/cas_oracle.py --render`), the editor twin rendered in headless Chromium (`tools/cas_capture.mjs`), and their difference: white where any channel differs by more than 64. The caption gives the share of differing pixels. |

The scenes cover the shipped placements at 1920 x 1080 (window 278 x 196,
marker 192 x 59) and 1024 x 600 (148 x 109, 102 x 33), both flash phases, the
`+k` overflow row, TEXT plus DETAIL, B612, and the marker at each level. No
scene differs in more than 1.14% of its pixels. What is left is glyph-edge
antialiasing, worst on the red row. A whole-pixel shift search on that row
finds (0, 0) as the best fit, so the residue is Qt's and Skia's blending on
saturated red, not a misplaced glyph. The amber row in the same style differs
in 4 pixels.

The same scenes are fixture cases, so `test/cas_twin.test.mjs` pins what each
picture shows.

These are offscreen renders, not glass. No Pi or Beelink capture was made.

## Regenerate

The fonts are the DejaVu TTFs (dejavu-fonts-ttf 2.37) and the Pi 5's B612
OTFs (Airbus B612 1.008, `~/.local/share/fonts` on the bench). They go in one
dir, which `FONTCONFIG_FILE` names for Qt and `FONT_DIR` names for Chromium.

```sh
PYEFIS=/path/to/pyEfis                  # billmallard/pyEfis with #552
OUT=/tmp/aer6356; mkdir -p $OUT
export PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell FONT_DIR=/path/to/fonts
cd configurator

# Device halves (PyQt6, offscreen), one per scene:
python3 -c "import json;[print(s['name'],s['type'],s['w'],s['h'],s['state'],s['t'],json.dumps(s['options'])) for s in json.load(open('tools/cas_scenes.json'))]" |
while read n ty w h st t op; do
  OPTIONS="$op" QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 tools/cas_oracle.py --render $OUT/dev_$n.png $ty $w $h $st $t
done
# Twin halves:
node tools/cas_capture.mjs $OUT
# The sheet:
python3 tools/cas_compare.py $OUT ../docs/images/aer-6356/cas_vs_widget_2x.png 2
```

The fixture the test reads comes from the same oracle and pyEfis checkout:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
  python3 tools/cas_oracle.py > test/fixtures/cas_pyefis.json
```
