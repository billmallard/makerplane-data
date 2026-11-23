# AER-6286: ap_annunciator twin and tape autopilot cues

The configurator twin of pyEfis `ap_annunciator` (AFC4, pyEfis #499, the
autopilot mode strip) and of the AFC4 cues on `airspeed_tape`,
`altimeter_tape` and `vsi_pfd`, compared with the device. All images are 1:1.

| File | What it shows |
|---|---|
| `vs_pi_sheet.png` | the strip in three scenes of the Pi 5 sheet on pyEfis #499 (`docs/aer-5808-evidence/joint/pi5`), B612 as that sheet uses, 1046 x 55. Per scene: the strip cropped from the Pi 5 glass capture, the real widget rendered offscreen, and the editor twin. Under each scene, the x ranges of the text and box pixels in each row. |
| `editor_left.png`, `editor_center.png` | the twin as the real `editor.html` draws it on its canvas from the dev schema, with `ap_position` left (the default, AP YD first) and `center` (lateral, AP YD, vertical). The tapes beside it show the AFC4 cues: the IASSEL box and bug, the ALTSEL box capturing, and the VSSEL bug on a VS tape and on `vsi_pfd`. |
| `tapes_vs_widget.png` | the editor's tape cues (bottom) against pyEfis's own widget sheet `docs/aer-5808-evidence/tapes.png` (top). The editor's tapes are approximations with their own scale; each cue's shape, colour and size are pinned to the widget by `test/ap_tape_cues_twin.test.mjs`, its place follows the editor's scale. |

## Regenerate

The Pi frames are pyEfis's `docs/aer-5808-evidence/joint/pi5/<scene>_single_cue.png`;
the strip sits at x 144, y 0, 1046 x 55 (the sidecar's `ModeAnnunciator`
geometry). The fonts: the DejaVu TTFs (dejavu-fonts-ttf 2.37) and the Pi's
B612 OTFs (Airbus B612 1.008, `~/.local/share/fonts` on the bench) in one dir
that `FONTCONFIG_FILE` names for Qt and `FONT_DIR` names for Chromium.

```sh
PYEFIS=/path/to/pyEfis                  # dev with #499
PI5=$PYEFIS/docs/aer-5808-evidence/joint/pi5
OUT=/tmp/aer6286; mkdir -p $OUT
export PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell FONT_DIR=/path/to/fonts

# Device halves (PyQt6, offscreen), one per scene:
for s in mtn_climb ksba_final ksba_disc; do
  OPTIONS='{"font_family":"B612"}' QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
    python3 configurator/tools/ap_annunciator_oracle.py --render $OUT/dev_$s.png 1046 55 pi_$s
done
# The ground under the strip, $OUT/bg_<scene>.png:
python3 configurator/tools/ap_annunciator_compare.py $PI5 $OUT --bg
# Twin halves, with the cells the oracle recorded for each scene:
for s in mtn_climb ksba_final ksba_disc; do
  CELLS=$(python3 -c "import json;d=json.load(open('configurator/test/fixtures/ap_annunciator_pyefis.json'));print(json.dumps([c for c in d['cases'] if c['state']=='pi_$s' and c['options'].get('font_family')=='B612'][0]['cells']))")
  CELLS="$CELLS" OPTIONS='{"font_family":"B612"}' BG=$OUT/bg_$s.png \
    node configurator/tools/ap_annunciator_capture.mjs $OUT/twin_$s.png 1046 55
done
python3 configurator/tools/ap_annunciator_compare.py $PI5 $OUT docs/images/aer-6286/vs_pi_sheet.png

# The editor canvas (proxies /assets/editor/* to the dev site):
node configurator/tools/ap_annunciator_editor_capture.mjs docs/images/aer-6286
```

`tapes_vs_widget.png` stacks pyEfis's `tapes.png` over crops of
`editor_left.png` (x 110-246, 750-910, 922-1024 and 1036-1094, y 68-592).

The fixtures the tests read come from the oracles, run against the same
pyEfis checkout:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
  python3 configurator/tools/ap_annunciator_oracle.py > configurator/test/fixtures/ap_annunciator_pyefis.json
# The font table (QT_BOLD_METRICS) now comes from the annunciator oracle
# (AER-6348): see docs/images/aer-6348/README.md.
QT_QPA_PLATFORM=offscreen PYTHONPATH=$PYEFIS/src:$PYEFIS \
  python3 configurator/tools/ap_tape_cues_oracle.py > configurator/test/fixtures/ap_tape_cues_pyefis.json
```
