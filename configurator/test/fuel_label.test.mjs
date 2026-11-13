// SPDX-License-Identifier: AGPL-3.0-or-later
//
// FUEL1f / AER-5547 (docs/fuel_provider_model.md section 6): the tank-label
// limit and the bar's name line come from the real pyEfis bar widget. The
// expected values are what tools/fuel_gauge_oracle.py read off the widget
// (test/fixtures/fuel_gauge_pyefis.json); this file runs the REAL
// public/fuel_label.js against them.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  STOCK_TANK_BARS, GLYPH_ADVANCES, PRINTABLE, labelAdvance, nameLine, stockLabelFit,
} from "../public/fuel_label.js";

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/fuel_gauge_pyefis.json", import.meta.url), "utf8"));
const advanceAt = (px) => (s) =>
  [...s].reduce((sum, ch) => sum + FIXTURE.advances[String(px)][ch], 0);

test("the fixture was measured in DejaVu Sans Condensed, not a substitute", () => {
  for (const l of FIXTURE.labels) assert.equal(l.family, "DejaVu Sans Condensed", `${l.type} ${l.w}x${l.h}`);
  assert.ok(FIXTURE.stock.length > 0, "the stock EMS2 screen has tank bars");
});

test("STOCK_TANK_BARS and GLYPH_ADVANCES are the fixture's", () => {
  assert.deepEqual(STOCK_TANK_BARS,
    FIXTURE.stock.map((s) => ({ w: s.w, h: s.h, px: s.name_px, width: s.name_rect[2] })));
  const pxs = [...new Set(FIXTURE.stock.map((s) => s.name_px))].sort((a, b) => a - b);
  assert.deepEqual(Object.keys(GLYPH_ADVANCES).map(Number).sort((a, b) => a - b), pxs);
  for (const px of pxs)
    assert.deepEqual(GLYPH_ADVANCES[px], [...PRINTABLE].map((ch) => FIXTURE.advances[String(px)][ch]), `px ${px}`);
});

test("labelAdvance never undercounts what Qt measures, and is within 6 px of it", () => {
  for (const l of FIXTURE.labels.filter((l) => l.stock))
    for (const c of l.cases) {
      const a = labelAdvance(c.label, l.name_px);
      assert.ok(a >= c.advance && a - c.advance <= 6, `${c.label} @${l.name_px}px: ${a} vs Qt ${c.advance}`);
    }
});

test("a label passes only if it fits every stock bar, as the widget measured it", () => {
  const labels = new Set(FIXTURE.labels.flatMap((l) => l.cases.map((c) => c.label)));
  for (const label of labels) {
    const qtFits = FIXTURE.labels.filter((l) => l.stock).every((l) =>
      l.cases.find((c) => c.label === label).advance <= l.name_rect[2]);
    assert.equal(stockLabelFit(label).fits, qtFits, label);
  }
  assert.equal(stockLabelFit("L MAIN").fits, true);
  assert.equal(stockLabelFit("HEADER").fits, false);
  for (let n = 1; n <= 8; n++) assert.ok(stockLabelFit(`Tank ${n}`).fits, `the default "Tank ${n}"`);
});

// Where the name's ink lands: a bar centres its first line (vertical) or
// starts it at x = 1 (horizontal), and the widget's own edge clips it.
// The browser twin measures with real shaping (kerned), so where Qt's own
// advance for a whole label is on record, measure with that.
function predictedInk(l, label) {
  const sum = advanceAt(l.name_px);
  const measure = (s) => (l.cases.find((c) => c.label === s) || {}).advance ?? sum(s);
  const [rx, , rw] = l.name_rect;
  const line = nameLine(label, rw, measure);
  const x0 = l.type === "vertical_bar_gauge" ? rx + (rw - line.advance) / 2 : rx;
  return [Math.max(0, x0), Math.min(l.w - 1, x0 + line.advance - 1)];
}

for (const l of FIXTURE.labels.filter((l) => l.type !== "arc_gauge")) {
  test(`the name line matches the real ${l.type} at ${l.w}x${l.h}`, () => {
    for (const c of l.cases) {
      const [x0, x1] = predictedInk(l, c.label);
      assert.ok(c.ink, `${c.label} leaves ink`);
      // Ink sits inside the advance box by a glyph's side bearing.
      assert.ok(Math.abs(c.ink[0] - x0) <= 4 && Math.abs(c.ink[2] - x1) <= 4,
        `${c.label}: twin ink ${x0}..${x1}, pyEfis ${c.ink[0]}..${c.ink[2]}`);
      assert.ok(c.ink[3] < l.name_rect[1] + l.name_rect[3] + 2, `${c.label}: one line only`);
    }
  });
}
