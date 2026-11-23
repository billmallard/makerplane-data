# AER-6404 (G96): the knob editor in the real editor page

Drawn by headless Chromium from this branch's `configurator/public/editor.html`,
driven with real clicks by `configurator/tools/knob_encoder_capture.mjs`. The
design is one 1024x600 screen, `RADIO`, with an arc gauge (RPM), a numeric
display (COMACTFREQ), a vertical bar gauge (OILP) and a button (MENU), and no
knob wiring. Schema: the dev site's `schema.json` (v4, pyEfis #539). FIX
catalogue: the prod site's `fix_keys.json` (the dev bucket has none).

- `knob_1_reachable_unbound.png`: the gauge, the numeric display and the bar
  gauge ticked **Reachable by knob** in that order. Badges 1, 2, 3 in that
  order, drawn hollow because the screen is not bound yet; the panel says so.
- `knob_2_bound_reordered.png`: **The knob drives this screen** ticked
  (ENC3 / BTN3 / 10000 by default), then the bar gauge moved from stop 3 to
  stop 1 with ↑ twice. Badges now OILP 1, RPM 2, COMACTFREQ 3, filled.
- `knob_3_instrument_section.png`: the bar gauge's own **Knob** section,
  **Advanced** opened, showing the generated `encoder_order` 10.
- `knob_2.yaml`: the Code pane at step 2, the YAML a save sends. OILP 10,
  RPM 20, COMACTFREQ 30; `encoder`, `encoder_button`, `encoder_timeout` in
  `screens.RADIO`.

To regenerate:

```bash
cd configurator
PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
  node tools/knob_encoder_capture.mjs ../docs/images/aer-6404
```

The script prints each step's badges (`[type, number, inert|live]`) and any
page errors; the run for these images printed none.
