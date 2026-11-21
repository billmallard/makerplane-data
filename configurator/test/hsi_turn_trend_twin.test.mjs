// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6089 (ATT1b twin of pyEfis#520): the HSI's turn-rate trend -- fixed
// white marks at half and standard rate each side of the lubber, and a
// turn_rate_color arc from the lubber to the heading reached in 6 s, capped
// at 24 deg with an arrowhead above 4 deg/s. Runs the REAL buildHSI() out of
// editor.html against test/fixtures/hsi_turn_trend_pyefis.json, written by
// tools/hsi_turn_trend_oracle.py from the merged widget's own
// _draw_turn_trend_rose / _draw_turn_trend_arc at 425 px. Regenerate the
// fixture with that tool; never hand-edit it.
//
// The twin's ring and band are its own (the rose radius sits up to 0.8 under
// the widget's, the arc band in `corners` up to 3 lower), so the trend is
// pinned RELATIVE to them: mark angles and x, radial offsets from the ring,
// offsets from the arc's root on the band, pen widths. Those are the
// quantities this change owns.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const ORACLE = JSON.parse(readFileSync(new URL("./fixtures/hsi_turn_trend_pyefis.json", import.meta.url), "utf8"));
const CONSTS = ["SOURCE_LABEL_MIN_CONTRAST", "ND_ADV", "ND_KERN"];
const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity",
  "normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance", "contrastRatio",
  "_rgbToHsv", "_hsvToRgb", "darkenToContrast", "ndAdvance", "buildHSI"];
const SAMPLE = "const rotSample = 3;";
const SOURCE = CONSTS.map((c) => extractConst(editorSource(), c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");
assert.ok(SOURCE.includes(SAMPLE), "buildHSI's preview turn rate moved; update SAMPLE");

// rot: the preview's sample turn rate, substituted so the capped path (which
// the standard-rate preview never reaches) runs through the same code.
function render(options, rot = 3) {
  const doc = {
    createElement() {
      const t = {};
      Object.defineProperty(t, "innerHTML", { set(v) { t._html = v; } });
      Object.defineProperty(t, "content", { get() { return { firstChild: { outerHTML: t._html } }; } });
      return t;
    },
  };
  const body = SOURCE.replace(SAMPLE, `const rotSample = ${rot};`);
  const { buildHSI } = new Function("document", `let _svgUid = 0; ${body}\nreturn { buildHSI };`)(doc);
  return buildHSI({ options: { heading_bug_enabled: true, ...options } }).outerHTML;
}

const attrs = (a) => Object.fromEntries([...a.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, k, v]) => [k, v]));
// The four rate marks: the lines drawn in the trend's mark width (0.08 fontSize).
function marks(svg) {
  return [...svg.matchAll(/<line ([^>]*)\/>/g)].map(([, a]) => attrs(a))
    .filter((l) => Math.abs(Number(l["stroke-width"]) - 44 / 12.5 * 0.08) < 1e-9)
    .map((l) => ({ x1: +l.x1, y1: +l.y1, x2: +l.x2, y2: +l.y2, stroke: l.stroke, w: +l["stroke-width"] }));
}
// The rose arc: "M cx top A rt rt 0 0 sweep ex ey".
function roseArc(svg) {
  const m = svg.match(/<path d="M([\d.-]+) ([\d.-]+) A([\d.]+) [\d.]+ 0 0 ([01]) ([\d.-]+) ([\d.-]+)" fill="none" stroke="([^"]+)" stroke-width="([\d.]+)" stroke-linecap="butt"\/>/);
  return m && { x0: +m[1], y0: +m[2], r: +m[3], sweep: +m[4], x1: +m[5], y1: +m[6], stroke: m[7], w: +m[8] };
}
function polyline(svg) {
  const m = svg.match(/<polyline points="([^"]+)" fill="none" stroke="([^"]+)" stroke-width="([\d.]+)" stroke-linecap="butt"\/>/);
  return m && { pts: m[1].split(" ").map((p) => p.split(",").map(Number)), stroke: m[2], w: +m[3] };
}
// The arrowhead: a 3-point path in the trend colour IMMEDIATELY after the
// trend's arc/polyline (the course pointer's head is the same shape and, on
// GPS, the same magenta).
function arrow(svg, color) {
  const at = svg.search(/stroke-linecap="butt"\/>/);
  if (at < 0) return null;
  const rest = svg.slice(svg.indexOf("/>", at) + 2);
  const m = rest.match(new RegExp(`^<path d="M([\\d.-]+) ([\\d.-]+) L([\\d.-]+) ([\\d.-]+) L([\\d.-]+) ([\\d.-]+) Z" fill="${color}"/>`));
  return m && [[+m[1], +m[2]], [+m[3], +m[4]], [+m[5], +m[6]]];
}
const ops = (cfg, op) => cfg.ops.filter((o) => o.op === op);
// The twin's ring: the rose circle drawn with the fg stroke.
function ring(svg) {
  const m = svg.match(/<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)" fill="[^"]*" stroke="#ffffff" stroke-width="0.8"/);
  return { cx: +m[1], cy: +m[2], r: +m[3] };
}
const deg = (x, y, c) => Math.atan2(x - c.cx, c.cy - y) * 180 / Math.PI;   // 0 = up, cw +

// fontSize: the twin's 44/12.5 is the widget's 15 px at 425 (3.529), so
// lengths in fontSize units agree to 0.1 % -- 0.01 covers it.
const TOL = 0.01, TOL_DEG = 0.01;

for (const [name, opts] of [["rose_top_panel_r3", { readout_layout: "top_panel" }],
                            ["rose_corners_r3", { readout_layout: "corners" }]]) {
  test(`${name}: rose marks and standard-rate arc match the widget`, () => {
    const cfg = ORACLE.configs[name], svg = render(opts), c = ring(svg);
    const want = ops(cfg, "line"), got = marks(svg);
    assert.equal(got.length, 4);
    want.forEach((w, i) => {
      const g = got[i], wc = { cx: cfg.cx, cy: cfg.cy };
      assert.ok(Math.abs(deg(g.x1, g.y1, c) - deg(...w.pts[0], wc)) < TOL_DEG, `mark ${i} angle`);
      const rin = (x, y, cc) => Math.hypot(x - cc.cx, y - cc.cy);
      assert.ok(Math.abs((rin(g.x1, g.y1, c) - c.r) - (rin(...w.pts[0], wc) - cfg.r)) < TOL, `mark ${i} inner`);
      assert.ok(Math.abs((rin(g.x2, g.y2, c) - c.r) - (rin(...w.pts[1], wc) - cfg.r)) < TOL, `mark ${i} outer`);
      assert.ok(Math.abs(g.w - w.w) < TOL, `mark ${i} width`);
      assert.equal(g.stroke, w.color);
    });
    const [wa] = ops(cfg, "arc"), a = roseArc(svg);
    assert.ok(a, "no trend arc");
    assert.ok(Math.abs((a.r - c.r) - (wa.r - cfg.r)) < TOL, "arc radius off the ring");
    assert.ok(Math.abs(a.x0 - c.cx) < 1e-9 && Math.abs(a.y0 - (c.cy - a.r)) < 1e-9, "arc root not at 12 o'clock");
    // Qt span is ccw +: a right turn is a negative span, a clockwise SVG sweep.
    assert.equal(a.sweep, wa.span < 0 ? 1 : 0);
    assert.ok(Math.abs(deg(a.x1, a.y1, c) - -wa.span) < TOL_DEG, "arc end angle");
    assert.ok(Math.abs(a.w - wa.w) < TOL, "arc width");
    assert.equal(a.stroke, wa.color);
    assert.equal(wa.cap, "FlatCap");
    assert.equal(arrow(svg, wa.color), null, "standard rate drew an arrowhead");
  });
}

test("rose_top_panel_l5: above 4 deg/s the arc stops at 24 deg and grows the widget's arrowhead", () => {
  const cfg = ORACLE.configs.rose_top_panel_l5, svg = render({ readout_layout: "top_panel" }, -5), c = ring(svg);
  const [wa] = ops(cfg, "arc"), a = roseArc(svg);
  assert.equal(a.sweep, 0);
  assert.ok(Math.abs(deg(a.x1, a.y1, c) - -wa.span) < TOL_DEG);
  const [wp] = ops(cfg, "polygon"), got = arrow(svg, wp.color);
  assert.ok(got, "no arrowhead");
  // Vertices relative to the arc's end point, widget vs twin.
  const wEnd = { x: cfg.cx + wa.r * Math.sin(-wa.span * Math.PI / 180), y: cfg.cy - wa.r * Math.cos(-wa.span * Math.PI / 180) };
  wp.pts.forEach(([x, y], i) => {
    assert.ok(Math.abs((got[i][0] - a.x1) - (x - wEnd.x)) < TOL, `arrow ${i} x`);
    assert.ok(Math.abs((got[i][1] - a.y1) - (y - wEnd.y)) < TOL, `arrow ${i} y`);
  });
});

for (const [name, opts, rot] of [["arc_top_panel_r3", { readout_layout: "top_panel" }, 3],
                                 ["arc_corners_r5", { readout_layout: "corners" }, 5]]) {
  test(`${name}: arc-view marks and trend match the widget, relative to the arc's root`, () => {
    const cfg = ORACLE.configs[name], svg = render({ ...opts, orientation: "arc" }, rot);
    const wl = ops(cfg, "polyline")[0], pl = polyline(svg);
    assert.ok(pl, "no trend polyline");
    const [wx0, wy0] = wl.pts[0], [gx0, gy0] = pl.pts[0];
    assert.ok(Math.abs(gx0 - 50) < 1e-9, "root not under the lubber");
    // Lift off the band: the twin's band y at the lubber is its lubber
    // triangle's tip less 0.35 of its tick unit (3.4), the widget's is dumped.
    const tip = Number(svg.match(/ L50 ([\d.]+) Z" fill="#ffffff"/)[1]);
    assert.ok(Math.abs((tip - 3.4 * 0.35 - gy0) - (cfg.band_top_y - wy0)) < TOL, "lift off the band");
    const want = ops(cfg, "line"), got = marks(svg);
    assert.equal(got.length, 4);
    want.forEach((w, i) => {
      const g = got[i];
      assert.ok(Math.abs(g.x1 - g.x2) < 1e-9, `mark ${i} not vertical`);
      assert.ok(Math.abs(g.x1 - w.pts[0][0]) < TOL, `mark ${i} x`);
      assert.ok(Math.abs((g.y1 - gy0) - (w.pts[0][1] - wy0)) < TOL, `mark ${i} top`);
      assert.ok(Math.abs((g.y2 - gy0) - (w.pts[1][1] - wy0)) < TOL, `mark ${i} bottom`);
      assert.ok(Math.abs(g.w - w.w) < TOL);
    });
    assert.equal(pl.pts.length, wl.pts.length, "polyline sampling");
    const [wxe, wye] = wl.pts.at(-1), [gxe, gye] = pl.pts.at(-1);
    assert.ok(Math.abs(gxe - wxe) < TOL && Math.abs((gye - gy0) - (wye - wy0)) < TOL, "trend end");
    assert.ok(Math.abs(pl.w - wl.w) < TOL);
    assert.equal(pl.stroke, wl.color);
    const wp = ops(cfg, "polygon")[0], ga = arrow(svg, wl.color);
    if (!wp) { assert.equal(ga, null, "uncapped trend drew an arrowhead"); return; }
    assert.ok(ga, "no arrowhead");
    wp.pts.forEach(([x, y], i) => {
      assert.ok(Math.abs(ga[i][0] - x) < TOL, `arrow ${i} x`);
      assert.ok(Math.abs((ga[i][1] - gy0) - (y - wy0)) < TOL, `arrow ${i} y`);
    });
  });
}

test("turn_rate_enabled false draws neither the arc nor the marks, rose or arc view", () => {
  for (const o of [{}, { orientation: "arc" }]) {
    const svg = render({ ...o, turn_rate_enabled: false });
    assert.equal(marks(svg).length, 0);
    assert.equal(roseArc(svg), null);
    assert.equal(polyline(svg), null);
  }
});

test("turn_rate_color colours the arc and arrowhead; the marks stay fg_color", () => {
  for (const o of [{}, { orientation: "arc" }]) {
    const svg = render({ ...o, turn_rate_color: "#00ff00" }, 5);
    const line = o.orientation ? polyline(svg) : roseArc(svg);
    assert.equal(line.stroke, "#00ff00");
    assert.ok(arrow(svg, "#00ff00"));
    assert.ok(marks(svg).every((m) => m.stroke === "#ffffff"));
  }
});

test("the trend paints under the lubber triangle, as the widget's does", () => {
  const rose = render({}), arc = render({ orientation: "arc" });
  assert.ok(rose.indexOf("stroke-linecap=\"butt\"") < rose.indexOf("Z\" fill=\"#ffffff\""));
  assert.ok(arc.indexOf("<polyline") < arc.indexOf("Z\" fill=\"#ffffff\""));
});
