<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-4758: moving-map zoom rail twin evidence (G23, makerplane-data #80)

A full-screen `moving_map` on a 1024x600 7" screen, 10 NM, track up, against
pyEfis `dev` @ `9ba4c90` (G22, pyEfis #388).

| File | What it shows |
|---|---|
| `compare_10nm.png` | Left: the pyEfis `MovingMap` widget, no data, `zoom_rail: true`. Middle: the editor twin. Right: an overlay where grey means they agree, red means device only, cyan means twin only |
| `compare_10nm_rail_x4.png` | The rail column of the overlay (x 0-150, y 70-460), 4x nearest-neighbour |
| `editor_on.png` | The real editor page after clicking the inspector's "Zoom rail" checkbox: the field (and "Zoom rail side") come from the dev `schema.json` |
| `twin_off.png`, `twin_on.png` | The twin's map element before and after that click |
| `pyefis_10nm.png` | The device render on its own |
| `saved.yaml` | The YAML body of the PUT that Save sent after the click: `zoom_rail: true` |

Rail column, device against the twin (twin resized to 1024x600 with
LANCZOS): mean absolute luminance difference 0.57/255, 0.15% of pixels differ
by more than 32, none by more than 96. As a control, the twin with the rail
OFF differs from the device by more than 32 on 5.26% of the same pixels.

## Regenerate

Both halves ran on the Pi 5 bench, in `~/bench-scratch/aer-4758`. It has
PyQt6 and Chromium; this repo's sandbox has neither.

```bash
# 1. pyEfis widget render: a git archive of billmallard/pyEfis @ 9ba4c90
#    (src tools tests/mock_db tests/__init__.py) in ./pyefis, pyEfis's venv.
QT_QPA_PLATFORM=offscreen PYTHONPATH=pyefis:pyefis/src:pyefis/tools \
  ~/pyEfis/.venv/bin/python cfg/tools/zoom_rail_oracle.py --render out/pyefis_10nm.png 1024 600 7

# 2. Editor captures: the unmodified public/editor.html in headless Chromium.
#    /api is mocked and /assets/editor is proxied to pyefis-dev.aerocommons.org.
PW=.../node_modules/playwright-core CHROME=/usr/bin/chromium \
  node cfg/tools/zoom_rail_capture.mjs out

# 3. Compose the comparison.
python3 cfg/tools/range_rings_compare.py out/pyefis_10nm.png out/twin_on.png out/compare_10nm.png
```

The test fixture `test/fixtures/zoom_rail_pyefis.json` is step 1 without
`--render` (see the header of `tools/zoom_rail_oracle.py`).
