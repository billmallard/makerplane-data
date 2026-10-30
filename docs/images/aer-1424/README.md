<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-1424 — SVS twin vs real render, coastal pose

`renderSVS()` (`configurator/public/editor.html`) is an independent
reimplementation of pyEfis `svs.py`/`svs_gl.py`, asserted to match the real
renderer and required to follow it in lockstep, but never once compared to a
render before this. This is that first comparison: one pose, three
invariants, measured and pinned in
`configurator/test/svs_twin_fidelity.test.mjs`.

## What these are

| File | What it shows |
|---|---|
| `coastal_side_by_side.png` | Top: the real Pi terrain-only GL capture at `SCENES["coastal"]` (`assets/editor/svs/coastal.webp`, `lat 34.4275, lon -119.8546, alt 2500 ft, hdg 000`). Bottom: `renderSVS()` run **headlessly**, unmodified, via `node-canvas`, on the exact same `coastal.json` data patch (same real SRTM3 terrain). |
| `coastal_skyline_overlay.png` | The ridge silhouette (row of the first non-sky pixel, per column) for both images, overlaid at identity alignment (no manual shift/scale correction). RMSE = 5.91 px out of 1080 rows. |

## The three invariants

1. **Horizon row.** pyEfis's own convention (`ai_widget.py`: `pixelsPerDeg =
   height / pitchDegreesShown`; `horizon_y = pitchAngle * ppd`, so pitch 0 is
   exactly the screen's vertical centre) and the twin's (`horizonY = H * (1 -
   horizon_position/100)`, `horizon_position` defaulting to 50) land on the
   same row — `H/2 = 540` — by construction. Confirmed by running the real
   `renderSVS()` against an emptied patch (no terrain to paint over it) and
   reading the sky→backdrop colour transition directly: row 540, exactly.
   **Caveat:** the coastal pose itself can't independently confirm this from
   the captured pixels — the camera looks inland (hdg 000, away from the
   Pacific), so the true horizon sits behind the Santa Ynez ridge in both
   images and is never drawn on either side.
2. **Angular scale.** The device (`camera.py` + `ai_widget.py`) uses
   `pixelsPerDeg = H / pitchDegreesShown` — exact and linear in degrees near
   boresight. The twin uses a pinhole `f = H / (2*tan(fov/2))` with the same
   default `fov = pitchDegreesShown = 30`. Same functional form
   (perspective/tan), different constant: at boresight the twin's implied
   px/deg is `f * (π/180) ≈ 35.18`, the device's is `H/30 = 36.0` — the twin
   is **~2.3% angularly narrower**. This matches the KSBA-runway check
   `configurator/CLAUDE.md` already documented ("matches within 2.3%"),
   independently rederived here from source and corroborated by the skyline
   RMSE: constraining the identity-alignment search to a physically
   plausible ±10% horizontal scale finds a best fit at `s ≈ 1.022`, i.e. the
   same ~2.2–2.3% figure, not just a coincidence of one prior measurement.
3. **Sign of roll — not measurable.** `renderSVS()` takes no pitch or roll
   input anywhere: the terrain projection only ever reads
   `patch.meta.cam.{lat,lon,alt_ft,head}`, and the symbology overlay is drawn
   with `attitudeSVG(inst, 0, 0, 0, ...)` — hardcoded level, per the
   `editor.html` comment "Drawn at level (pitch 0 / roll 0) so it aligns with
   the level terrain". There is no roll convention in the twin to be right or
   wrong about, so this invariant is a **gap, not a match**: the SVS/virtual_vfr
   preview is a fixed-attitude static preview, not an attitude-driven view.
   What it would take to measure it for real: a `roll_deg`/`pitch_deg` field
   on the exported scene pose (`tools/export_svs_preview_patch.py`), a
   roll/pitch-aware rotation added to `renderSVS()`'s projection, and a second
   real Pi capture at nonzero roll to check the sign against.

## What this does and does not prove

**Proves:** `renderSVS()` can be driven headlessly (node-canvas, no browser),
so its output is checkable in CI without a GPU or a live Pi bench; the
horizon-row convention matches by formula and by driving the real code; the
angular-scale gap is real, small, and consistent across two independent
measurement methods (formula + pixel correlation); and the twin's roll
handling isn't a "wrong sign", it's simply absent.

**Does not prove:** fidelity at any other pose, altitude band, or attitude —
this is one pose (`coastal`, level, 2500 ft). Do not read this as a general
twin-vs-renderer parity result; extending it to more poses/attitudes is a
follow-up, not something to infer from this evidence.

## Regenerate

```bash
# from the pyEfis repo, on a machine with real SRTM3 + NASR data:
PYTHONPATH="C:/pylib;src" python tools/export_svs_preview_patch.py \
    --out work/svs_patches --scene coastal

# the ground-truth capture is the live fallback the configurator already
# serves at the same exporter pose (see configurator/CLAUDE.md "Pi reference
# captures" for how it's taken):
curl -o coastal.webp https://pyefis.aerocommons.org/assets/editor/svs/coastal.webp
curl -o coastal.json https://pyefis.aerocommons.org/assets/editor/svs/coastal.json

# from makerplane-data/configurator, with `canvas` installed (NODE_ENV must
# not be "production" or npm silently skips devDependencies):
NODE_ENV=development npm install
NODE_ENV=development node --test test/svs_twin_fidelity.test.mjs
```

The side-by-side and skyline-overlay images were produced with plain
PIL/matplotlib from the twin's `canvas.toBuffer("image/png")` output and the
real capture, using the row-of-first-non-sky-pixel classifier documented
inline in `test/svs_twin_fidelity.test.mjs`'s `skyline()` helper.
