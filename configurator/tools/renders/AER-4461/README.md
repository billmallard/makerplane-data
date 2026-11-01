<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-4461: moving-map range-ring twin evidence

A full-screen `moving_map` on a 1024x600 screen, range 10 NM, ownship at 50%.

| File | What it shows |
|---|---|
| `editor_default.png`, `editor_configured.png` | The real editor page: property panel scrolled to the seven `range_ring_*` fields, with the live preview beside it |
| `compare_default.png`, `compare_configured.png` | Left: the pyEfis `MovingMap` widget. Middle: the editor twin. Right: an overlay where grey means they agree, red means device only, cyan means twin only |
| `compare_default_BEFORE.png` | The same comparison against the twin on `dev` before this change (100x100 `slice` viewBox) |
| `compare_configured_label10_x8.png` | The "10" ring label from the configured overlay at 8x nearest-neighbour: about a 1 px fringe, from scaling and hinting |

The `configured` scene uses `range_ring_fractions: "0.25,0.5,0.75,1"`, `range_ring_label_angle: 135`,
`range_ring_color: "#00ffcc"`, `range_ring_opacity: 80`, `range_ring_width: 1.5`, and `range_ring_label_size: 18`.

## Regenerate

```bash
# 1. pyEfis widget renders: billmallard/pyEfis @ dev (8024da2 here), PyQt6.
#    Qt needs DejaVu Sans Condensed on its fontconfig path, or the chip text
#    falls back to DejaVu Sans and the overlay fringes. The test file is a
#    throwaway dropped into the pyEfis checkout so it can use pyEfis's own `fix`
#    fixture; the body is in the PR.
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 -m pytest --no-cov tests/test_zz_render.py

# 2. Editor captures: the unmodified public/editor.html in headless Chromium.
#    /api is mocked and /assets/editor is proxied to pyefis-dev.aerocommons.org.
PW=.../node_modules/playwright-core CHROME=.../chrome-headless-shell \
  node tools/range_rings_capture.mjs configured editor_configured.png twin_configured.png

# 3. Compose the comparison.
python3 tools/range_rings_compare.py pyefis_configured.png twin_configured.png compare_configured.png
```

The "before" capture is step 2 run with `git show origin/dev:configurator/public/editor.html`
temporarily in place of `public/editor.html`.
