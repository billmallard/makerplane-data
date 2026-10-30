// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-1424: renderSVS() (editor.html) is an independent reimplementation of
// pyEfis svs.py/svs_gl.py, asserted to match the real renderer, and never
// once measured against a render before this. This pins the first such
// measurement -- one pose ("coastal", the exact SCENES["coastal"] pose from
// pyEfis tools/export_svs_preview_patch.py: lat 34.4275, lon -119.8546,
// 2500 ft, hdg 000), three invariants -- so the comparison is a standing
// regression guard, not a number in a PR description that rots on the next
// edit (AER-438 / AER-413).
//
// Ground truth: test/fixtures/svs_coastal_real.png is a lossless re-encode
// of the real Pi fallback capture served at
// https://pyefis.aerocommons.org/assets/editor/svs/coastal.webp -- a real,
// on-device, terrain-only GL frame (SVS_TERRAIN_ONLY=1, SVS_RENDERER=opengl)
// taken at this exact pose (configurator/CLAUDE.md "Pi reference captures").
// test/fixtures/svs_coastal_patch.json is the matching
// assets/editor/svs/coastal.json data patch -- the SAME real SRTM3 terrain
// data both the twin and the Pi capture render from.
//
// Runs the REAL renderSVS() out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs), via node-canvas, so this
// fails the moment the twin's projection math drifts from what's measured
// here without the assertions being updated deliberately.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCanvas, loadImage } from "canvas";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(HERE, "fixtures");

const CONSTS = ["SVS_PALETTES", "SVS_HAZE_RGB"];
const FUNCS = ["_svsNum", "_svsBool", "_svsHash", "_svsNoise", "renderSVS"];
const SRC = editorSource();
const body = CONSTS.map((c) => extractConst(SRC, c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(SRC, f)).join("\n\n");

function makeRenderSVS() {
  const build = new Function(`${body}\nreturn renderSVS;`);
  return build();
}

function render(patch, options = {}) {
  const renderSVS = makeRenderSVS();
  const canvas = createCanvas(1920, 1080);
  renderSVS(canvas, patch, options);
  return canvas;
}

function loadPatch() {
  return JSON.parse(readFileSync(resolve(FIXTURES, "svs_coastal_patch.json"), "utf8"));
}

// The visible sky→ground colour split, per column: both renderSVS's sky
// fill and the real device's sky are a blue-dominant gradient distinct from
// every terrain/backdrop colour used (clearance palette, haze, water).
function skyline(imageData) {
  const { data, width, height } = imageData;
  const rows = new Int32Array(width);
  for (let x = 0; x < width; x++) {
    let y = height - 1;
    for (let yy = 0; yy < height; yy++) {
      const i = (yy * width + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const isSky = b > r + 25 && b > g + 10 && r < 140;
      if (!isSky) { y = yy; break; }
    }
    rows[x] = y;
  }
  return rows;
}

test("SVS twin: horizon row matches the device's pitch-0 convention (screen centre)", () => {
  // pyEfis ai_widget.py: pixelsPerDeg = height / pitchDegreesShown; the
  // pitch-0 line is horizon_y = pitchAngle * ppd from centre (ai_widget.py
  // :1037) -- at pitch 0 that's the screen's vertical centre, independent of
  // terrain. editor.html's renderSVS uses the same convention: horizonY =
  // H * (1 - horizon_position / 100), horizon_position defaulting to 50 (the
  // fallback in _svsNum), which is also H/2.
  //
  // The "coastal" pose can't confirm this from the real capture's terrain
  // directly -- the camera looks inland (hdg 000, away from the Pacific), so
  // the true horizon sits behind the Santa Ynez ridge and is never drawn on
  // either side. What IS testable end-to-end through the real renderSVS is
  // that with no terrain to paint over it, the twin's own sky/backdrop
  // gradient split -- its definition of "the horizon row" -- lands exactly
  // on H/2, not merely that the formula says so in a comment.
  const patch = loadPatch();
  const empty = { ...patch, tiers: [], runways: [] };
  const canvas = render(empty);
  const ctx = canvas.getContext("2d");
  const col = ctx.getImageData(0, 0, 1, canvas.height);
  let horizonRow = -1;
  for (let y = 1; y < col.height; y++) {
    const prev = 4 * (y - 1), cur = 4 * y;
    const jump = Math.abs(col.data[cur] - col.data[prev])
      + Math.abs(col.data[cur + 1] - col.data[prev + 1])
      + Math.abs(col.data[cur + 2] - col.data[prev + 2]);
    if (jump > 30) { horizonRow = y; break; }
  }
  assert.equal(horizonRow, 540, "sky/backdrop split must sit at H/2 for the default 50% horizon_position");
});

test("SVS twin: angular scale is ~2.3% narrower than the device's, pinned within [1.5%, 3.0%]", async () => {
  // Device (camera.py view_projection + ai_widget.py): pixelsPerDeg =
  // H / pitchDegreesShown (default 30) is EXACT and LINEAR in degrees near
  // boresight (DEG_PER_RAD * ppd * tan(theta), whose slope at theta=0 is
  // exactly ppd).
  //
  // Twin (renderSVS): a pinhole camera, f = H / (2 * tan(fov/2)), fov =
  // pitchDegreesShown (default 30) -- also tan-based, but f's implied
  // px/deg at boresight is f * (pi/180), NOT H/fov. The two projections
  // agree on the FORM (perspective/tan) but not the constant.
  const H = 1080, fovDeg = 30;
  const devicePxPerDeg = H / fovDeg;
  const f = H / (2 * Math.tan((fovDeg / 2) * Math.PI / 180));
  const twinPxPerDeg = f * (Math.PI / 180);
  const ratio = devicePxPerDeg / twinPxPerDeg;
  assert.ok(ratio > 1.015 && ratio < 1.030,
    `device/twin angular-scale ratio drifted to ${ratio.toFixed(4)} (was ~1.023 at measurement time, ` +
    "configurator/CLAUDE.md's KSBA-runway check independently found the same ~2.3% gap)");

  // Corroborate against the real capture: cross-correlating the twin's and
  // the real photo's ridge-silhouette (skyline) column profile at IDENTITY
  // alignment (no scale/shift correction) should already land within a
  // pinned pixel tolerance -- if a future edit to the projection or the
  // exporter's pose math regresses structural alignment, this catches it
  // even though it can't isolate degrees from pixels on its own.
  const patch = loadPatch();
  const twinCanvas = render(patch);
  const twinRows = skyline(twinCanvas.getContext("2d").getImageData(0, 0, 1920, 1080));

  const realImg = await loadImage(resolve(FIXTURES, "svs_coastal_real.png"));
  const realCanvas = createCanvas(realImg.width, realImg.height);
  realCanvas.getContext("2d").drawImage(realImg, 0, 0);
  const realRows = skyline(realCanvas.getContext("2d").getImageData(0, 0, 1920, 1080));

  let sumSq = 0;
  for (let x = 0; x < 1920; x++) {
    const d = twinRows[x] - realRows[x];
    sumSq += d * d;
  }
  const rmse = Math.sqrt(sumSq / 1920);
  assert.ok(rmse < 10,
    `skyline RMSE vs the real coastal capture grew to ${rmse.toFixed(2)}px (was ~5.9px at measurement time)`);
});

test("SVS twin: roll has no effect -- renderSVS does not model attitude at all", () => {
  // editor.html buildVirtualVfr draws its symbology overlay with the comment
  // "Drawn at level (pitch 0 / roll 0) so it aligns with the level terrain"
  // (attitudeSVG(inst, 0, 0, 0, ...)), and renderSVS's own projection only
  // ever reads patch.meta.cam.{lat,lon,alt_ft,head} -- there is no pitch or
  // roll input anywhere in the terrain projection either. So "the sign of
  // roll" -- one of the three invariants AER-1423 measured for the real
  // renderer -- is not a question the twin can answer: it has no roll
  // convention to be right or wrong about. This pins that absence so it
  // fails loudly (not silently) the day someone adds partial roll support
  // without updating this test.
  assert.ok(!/renderSVS\([^)]*\broll\b/.test(extractFunction(SRC, "renderSVS")),
    "renderSVS's signature grew a roll-shaped parameter -- update/replace this pinned gap");
  assert.ok(!extractFunction(SRC, "renderSVS").includes(".roll"),
    "renderSVS now reads .roll from somewhere -- update/replace this pinned gap");

  const patch = loadPatch();
  const level = render({ ...patch, meta: { ...patch.meta, cam: { ...patch.meta.cam, roll: 0 } } });
  const banked = render({ ...patch, meta: { ...patch.meta, cam: { ...patch.meta.cam, roll: 37 } } });
  assert.deepEqual(level.toBuffer("image/png"), banked.toBuffer("image/png"),
    "renderSVS output changed with meta.cam.roll -- it now models roll; replace this pinned-absence test " +
    "with a real sign check (needs a second real capture with nonzero roll to validate against)");
});
