// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6498 (SVSSHADE3): the SVS preview follows pyEfis's shared terrain
// shading (terrain_shading.py, SVSSHADE2, pyEfis #563). The expected values
// in test/fixtures/svs_shading_pyefis.json come from the real module via
// tools/svs_shading_oracle.py, never typed by hand: the hypsometric base, the
// band rule with the airport collapse, the collapse gate, the
// terrain_shading option and the 0.7 hazard lighting floor. The lighting
// constants are read out of svs_gl.py's GLSL by the same oracle.
//
// Runs the real SVS block out of editor.html (test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCanvas } from "canvas";
import { editorSource, svsSource } from "./support/extract.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FX = JSON.parse(readFileSync(resolve(HERE, "fixtures", "svs_shading_pyefis.json"), "utf8"));
const C = FX.constants;
const SVS = new Function(`${svsSource(editorSource())}
return { SVS_PALETTES, SVS_HYPSO_STOPS, SVS_BAND, SVS_HAZARD_LIGHT_FLOOR, SVS_LIGHT,
         _svsShading, _svsHypso, _svsGate, _svsBand, _svsLight, _svsHazardLight, renderSVS };`)();

// hypso_rgb returns float32; the twin works in doubles.
const F32 = 1e-4;

test("SVS shading twin: constants are the module's", () => {
  assert.deepEqual(SVS.SVS_HYPSO_STOPS, C.HYPSO_STOPS);
  assert.deepEqual(SVS.SVS_PALETTES, C.PALETTES);
  assert.deepEqual(SVS.SVS_BAND, C.BAND);
  assert.equal(SVS.SVS_HAZARD_LIGHT_FLOOR, C.HAZARD_LIGHT_FLOOR);
  assert.equal(SVS.SVS_LIGHT.ambient, C.AMBIENT);
  assert.equal(SVS.SVS_LIGHT.diffuse, C.DIFFUSE);
  assert.deepEqual(SVS.SVS_LIGHT.sun, C.SUN_DIR);
  // the twin's slope is unexaggerated; it would need a factor otherwise
  assert.equal(C.SLOPE_EXAG, 1);
});

test("SVS shading twin: hypsometric base matches hypso_rgb across every stop", () => {
  for (const [e, r, g, b] of FX.hypso) {
    const got = SVS._svsHypso(e);
    [r, g, b].forEach((want, k) =>
      assert.ok(Math.abs(got[k] - want) < F32, `hypso(${e})[${k}] = ${got[k]}, module ${want}`));
  }
});

test("SVS shading twin: band rule matches terrain_shading.band, airport collapse included", () => {
  for (const [clr, elev, gate, green, yellow, want] of FX.band) {
    assert.equal(SVS._svsBand(clr, elev, gate, green, yellow), want,
      `band(clr ${clr}, elev ${elev}, gate ${gate}, ${green}/${yellow})`);
  }
});

test("SVS shading twin: collapse gate matches collapse_gate_ft", () => {
  for (const [near, fe, agl, want] of FX.gate) {
    assert.equal(SVS._svsGate(near, fe, agl), want, `gate(${near}, ${fe}, ${agl})`);
  }
});

test("SVS shading twin: terrain_shading values normalise as shading_mode does", () => {
  for (const [v, want] of FX.shading_mode) assert.equal(SVS._svsShading(v), want, `mode(${v})`);
  assert.equal(SVS._svsShading(undefined), C.DEFAULT_SHADING);
});

test("SVS shading twin: hazard bands are never lit below the floor; the base is untouched", () => {
  for (const [band, lit, want] of FX.hazard_light) {
    assert.equal(SVS._svsHazardLight(band, lit), want, `hazard_light(${band}, ${lit})`);
  }
});

test("SVS shading twin: lighting is the vertex shader's AMBIENT + DIFFUSE * diff", () => {
  // Closed forms from the oracle's GLSL constants: flat ground, a plane whose
  // normal is the sun direction (full sun), and one facing straight away
  // (ambient only). A sign error in either slope breaks the last two.
  const [sx, sy, sz] = C.SUN_DIR, sl = Math.hypot(sx, sy, sz);
  const w = 90, h = 120;
  const close = (a, b) => Math.abs(a - b) < 1e-12;
  assert.ok(close(SVS._svsLight(100, 100, 100, 100, w, h), C.AMBIENT + C.DIFFUSE * sz / sl));
  // normal (-dE, -dN, 1) parallel to the sun: dE = -sx/sz, dN = -sy/sz
  const dE = -sx / sz, dN = -sy / sz;
  const plane = (de, dn) => {   // corners nw, ne, se, sw of a w x h cell
    const z = (x, y) => de * x + dn * y;   // x east, y north from the SW corner
    return [z(0, h), z(w, h), z(w, 0), z(0, 0)];
  };
  assert.ok(close(SVS._svsLight(...plane(dE, dN), w, h), C.AMBIENT + C.DIFFUSE));
  assert.ok(close(SVS._svsLight(...plane(-4 * dE, -4 * dN), w, h), C.AMBIENT));
});

// --- through renderSVS ------------------------------------------------------

const PATCH = JSON.parse(readFileSync(resolve(HERE, "fixtures", "svs_coastal_patch.json"), "utf8"));
function px(options) {
  const cv = createCanvas(400, 240);
  SVS.renderSVS(cv, PATCH, options);
  return Buffer.from(cv.getContext("2d").getImageData(0, 0, 400, 240).data.buffer);
}

test("SVS shading twin: relief is the default; an unknown value previews relief", () => {
  const relief = px({ svs_terrain_shading: "relief" });
  assert.equal(Buffer.compare(px({}), relief), 0, "no option must preview relief (the device default)");
  assert.equal(Buffer.compare(px({ svs_terrain_shading: "hypso" }), relief), 0);
  assert.notEqual(Buffer.compare(px({ svs_terrain_shading: "clearance" }), relief), 0);
});

test("SVS shading twin: clearance mode ignores the relief lighting; relief ignores safe_gradient", () => {
  // safe_gradient only exists under clearance shading (svs_gl: u_relief
  // branch comes first), so it must not move a relief preview.
  const relief = px({});
  assert.equal(Buffer.compare(px({ svs_safe_gradient: false }), relief), 0);
  const clr = px({ svs_terrain_shading: "clearance" });
  assert.notEqual(Buffer.compare(px({ svs_terrain_shading: "clearance", svs_safe_gradient: false }), clr), 0);
});

test("SVS shading twin: the bands sit in the same places in both modes", () => {
  // Paint each band in a unique, unlit-proof colour (the twin multiplies, so
  // pure primaries keep their hue) and compare where each lands. With texture,
  // grid and haze off the only per-mode difference left is the base.
  const flat = { svs_terrain_texture: 0, svs_terrain_grid: 0, svs_haze: false };
  const mask = (mode) => {
    const cv = createCanvas(400, 240);
    SVS.renderSVS(cv, PATCH, { ...flat, svs_terrain_shading: mode });
    const d = cv.getContext("2d").getImageData(0, 0, 400, 240).data;
    const m = new Uint8Array(400 * 240);
    for (let i = 0; i < m.length; i++) {
      const r = d[4 * i], g = d[4 * i + 1], b = d[4 * i + 2];
      // conflict: magenta (r and b high, g low); warning: red; caution: amber
      if (r > 60 && b > 60 && g < 20) m[i] = 3;
      else if (r > 60 && g < 0.35 * r && b < 0.1 * r + 2) m[i] = 2;
      else if (r > 60 && g > 0.55 * r && g < 0.85 * r && b < 0.1 * r + 2) m[i] = 1;
    }
    return m;
  };
  const a = mask("relief"), b = mask("clearance");
  const counts = [0, 0, 0, 0];
  let differ = 0;
  for (let i = 0; i < a.length; i++) { counts[a[i]]++; if (a[i] !== b[i]) differ++; }
  for (const k of [1, 2, 3]) assert.ok(counts[k] > 200, `band ${k} barely painted (${counts[k]} px)`);
  // polygon edges antialias against a different neighbour in each mode
  assert.ok(differ < 0.01 * a.length, `${differ} px change band between modes`);
});

test("SVS shading twin: renderSVS lights a hazard band at the floor or above, and the base below it", () => {
  // The approach patch (Telluride; the served assets/editor/svs/approach.json)
  // has shadowed slopes both in the bands and in the relief base. Texture,
  // grid and haze off: a pixel is then base colour x light exactly.
  const patch = JSON.parse(readFileSync(resolve(HERE, "fixtures", "svs_approach_patch.json"), "utf8"));
  const W = 800, H = 480;
  const cv = createCanvas(W, H);
  SVS.renderSVS(cv, patch, { svs_terrain_texture: 0, svs_terrain_grid: 0, svs_haze: false });
  const d = cv.getContext("2d").getImageData(0, 0, W, H).data;
  const pal = C.PALETTES.clearance;
  // Interior pixels only (all four neighbours the same colour), so polygon
  // antialiasing can't pass for a light level.
  const same = (i, j) => d[4 * i] === d[4 * j] && d[4 * i + 1] === d[4 * j + 1] && d[4 * i + 2] === d[4 * j + 2];
  // The darkest relief colour a lit-at-the-floor base pixel could have, per
  // channel, over the stops this terrain spans (0..14000 ft).
  const stops = C.HYPSO_STOPS.filter(s => s[0] >= 0);
  const minG = Math.min(...stops.map(s => s[2]));
  let bandMin = Infinity, bandN = 0, darkBase = 0;
  for (let y = 1; y < H - 1; y++) for (let x = 1; x < W - 1; x++) {
    const i = y * W + x;
    const r = d[4 * i], g = d[4 * i + 1], b = d[4 * i + 2];
    // a relief pixel (warm, r/g under amber's 1.38 and above no stop's 1.31,
    // unlike grey runway, blue sky or the bands) darker than any base colour
    // lit at the floor. Shadowed base cells are small and far here, so no
    // interior test for these.
    if (r > g && r < 1.34 * g && g > b + 5 && g < C.HAZARD_LIGHT_FLOOR * minG - 2) darkBase++;
    if (!(same(i, i - 1) && same(i, i + 1) && same(i, i - W) && same(i, i + W))) continue;
        for (const k of [1, 2, 3]) {   // caution, warning, conflict: colour x one light level
      const l = r / pal[k][0];
      if (l > 0.2 && l <= 1.01 && Math.abs(g - pal[k][1] * l) <= 1.5 && Math.abs(b - pal[k][2] * l) <= 1.5) {
        bandMin = Math.min(bandMin, l); bandN++;
      }
    }
  }
  assert.ok(bandN > 2000, `too few band pixels found (${bandN})`);
  assert.ok(bandMin >= C.HAZARD_LIGHT_FLOOR - 0.01, `a band pixel is lit at ${bandMin.toFixed(3)}`);
  assert.ok(bandMin < C.HAZARD_LIGHT_FLOOR + 0.01, `no band pixel sits on the floor (${bandMin.toFixed(3)})`);
  assert.ok(darkBase > 100, `only ${darkBase} relief pixels below the floor: is the base floored too?`);
});
