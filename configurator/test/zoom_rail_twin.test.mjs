// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4758 (G23, makerplane-data #80): the moving map's zoom rail, twinned
// from pyEfis MovingMap (G22, pyEfis #205). The expected values are not
// hand-written: they are what the REAL widget computes and paints, dumped by
// tools/zoom_rail_oracle.py into test/fixtures/zoom_rail_pyefis.json
// (regenerate with the command in that script's header). This test runs the
// REAL editor functions against them (see test/support/extract.mjs), then
// checks the options survive a save.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const FNS = ["pyFloat", "displayPxPerMm", "railLadder", "zoomRailGeom", "zoomRailSvg"];
const { displayPxPerMm, railLadder, zoomRailGeom, zoomRailSvg } = new Function(`
  ${FNS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { displayPxPerMm, railLadder, zoomRailGeom, zoomRailSvg };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/zoom_rail_pyefis.json", import.meta.url), "utf8"));

const close = (a, b, what) =>
  assert.ok(Math.abs(a - b) < 1e-9, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what) => {
  for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`);
};

// The twin's range is rangeRingGeom's: range_nm, default 10 (the chip's).
const rangeOf = (o) => (o.range_nm ?? 10);

for (const c of FIXTURE.cases) {
  test(`matches pyEfis MovingMap zoom rail: ${JSON.stringify(c.options)} @ ${c.w}x${c.h} on ${c.screen.inches}"`, () => {
    close(displayPxPerMm(c.screen), c.px_per_mm, "px per mm");
    const g = zoomRailGeom(c.options, c.w, c.h, c.px_per_mm, rangeOf(c.options));
    if (c.rail === null) {
      assert.equal(g, null, "no rail");
      assert.equal(c.ops.length, 0, "the widget painted nothing");
      assert.equal(zoomRailSvg(g), "", "the twin draws nothing");
      return;
    }
    const e = c.rail;
    closeRect(g.claim, e.claim, "claim");
    closeRect(g.plus, e.plus, "plus cap");
    closeRect(g.minus, e.minus, "minus cap");
    for (const k of ["x", "y0", "y1", "touch"]) close(g[k], e[k], k);
    assert.deepEqual(g.ladder, e.ladder, "ladder");
    g.rungY.forEach((y, i) => close(y, e.rung_y[i], `rung ${i} y`));
    close(g.thumbY, e.thumb_y, "thumb y");

    // What _paint_zoom_rail drew: the haloed path twice, then the thumb.
    const [halo, ink, ring, disc] = c.ops;
    assert.deepEqual(c.ops.map((op) => op.op), ["path", "path", "ellipse", "ellipse"]);
    assert.deepEqual(halo.pen.rgba, [0, 0, 0, 170]);
    assert.deepEqual(ink.pen.rgba, [205, 205, 205, 255]);
    assert.ok(halo.pen.round_cap && ink.pen.round_cap, "round caps");
    close(ink.pen.width, g.lw, "line width");
    close(halo.pen.width, g.lw + 3, "halo width");
    assert.equal(g.segments.length, ink.segments.length, "segment count");
    g.segments.forEach((s, i) => {
      assert.equal(ink.segments[i].length, 2, `segment ${i} is one line`);
      s.forEach((pt, j) => {
        close(pt[0], ink.segments[i][j][0], `segment ${i} pt ${j} x`);
        close(pt[1], ink.segments[i][j][1], `segment ${i} pt ${j} y`);
      });
    });
    assert.deepEqual(ring.pen.rgba, [0, 0, 0, 170]);
    assert.equal(ring.brush, null, "the halo ring is not filled");
    close(ring.pen.width, 2, "ring width");
    close(ring.rx, g.r + 1, "ring radius");
    assert.equal(disc.pen, null, "the disc has no outline");
    assert.deepEqual(disc.brush, [205, 205, 205, 255]);
    close(disc.rx, g.r, "thumb radius");
    for (const op of [ring, disc]) {
      close(op.cx, g.x, "thumb x");
      close(op.cy, g.thumbY, "thumb y (painted)");
    }
  });
}

test("the fixture covers off by default, on, both sides, and a between-rung range", () => {
  const off = FIXTURE.cases.find((c) => Object.keys(c.options).length === 0);
  assert.equal(off.rail, null, "zoom_rail unset = no rail");
  assert.ok(FIXTURE.cases.some((c) => c.options.zoom_rail_side === "right" && c.rail));
  const mid = FIXTURE.cases.find((c) => c.options.range_nm === 13.7);
  assert.ok(mid.rail.thumb_y > mid.rail.rung_y[2] && mid.rail.thumb_y < mid.rail.rung_y[3]);
});

test("10 NM on the default ladder sits on the third detent", () => {
  const g = zoomRailGeom({ zoom_rail: true }, 1024, 600, displayPxPerMm({ width: 1024, height: 600, inches: 7 }), 10);
  assert.equal(g.thumbY, g.rungY[2]);
});

test("a YAML list is not a comma string: the ladder falls back like str() does", () => {
  assert.deepEqual(railLadder({ range_ladder: [2, 5, 10] }), [10]);
  assert.deepEqual(railLadder({ range_ladder: null }), [10]);
  assert.deepEqual(railLadder({ range_ladder: 20 }), [20]);
  assert.deepEqual(railLadder({}), [2, 5, 10, 20, 40, 80, 160]);
});

test("no diagonal, or an implausible one, falls back to 96 DPI", () => {
  close(displayPxPerMm({ width: 1024, height: 600, inches: null }), 96 / 25.4, "none");
  close(displayPxPerMm({ width: 1024, height: 600 }), 96 / 25.4, "absent");
  close(displayPxPerMm({ width: 2000, height: 1200, inches: 4 }), 96 / 25.4, "too dense");
});

test("the twin draws the rail with zoom_rail on and nothing with it off", () => {
  const ppm = displayPxPerMm({ width: 1024, height: 600, inches: 7 });
  const on = zoomRailSvg(zoomRailGeom({ zoom_rail: true }, 1024, 600, ppm, 10));
  assert.equal((on.match(/<path /g) || []).length, 2);
  assert.equal((on.match(/<circle /g) || []).length, 2);
  assert.equal(zoomRailSvg(zoomRailGeom({ zoom_rail: false }, 1024, 600, ppm, 10)), "");
});

test("zoom_rail and zoom_rail_side round-trip through the saved YAML", () => {
  // Save is js-yaml dump of the design; load is js-yaml load. The options
  // object is written as-is, so a boolean stays a boolean.
  const inst = { type: "moving_map", row: 0, column: 0, span: { rows: 110, columns: 200 },
                 options: { range_nm: 10, zoom_rail: true, zoom_rail_side: "right" } };
  const back = jsyaml.load(jsyaml.dump({ instruments: [inst] })).instruments[0].options;
  assert.equal(back.zoom_rail, true);
  assert.equal(back.zoom_rail_side, "right");
  const off = jsyaml.load(jsyaml.dump({ o: { zoom_rail: false } })).o;
  assert.equal(off.zoom_rail, false);
});
