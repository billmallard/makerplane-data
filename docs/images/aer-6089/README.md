# AER-6089: HSI turn-rate trend, editor preview vs pyEfis widget

ATT1b twin of pyEfis#520. Widget renders come from pyEfis `dev` @
`d9f7066` (after #520 merged); the editor preview is `buildHSI()` from this
branch's `configurator/public/editor.html`.

- `hsi_turn_trend_sheet.png`: the Pilot's Guide figure
  (pyEfis `docs/images/hsi/turn_rate_trend.png`) on top, then one row per
  config: the pyEfis widget at the editor preview's sample pose (heading 87,
  bug 50, course 110) on the left, the editor preview on the right. Rows:
  arc view corners at +5 deg/s (capped, arrowhead), arc view top_panel at
  +3, rose corners at +3, rose top_panel at -5 (capped, left), rose
  top_panel at +3 (what the editor shows).
- `hsi_turn_trend_zoom3x.png`: the band around the lubber where the trend
  lives, 3x nearest-neighbour, widget left, preview right.

The preview is rasterised by QtSvg, not a browser (headless Chromium does not
launch in the sandbox that produced these). Geometry is the editor's own SVG,
but QtSvg ignores `rgba()` fills and strokes, so the readout panel's border and
translucent fill are missing on the right. That is the rasteriser, not
the editor.

The twin's rose and arc band were already slightly off the widget's
before this change: the rose radius is 48.0 vs 48.8 in `corners`, and the arc
band sits about 3 units lower in `corners`. The trend hangs off the twin's own
ring and band, so it inherits that offset. The geometry test pins the trend
relative to the ring and band.

## Regenerate

```bash
# pyEfis checkout at dev, PyQt6 importable; FONT_DIR only where DejaVu is
# not installed at the path tools/render_instrument.py looks in.
export QT_QPA_PLATFORM=offscreen PYEFIS=/path/to/pyEfis FONT_DIR=/path/to/dejavu/ttf
cd configurator
python3 tools/hsi_turn_trend_oracle.py /tmp/tt 425      # widget grabs + JSON
cp /tmp/tt/hsi_turn_trend_oracle.json test/fixtures/hsi_turn_trend_pyefis.json
node tools/hsi_turn_trend_svg.mjs /tmp/tt/hsi_turn_trend_oracle.json /tmp/tt
python3 tools/hsi_turn_trend_compare.py /tmp/tt ../docs/images/aer-6089 \
  $PYEFIS/docs/images/hsi/turn_rate_trend.png 3
```
