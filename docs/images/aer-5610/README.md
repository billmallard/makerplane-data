# AER-5610: the configurator puts the DATA flag on every panel

The real editor (`configurator/public/editor.html`), driven headlessly with a
mocked `/api` and the live editor assets, through the default DATA flag cases.
Each case clicks Save; `*.saved.yaml` is the body the editor PUT.

- `new-panel.png`: a device with no saved config. The boot screen starts with a
  `data_annunciation` at row 2, column 2, 5 x 18.
- `existing-panel.png` + `existing-panel.saved.yaml`: a panel saved before this
  change (airspeed tape, attitude indicator, altimeter tape, no flag). It opens
  with the flag added (top of LAYERS), and the save carries it.
- `existing-panel_flag_zoom3x.png`: the top left of that canvas, nearest-neighbour
  3x. The flag draws as the black rectangle: pyEfis's palette image for
  `data_annunciation` is its hidden state on black. On the device the flag is
  invisible until a pack expires or a panel change is pending, and then shows
  amber.
- `removed.png` + `removed.saved.yaml`: the same panel after the flag is selected
  and deleted. The warning shows above the canvas, and the save writes
  `main.dataFlag: false`.

## Regenerate

From `configurator/`, with `npm install` done and a headless Chromium:

```bash
PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
  node tools/data_flag_capture.mjs OUT
python3 -c "from PIL import Image; im=Image.open('OUT/existing-panel.png'); \
c=im.crop((331,150,571,230)); c.resize((c.width*3,c.height*3),Image.NEAREST).save('OUT/existing-panel_flag_zoom3x.png')"
```

The tool also writes a `restored` case: deleted, then "Put the DATA flag back"
clicked, which saves the flag again and no `dataFlag` key.
