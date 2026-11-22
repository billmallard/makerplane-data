// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6304 (AFC5 twin of pyEfis#519): the attitude indicator's flight
// director -- a magenta single cue (a chevron banked by the bank error) or
// cross-pointer bars, at command minus attitude, or nothing for fd_style off.
// Runs the REAL attitudeSVG() out of editor.html against
// test/fixtures/ai_flight_director_pyefis.json, written by
// tools/ai_flight_director_oracle.py from what the merged widget's paintEvent
// drew. Regenerate the fixture with that tool; never hand-edit it.
//
// The bars are compared in widget px: the twin's viewBox units map to px by
// one unit = h * max(1, w/h) / 100 (the slice viewBox scales by the larger
// side), centred, with the horizon_position shift the symbol frame carries.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const ORACLE = JSON.parse(readFileSync(new URL("./fixtures/ai_flight_director_pyefis.json", import.meta.url), "utf8"));
const CONSTS = ["FD_PREVIEW_ERROR", "FD_STYLES", "FD_ROLL_FULL_SCALE_DEG"];
const FUNCS = ["_polar", "flightDirectorSVG", "attitudeSVG"];
const SOURCE = CONSTS.map((c) => extractConst(editorSource(), c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");
const { attitudeSVG, FD_PREVIEW_ERROR } = new Function(
  `let _svgUid = 0; ${SOURCE}\nreturn { attitudeSVG, FD_PREVIEW_ERROR };`)();

const PX_TOL = 0.01;     // the twin writes 3 decimals of a unit (<= 6 px here)

function twin(cfg, err) {
  const { w, h } = cfg;
  const options = { ...cfg.options, pitchDegreesShown: cfg.pitchDegreesShown };
  // Pitch and roll only move the horizon; the bars depend on the error alone.
  const svg = attitudeSVG({ options }, cfg.pitch, -cfg.roll, 0, !cfg.svs, w / h, h,
    err ?? { pitch: cfg.fd_pitch - cfg.pitch, roll: cfg.fd_roll - cfg.roll });
  const sc = Math.max(1, w / h), u = h * sc / 100;
  const hpRaw = Number(options.horizon_position);
  const hoff = ((isFinite(hpRaw) ? hpRaw : 50) / 100 - 0.5) * (100 / sc);
  const g = svg.match(/<g class="fd fd-([a-z_]+)" fill="([^"]+)" stroke="([^"]+)" stroke-width="([\d.]+)" stroke-linejoin="([a-z]+)">(.*?)<\/g>/s);
  if (!g) return { svg, ops: [] };
  const ops = [...g[6].matchAll(/<polygon points="([^"]+)"\/>/g)].map(([, pts]) => ({
    pts: pts.split(" ").map((p) => p.split(",").map(Number))
      .map(([x, y]) => [(x - 50) * u + w / 2, (y - hoff - 50) * u + h / 2]),
    fill: g[2], pen: g[3], pen_w: Number(g[4]) * u, join: g[5],
  }));
  return { svg, style: g[1], ops };
}

for (const [name, cfg] of Object.entries(ORACLE.configs)) {
  if (name === "declutter_bank") continue;    // see the declutter test below
  test(`flight director twin matches pyEfis: ${name}`, () => {
    const got = twin(cfg).ops, want = cfg.ops;
    assert.equal(got.length, want.length, `shape count (${name})`);
    for (let i = 0; i < want.length; i++) {
      assert.equal(got[i].fill, "#ff00ff");
      assert.equal(got[i].pen, want[i].pen);
      assert.equal(got[i].join, "miter");
      assert.equal(want[i].join, "MiterJoin");
      assert.ok(Math.abs(got[i].pen_w - want[i].pen_w) < 1e-3, `pen ${got[i].pen_w} vs ${want[i].pen_w}`);
      assert.equal(got[i].pts.length, want[i].pts.length);
      got[i].pts.forEach(([x, y], k) => {
        const [wx, wy] = want[i].pts[k];
        assert.ok(Math.abs(x - wx) < PX_TOL && Math.abs(y - wy) < PX_TOL,
          `${name} shape ${i} vertex ${k}: twin (${x.toFixed(3)}, ${y.toFixed(3)}) vs pyEfis (${wx.toFixed(3)}, ${wy.toFixed(3)})`);
      });
    }
  });
}

test("the oracle exercised every branch it claims to", () => {
  const c = ORACLE.configs;
  assert.equal(c.off.ops.length, 0, "off draws nothing");
  assert.equal(c.declutter_bank.ops.length, 0, "declutter hides the bars");
  assert.equal(c.unknown_style.ops.length, 1, "an unknown style is the single cue");
  assert.equal(c.default_style.ops.length, 1, "no fd_style is the single cue");
  assert.equal(c.upper_case.ops.length, 2, "the style is case-insensitive");
  // The roll bar stops at full deflection (45 deg error > 30 deg full scale):
  // its centre is the cue's half-span right of centre.
  const roll = c.cross_pointer_pinned.ops[1].pts;
  assert.ok(Math.abs((roll[0][0] + roll[1][0]) / 2 - 200 - 400 * 0.16) < 1e-9, "roll bar at full scale");
  // The 3 px thickness floor binds on the 120 px-tall widget (0.014 * 120 < 3).
  const bar = c.cross_pointer_small.ops[0].pts;
  assert.ok(Math.abs((bar[2][1] - bar[1][1]) - 3) < 1e-9, "thickness floor reached");
  // A 30 deg climb error (180 px) is pinned at the glass margin, 3 x the
  // 4.2 px thickness from the top.
  const pin = c.cross_pointer_pinned.ops[0].pts;
  assert.ok(Math.abs((pin[0][1] + pin[3][1]) / 2 - 3 * 4.2) < 1e-9, "bar pinned at the top margin");
  // A 40 deg dive error on a horizon raised to 65% is pinned at the bottom.
  const low = c.cross_pointer_pinned_low_raised.ops[0].pts;
  assert.ok(Math.abs((low[0][1] + low[3][1]) / 2 - (300 - 3 * 4.2)) < 1e-9, "bar pinned at the bottom margin");
});

test("the preview draws the sample command: 4 deg up, 25 deg of bank left", () => {
  assert.deepEqual(FD_PREVIEW_ERROR, { pitch: 4, roll: -25 });
  // pyEfis styles.png's "both" column is the same error (0/10 -> 4/-15).
  for (const style of ["single_cue", "cross_pointer"]) {
    const cfg = ORACLE.configs[`${style}_both`];
    const viaDefault = twin({ ...cfg, fd_pitch: NaN, fd_roll: NaN }, FD_PREVIEW_ERROR);
    assert.deepEqual(viaDefault.ops, twin(cfg).ops);
    const svg = attitudeSVG({ options: { fd_style: style } }, -2, 4, 1, true, 1, 300);
    assert.ok(svg.includes(`class="fd fd-${style}"`), `${style} drawn with no explicit error`);
  }
  assert.ok(!attitudeSVG({ options: { fd_style: "off" } }, -2, 4, 1, true, 1, 300).includes('class="fd'));
});

test("the single cue goes under the aircraft symbol, the bars over it", () => {
  const cue = attitudeSVG({ options: { fd_style: "single_cue" } }, 0, 0, 0, true, 1, 300);
  const bars = attitudeSVG({ options: { fd_style: "cross_pointer" } }, 0, 0, 0, true, 1, 300);
  const sym = (s) => s.indexOf('<circle cx="50" cy="50"');   // the classic symbol's dot
  assert.ok(cue.indexOf('class="fd') < sym(cue));
  assert.ok(bars.indexOf('class="fd') > sym(bars));
});

test("the preview pose is not an unusual attitude, so it never declutters", () => {
  // The twin does not model the declutter: its bars are drawn at a fixed,
  // in-envelope pose. This pins the fixture's evidence that the device hides
  // them past unusualBankDeg, and that the twin's poses stay well inside it.
  assert.equal(ORACLE.configs.declutter_bank.roll, 70);
  const src = editorSource();
  assert.ok(src.includes("attitudeSVG(inst, 0, 0, 0, false, r.w / r.h,"), "SVS overlay pose moved");
});
