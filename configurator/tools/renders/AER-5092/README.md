# AER-5092 -- heading_display previews in white (S2 twin, CAP-121)

pyEfis #416 moved `heading_display`'s `fg_color` default from `#aaaaaa`
(`ink.muted`) to `#ffffff` (`ink.primary`). These are **editor twin** renders
from the REAL editor (`public/editor.html`), not device frames, captured by
`tools/heading_display_capture.mjs`: a 1024x600 panel holding one
`heading_display` with its colours unset, `/api/*` answered locally,
`/assets/editor/*` (schema.json) proxied from the origin named below, headless
Chromium.

`heading_display_before_after.png`: the tiles are 3x nearest-neighbour; under
each is the inspector's Foreground field at 1:1.

| Column | Editor | schema.json from | Text and frame (computed) |
|---|---|---|---|
| 1 `before` | `dev` at `2924000` | pyefis-dev (default `#ffffff`) | `rgb(170,170,170)`: the hardcoded grey wins over the schema |
| 2 `after` | this branch | pyefis-dev (default `#ffffff`) | `rgb(255,255,255)` |
| 3 `after_oldschema` | this branch | pyefis (prod, still `#aaaaaa`) | `rgb(170,170,170)`: the tile follows the schema, not a literal |

Column 1's field also shows the inconsistency the old build had: the dropdown
said `Default (ink.muted)` and the swatch was white, while the tile drew grey.

Column 3 is what production shows until pyEfis's schema.json is republished
there. The tile and swatch are grey, as the device built from that schema
draws them. The dropdown label already says `ink.primary`, because the token
map is a twin of pyEfis `dev`.

Only `heading_display` changes. Of the live-text types, only `heading_display`
and `value_text` declare a colour default in schema.json (`value_text`: only
`bg_color`). `value_text` overwrites its background later in the same branch,
so its preview is unchanged.

## Regenerate

```bash
cd configurator
export PW=/path/to/node_modules/playwright-core
export CHROME=/path/to/chrome-headless-shell
O=tools/renders/AER-5092
git show 2924000:configurator/public/editor.html > /tmp/editor_base.html
EDITOR=/tmp/editor_base.html node tools/heading_display_capture.mjs $O before
node tools/heading_display_capture.mjs $O after
ASSET_ORIGIN=https://pyefis.aerocommons.org node tools/heading_display_capture.mjs $O after_oldschema
```

Each run prints the computed text and border colour, plus the schema default
it read. The montage puts the three `*_tile.png` files side by side at 3x
nearest-neighbour, with the `*_field.png` crops beneath them at 1:1.

In the agent sandbox, Chromium also needs `LD_LIBRARY_PATH` pointed at
extracted system libs (glib, nss, atk). It also needs `FONTCONFIG_FILE` set to
any valid fonts.conf: with no config, Skia aborts the browser on the first text
draw (`SkFontMgr_FontConfigInterface ... Not implemented`).
