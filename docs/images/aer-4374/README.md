<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-4374 Font size on the tab_section and button twins

Device render (pyEfis #356, head `afeabee`) above the configurator twin
(`editor.html` on this branch), same scene, same panel size, same
`font_percent` values. Each image is one widget:

- `tab_section_top.png`: tab_section, three tabs, 320x160, unset / 0.2 / 150.
- `tab_section_left.png`: same, `tab_position: left`, two tabs.
- `button.png`: inline button "Units", 160x80, unset / 0.5 / 0.8.

## What to compare, and what is pre-existing drift

The change under review is how each widget moves *relative to its own unset
panel*. Bar thickness, measured:

| font_percent | device label / bar px | twin label / bar px |
|---|---|---|
| unset | 12 (9 pt) / 24 | 11 / 22 |
| 0.2   | 8 / 20 (floor)  | 8 / 19 (floor) |
| 150   | 18 / 30         | 17 / 28 |

The device bar is the label plus a fixed 12 px at every size (also measured
at 0.9, 120, 200, 300: 11/23, 14/26, 24/36, 36/48), so the twin grows its bar
by the label's change rather than proportionally. The constant 1-2 px offset
is the twin's 11 px / 22 px baseline against the device's 12 px / 24 px under
offscreen Qt, which predates this change and is unchanged by it (unset must
stay byte-identical).

Also pre-existing and out of scope: the twin button's colours (the offscreen
device button has no stylesheet colours here) and its size rule
`0.45 / lines * h` against the device's `0.38 * h`. Both scale by the same
factor, so the ratios between panels match.

Unset is pixel-identical to `origin/main` (`415f83d`): each scene's unset
panel was rendered from both editors through the same proto page and the
RGB bytes compared equal. `dev` (`11f3fb1`) differs from that main only in
the inspector and save path, none of the extracted twin functions.

## Regenerating

Twin (from `configurator/`; headless Chromium setup as in
`docs/images/aer-665/README.md`, with `FONTCONFIG_FILE` pointing at a dir
holding DejaVu):

```bash
for n in 0 1 2; do
  node tools/font_size_proto.mjs --out /tmp/fs$n.html --scene $n
  WS=976,182; [ $n = 2 ] && WS=496,102
  chrome-headless-shell --no-sandbox --disable-gpu --hide-scrollbars \
    --window-size=$WS --screenshot=/tmp/work/twin/$n.png file:///tmp/fs$n.html
done
```

Device (from a pyEfis checkout at PR #356's head; Qt offscreen needs a
fontconfig with DejaVu too, or every glyph renders as a box):

```bash
QT_QPA_PLATFORM=offscreen FONTCONFIG_FILE=/tmp/fonts.conf PYTHONPATH=src \
  python3 <makerplane-data>/docs/images/aer-4374/render_device.py /tmp/work/device
```

Compose:

```bash
CAPTION_FONT=/path/to/DejaVuSans-Bold.ttf \
  python3 docs/images/aer-4374/compose.py /tmp/work docs/images/aer-4374
```
