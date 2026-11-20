# AER-5812: autopilot_panel twin evidence

The configurator twin of pyEfis `autopilot_panel` (AFC3, pyEfis #497),
compared with the device. Each sheet stacks, per state: the device frame, the
twin, and an overlay (device luminance in red, twin in green and blue, so
agreement reads grey and a disagreement shows as a red or cyan fringe). Each
overlay caption gives the share of pixels whose luminance differs by more than
48 of 255. All images are 1:1.

| File | What it shows |
|---|---|
| `vs_pi_sheet_full.png` | twin against the Pi 5 capture `sheet_full.png` (pyEfis #497), scenes 2 and 8, 640x260, DejaVu Sans as that sheet uses |
| `vs_pi_sheet_compact.png` | the same against `sheet_compact.png`, 900x110 |
| `preview_full.png` | twin against the real widget offscreen, in the schema's `preview` state, default font (DejaVu Sans Condensed); then `buttons: AP,HDG,NAV,ALT,VS` |
| `preview_compact.png` | the same with `layout: compact` |
| `editor_default.png`, `editor_compact_3keys.png` | the twin as the real `editor.html` draws it on its canvas, from a pasted screen YAML (default options; `layout: compact`, `buttons: AP,HDG,ALT`) |

## Regenerate

Device frames, from a pyEfis checkout with #497 (PyQt6, offscreen, a
FONTCONFIG_FILE naming a dir with the DejaVu TTFs):

```sh
PYEFIS=/path/to/pyEfis
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 configurator/tools/autopilot_panel_oracle.py --render device_full.png full 640 260
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 configurator/tools/autopilot_panel_oracle.py --render device_compact.png compact 900 110
OPTIONS='{"buttons":"AP,HDG,NAV,ALT,VS"}' QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 configurator/tools/autopilot_panel_oracle.py --render device_btn_full.png full 640 260
```

Twin frames (`OPTIONS` merges options, `PREVIEW` replaces the preview block,
the last argument is the font family):

```sh
export PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
       FONT_DIR=/path/to/dejavu-fonts-ttf-2.37/ttf
node configurator/tools/autopilot_panel_capture.mjs twin_full.png full 640 260
OPTIONS='{"buttons":"AP,HDG,NAV,ALT,VS"}' \
    node configurator/tools/autopilot_panel_capture.mjs twin_btn_full.png full 640 260
PREVIEW='{"hdg":"270","vs":"+500","ias":"---","altsel":"8500","active":["AP","FD","YD","HDG","VS"],"armed":["NAV","ALT"],"unavailable":[],"message":""}' \
    node configurator/tools/autopilot_panel_capture.mjs pi2_full.png full 640 260 "DejaVu Sans"
PREVIEW='{"hdg":"270","vs":"+500","ias":"---","altsel":"8500","active":["AP","HDG","ALT"],"armed":[],"unavailable":["APR","YD","VNAV","IAS"],"message":""}' \
    node configurator/tools/autopilot_panel_capture.mjs pi8_full.png full 640 260 "DejaVu Sans"
```

The Pi frames are crops of pyEfis's `docs/aer-5807-evidence/sheet_*.png`
(`SHEET@X,Y,W,H`; scene k of the full sheet sits at y = 30 + (k-1)*298 + 26,
of the compact sheet at y = 30 + (k-1)*148 + 26, x = 12):

```sh
SH=$PYEFIS/docs/aer-5807-evidence
python3 configurator/tools/autopilot_panel_compare.py vs_pi_sheet_full.png 1 \
    "Pi 5 sheet_full.png scene 2" "$SH/sheet_full.png@12,354,640,260" pi2_full.png \
    "Pi 5 sheet_full.png scene 8" "$SH/sheet_full.png@12,2142,640,260" pi8_full.png
python3 configurator/tools/autopilot_panel_compare.py preview_full.png 1 \
    "schema preview" device_full.png twin_full.png \
    "buttons: AP,HDG,NAV,ALT,VS" device_btn_full.png twin_btn_full.png
```

The compact sheets are made the same way at 900x110 with `compact` (Pi crops
at y = 204 and 1092).
