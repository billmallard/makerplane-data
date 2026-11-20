# AER-5848: HSI one-button BRG tab twin (HSIBRG4, pyEfis#504)

The editor's HSI preview against the merged pyEfis widget, at boot, for every
config that draws a bearing feature. At boot no pointer is on, so the BRG tab
reads NONE.

- `hsi_bearing_sheet.png`: one row per config. Left to right: the pyEfis
  widget, the editor preview with this change, the editor preview before it.
- `hsi_bearing_tab_zoom3x.png`: the top band of each `top_panel` config
  (source tab, panel, BRG tab), nearest-neighbour 3x. Left: the widget.
  Right: the editor with this change.

## Sources

- pyEfis `dev` at `07294827` (the pyEfis#504 merge).
- makerplane-data `dev` at `761c8f8` for the "before" column.
- Editor schema from `https://pyefis-dev.aerocommons.org/assets/editor/schema.json`.

## Regenerate

From `configurator/`, with PyQt6, DejaVu fonts (DejaVu Sans Condensed
included) and a headless Chromium:

```bash
# 1. The widget: renders and test/fixtures/hsi_bearing_pyefis.json
QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis \
  python3 tools/hsi_bearing_oracle.py OUT/oracle 425

# 2. The editor tile, after and before
PW=/path/to/playwright-core CHROME=/path/to/chrome-headless-shell \
  node tools/hsi_bearing_capture.mjs OUT/oracle/hsi_bearing_oracle.json OUT/twin twin
git show 761c8f8:configurator/public/editor.html > OUT/editor_base.html
EDITOR=OUT/editor_base.html PW=... CHROME=... \
  node tools/hsi_bearing_capture.mjs OUT/oracle/hsi_bearing_oracle.json OUT/twin base

# 3. Compose
python3 tools/hsi_bearing_compare.py OUT/oracle OUT/twin ../docs/images/aer-5848 3
```

The oracle seeds the editor's sample values (heading 87, bug 50, course 110,
CDI on GPS), plus CDIV and GSV, so the widget raises no NAV/GS flag that the
static preview does not draw.
