# AER-6304: flight director, editor preview vs pyEfis widget

AFC5 twin of pyEfis#519. The widget renders come from pyEfis `dev` @
`d980118` (#519 merged). The editor renders come from this branch's
`configurator/public/editor.html`, drawn by headless Chromium.

- `ai_fd_sheet.png`: the four commands in pyEfis
  `docs/aer-6052-evidence/styles.png`, at its 400x300 size, with
  pitchDegreesShown 50. There are three rows per style. The top row is
  styles.png's own cell, as committed. The middle row is the widget,
  re-rendered by `tools/ai_flight_director_oracle.py`. The bottom row is the
  editor's `attitudeSVG()`, drawn in the real editor page at the same pose and
  command.
- `ai_fd_zoom3x.png`: the "both" command, which is the editor's preview
  sample. The crop is around the aircraft symbol, enlarged 3x
  nearest-neighbour, with the widget on the left and the editor on the right.
- `ai_fd_preview.png`: what the editor shows for single_cue, cross_pointer
  and off, beside the option form's "Flight director style" field (with the
  list open). Below that is the virtual_vfr (SVS) preview in both styles.

The bars match. Outside the bars, the twin's ladder, symbol and bank scale
are thinner than the widget's, as they were before this change.

## styles.png's "satisfied" cell is drawn at pitch 0

styles.png captions its first column "pitch 2, roll 0, command 2/0". Its
generator (`docs/aer-6052-evidence/render.py`) writes PITCH before it clears
the item's fail flag. The widget drops an attitude value that arrives while
any attitude item is failed, and a freshly defined item starts failed. So
that cell was drawn at pitch 0 with a command of 2, and its cue sits 2 deg
above the symbol, not on it. The oracle clears every flag first and asserts
that the pose was applied, so its re-render, the middle row, is the real
satisfied picture. The editor matches the middle row. In the other seven
cells, the FD pixels of styles.png and the oracle's re-render are
identical: the magenta masks are equal. About 2,100 other pixels per cell
differ by more than 40 levels, all inside x 118-282 where the ladder
numerals are. That fits a different DejaVu build on this host; I did not
trace it further.

## Measured overlap (magenta FD fill, widget vs editor, widget px)

| style | satisfied | climb | turn | both |
|---|---|---|---|---|
| single_cue | IoU 0.967 | 0.963 | 0.965 | 0.951 |
| cross_pointer | 0.992 | 0.992 | 0.803 | 1.000 |

The exact check is `test/ai_flight_director_twin.test.mjs`. On 27 configs it
requires every vertex of the editor's bars to be within 0.01 px of what the
widget's paint drew, and the outline width to match. The pixel overlap above
is coarser, and the two low cells have known causes:

- **cross_pointer turn (0.803):** every differing pixel is in one column,
  x=241, along the roll bar's left edge. The edge is at 240.57 px. The widget
  draws without antialiasing and snaps it, while the editor capture is
  downscaled from 2.36x and leaves that column blended.
- **single_cue both (0.951):** the widget's thick yellow symbol bar covers
  part of the cue's lower-left arm. The twin's thinner symbol bar covers less
  of it.

## Regenerate

```bash
# pyEfis checkout at dev with PyQt6 importable; Chromium headless shell and
# playwright-core for the editor half.
export QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis
cd configurator
python3 tools/ai_flight_director_oracle.py /tmp/fd          # widget grabs + JSON
cp /tmp/fd/ai_flight_director_oracle.json test/fixtures/ai_flight_director_pyefis.json
PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
  node tools/ai_flight_director_capture.mjs test/fixtures/ai_flight_director_pyefis.json /tmp/cap
FONT_DIR=/path/to/dejavu/ttf python3 tools/ai_flight_director_compare.py /tmp/fd /tmp/cap \
  $PYEFIS/docs/aer-6052-evidence/styles.png ../docs/images/aer-6304
```
