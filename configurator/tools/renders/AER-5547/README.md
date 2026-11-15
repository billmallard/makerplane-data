<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-5547: tank picker, orphan mark and stock-bar migration evidence (FUEL1f)

Five vertical bars at the stock EMS2 tank-bar size (64 x 327 device px on a
1280x720 screen, `small_font_percent: 0.06`), against an aircraft with
L MAIN (slot 1), R MAIN (2) and L TIP (3). R TIP was removed. Measured
against pyEfis `dev` @ `9f7a1145f4c57c023a0a92851b999390e0217518`.

The five bars, left to right: a plain `FUELQ1` "Tank 1"; an untouched stock
`FUELQ3` "Tank 3"; a bar bound to L TIP; a bar bound to the removed R TIP;
a hand-named `FUELQ4` "R TIP AUX".

| File | What it shows |
|---|---|
| `compare_labels.png` | Left: the real pyEfis `VerticalBar`, each drawn as the device receives it (no FIX data, so a bound bar is its safe colour with no bands). Right: the editor twin, scaled to the same height. 2x nearest-neighbour. |
| `device_labels.png`, `twin_labels.png` | The two halves on their own, 1:1 |
| `orphan_props.png` | The orphan selected: outlined red, the picker reads "R TIP - removed" in red, and says what the device draws |
| `migrate_hover.png` | "Tank 3" selected, pointer on its "Bind to L TIP" offer: the twin previews L TIP (dashed outline); nothing is saved yet |
| `migrate_done.png` | After the click: the picker reads "L TIP - FUELQ3", the key and name show what the device receives |
| `saved.yaml` | The PUT body after Save: the migrated bar stores `tank: tk_lt` only |
| `capture.json` | The names the twin drew, the orphan picker's options, and the options before and after the click |

What to look at in `compare_labels.png`:

- "R TIP AUX" does not fit the bar. The widget word-wraps and the bar covers
  every line after the first, so both draw "R TIP".
- The orphan: the bar's bad colour set, no bands, the pointer at 0, `xxx`,
  and its last label in grey. The red outline is editor chrome, not twin.
- Not changed here: the twin's sample bands and its value text size. The
  device draws its bands from fix-gateway (none in this offscreen render).

## Regenerate

Ran in the agent sandbox with PyQt6, DejaVu 2.37 TTFs named by
`FONTCONFIG_FILE`, Playwright's headless Chromium and Pillow.

```bash
cd configurator
O=tools/renders/AER-5547
PYEFIS=/path/to/pyEfis    # @ dev 9f7a1145, PyQt6 installed
export QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS/tools:$PYEFIS

# 1. Editor: the unmodified public/editor.html in headless Chromium; /api is
#    mocked and /assets/editor is proxied to pyefis-dev.aerocommons.org.
PW=.../node_modules/playwright-core CHROME=.../chrome-headless-shell \
  node tools/fuel_gauge_capture.mjs $O > $O/capture.json

# 2. Device, then the composition and crops.
python3 tools/fuel_gauge_oracle.py --render $O/device_labels.png
python3 tools/fuel_gauge_compare.py $O
```

The test fixture `test/fixtures/fuel_gauge_pyefis.json` is the oracle
without `--render` (see the header of `tools/fuel_gauge_oracle.py`).
