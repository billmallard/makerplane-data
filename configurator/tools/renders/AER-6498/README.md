# AER-6498 (SVSSHADE3): the SVS preview follows the shared terrain shading

The editor's `renderSVS()` twin now ports pyEfis SVSSHADE2 (pyEfis #563,
`src/pyefis/instruments/ai/terrain_shading.py` and the `svs_gl.py` fragment
shader): the hypsometric relief base, the hazard bands with the airport
collapse, the 0.7 hazard lighting floor and the `svs_terrain_shading` option
(default `relief`).

## What is here

| File | What it shows |
|---|---|
| `compare_<scene>.png` | Top row: SVSSHADE2's Beelink capture on pyEfis dev before the change (clearance), beside the editor twin with `svs_terrain_shading: clearance`. Bottom row: SVSSHADE2's capture of the branch (relief), beside the twin with no option saved (relief). The same four poses (pyEfis `tools/export_svs_preview_patch.py` SCENES), 800x480, the twin at `pitchDegreesShown: 50` (the device default). |
| `editor_scenes.png` | The real editor page (headless Chromium, `tools/svs_editor_capture.mjs`), one virtual_vfr per scene. Top row: no option saved (relief). Bottom row: `svs_terrain_shading: clearance`. Left to right: coastal, mountains, approach, final. Scaled to 50%. |
| `editor_inspector.png` | The inspector of the selected virtual_vfr: the "Terrain shading" field, value `relief`, choices `relief` and `clearance`, from the dev site's schema.json. |
| `fallback/<scene>.webp` | New fallback frames for `assets/editor/svs/<scene>.webp`: terrain-only GL captures on the Pi 5 at the four poses, pyEfis dev `b30b785b` (relief), 1920x1080 like the frames served before. **On dev since 2026-11-25; production waits** (see Upload below). The sidecars (`<scene>.png.json`) describe the PNG each webp was encoded from (PIL, quality 85, method 6); their `sha256` is the PNG's. |

The device halves of `compare_<scene>.png` carry the SVS symbology; the twin
draws terrain only. The twin's coastal patch floods the Goleta Slough flats
as water (the known gap in configurator/CLAUDE.md).

## Commands

Twin renders (node-canvas, the real SVS block out of editor.html):

    node tools/renders/AER-6498/render_svs.mjs public/editor.html PATCHES OUT 800 480 50
    python3 tools/renders/AER-6498/compose.py \
      $PYEFIS/docs/images/svsshade2/beelink OUT tools/renders/AER-6498 DejaVuSans.ttf

`PATCHES` holds `coastal.json` ... `final.json` fetched from
`https://pyefis-dev.aerocommons.org/assets/editor/svs/` (byte-identical to
the production copies on 2026-11-24).

`terrain_shading: clearance` is unchanged: the same command against
`git show origin/dev:configurator/public/editor.html` gives byte-identical
`<scene>_clearance.png` for all four scenes, at the editor's default FOV and
at 50.

Real editor:

    PW=.../playwright-core CHROME=.../chrome-headless-shell \
      node tools/svs_editor_capture.mjs OUT

Fallback frames (Pi 5, alongside the live pyEfis, no restart; scratch is
removed afterwards):

    scp pi6498.sh wpballard@10.110.10.199:/tmp/
    ssh wpballard@10.110.10.199 'flock -w 900 /tmp/pyefis-bench.lock \
      sh /tmp/pi6498.sh /home/wpballard/pyEfis/.venv/bin/python /data/makerplane-data'

Upload. Done for dev on 2026-11-25 (Bill, card f0c128fc on AER-6507): `assets/editor/dev/svs/` now serves these four files byte for byte; production's `assets/editor/svs/` is unchanged. The production upload is carried as a step for the pyEfis `qa -> master` release that contains SVSSHADE2 (maos-workspace `makerplane/processes/release_promotion.md`, "Manual production steps carried to `master`"). Editor assets are per environment (`src/index.ts`
`assetKey`: production reads `assets/editor/`, dev reads
`assets/editor/dev/`, qa `assets/editor/qa/`), and pyEfis's editor-assets job
leaves the svs frames to a manual upload. The relief frames belong in an
environment once its schema.json carries `svs_terrain_shading` (dev today;
production when pyEfis promotes SVSSHADE2):

    for s in coastal mountains approach final; do
      npx wrangler r2 object put makerplane-configs/assets/editor/dev/svs/$s.webp \
        --file tools/renders/AER-6498/fallback/$s.webp --content-type image/webp --remote
    done
