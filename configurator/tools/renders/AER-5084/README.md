# AER-5084 -- token-or-custom colour controls (S3, CAP-121)

Captured from the REAL editor (`public/editor.html`) by
`tools/color_tokens_capture.mjs`: `/api/*` answered locally, `/assets/editor/*`
(schema.json etc.) proxied from `https://pyefis-dev.aerocommons.org`, headless
Chromium. These are **editor twin** renders, not device frames.

| File | What it shows |
|---|---|
| `1_token_button.png` | A button whose colours are tokens (`fg_color: state.caution`, `bg_color: surface.raised`) selected: the inspector shows each colour as a token dropdown with a swatch of what the device paints; the canvas draws the resolved colours. |
| `1_inspector_2x.png` | The two colour fields from `1_token_button.png`, cropped and upscaled 2x nearest-neighbour. |
| `2_custom_button.png` | A button with a custom hex (`fg_color: "#ff4dd2"`) selected: "Custom..." plus the colour picker that existed before; its background is left at Default (`surface.raised`). |
| `2_inspector_2x.png` | The colour fields from `2_custom_button.png`, 2x nearest-neighbour. |
| `3_hsi_default_vs_ink_muted.png` | Left: HSI with every colour unset. Right: the same HSI after picking `ink.muted` from its Foreground dropdown in the inspector. Course magenta and heading-bug cyan (nav.* tokens) do not move. |

The capture also checks two things that are not pictures:

- **Nothing moves for a panel without tokens.** It writes the canvas markup of a
  token-free panel (`canvas_notokens.html`). Run once against this branch and
  once with `EDITOR=` pointing at the `dev` editor; the two files were
  byte-identical (`cmp`), so the pixels are too.
- **Tokens go to the device as tokens.** `saved.yaml`, the body of the Save PUT,
  carries `fg_color: state.caution`, `bg_color: surface.raised`, `fg_color:
  ink.muted` -- pyEfis resolves them itself (S1, pyEfis #368).

## Regenerate

```bash
cd configurator
export PW=/path/to/node_modules/playwright-core
export CHROME=/path/to/chrome-headless-shell
node tools/color_tokens_capture.mjs OUT
git show origin/dev:configurator/public/editor.html > /tmp/editor_dev.html
EDITOR=/tmp/editor_dev.html node tools/color_tokens_capture.mjs OUT_DEV
cmp OUT/canvas_notokens.html OUT_DEV/canvas_notokens.html
```

The PNGs here were then assembled from `OUT` with PIL: `3_*` is
`3a_hsi_default.png` and `3b_hsi_ink_muted.png` side by side; the `*_2x` files
crop `(1322, 720, 1600, 900)` of the page shots and resize 2x with
`Image.NEAREST`.
