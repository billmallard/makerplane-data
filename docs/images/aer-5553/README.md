# AER-5553: HSI bearing-pointer twin (HSIBRG1, pyEfis#464)

The editor's HSI preview against the merged pyEfis widget, at boot, for every
config that draws a bearing feature.

- `hsi_bearing_sheet.png`: one row per config. Left to right: the pyEfis
  widget, the editor preview with this change, the editor preview before it.
- `hsi_bearing_tab_zoom3x.png`: the BRG tab corner of each `top_panel`
  config, nearest-neighbour 3x. Left: the widget. Right: the editor.

## Sources

- pyEfis `dev` at `fefa03f6` (the pyEfis#464 merge).
- makerplane-data `dev` at `7f8c01d` for the "before" column.
- Editor schema from `https://pyefis-dev.aerocommons.org/assets/editor/schema.json`,
  which pyEfis's editor-assets workflow republished at that merge.

## Regenerate

From `configurator/`, with PyQt6, DejaVu fonts and a headless Chromium:

```bash
# 1. The widget: renders and test/fixtures/hsi_bearing_pyefis.json
QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis \
  python3 tools/hsi_bearing_oracle.py OUT/oracle 425

# 2. The editor tile, after and before
PW=/path/to/playwright-core CHROME=/path/to/chrome-headless-shell \
  node tools/hsi_bearing_capture.mjs OUT/oracle/hsi_bearing_oracle.json OUT/twin twin
git show 7f8c01d:configurator/public/editor.html > OUT/editor_base.html
EDITOR=OUT/editor_base.html PW=... CHROME=... \
  node tools/hsi_bearing_capture.mjs OUT/oracle/hsi_bearing_oracle.json OUT/twin base

# 3. Compose
python3 tools/hsi_bearing_compare.py OUT/oracle OUT/twin ../docs/images/aer-5553 3
```

The oracle seeds the editor's sample values (heading 87, bug 50, course 110,
CDI on GPS), plus CDIV and GSV, so the widget raises no NAV/GS flag that the
static preview does not draw.
