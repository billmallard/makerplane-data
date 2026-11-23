// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6286 (AFC4): the ap_annunciator twin against what the REAL pyEfis
// ModeAnnunciator paints. The expected values are not hand-written: they are
// dumped by tools/ap_annunciator_oracle.py into
// test/fixtures/ap_annunciator_pyefis.json (regenerate with the command in
// that script's header; QT_BOLD_METRICS comes from
// tools/qt_bold_metrics.py over the annunciator oracle's fixture). This test runs the REAL editor functions
// against them (see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["AP_STRIP_SLOTS", "AP_STRIP_TEMPLATES", "AP_STRIP_COLORS", "QT_BOLD_METRICS"];
const FUNCS = ["fontScale", "apColor", "apEsc", "qtBoldFont", "apStripFit", "apStripSlots",
  "apStripCells", "apStripGeom", "apStripSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { qtBoldFont, apStripSlots, apStripCells, apStripGeom, apStripSvg, QT_BOLD_METRICS };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/ap_annunciator_pyefis.json", import.meta.url), "utf8"));

const close = (a, b, what, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what, tol) => { for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`, tol); };
// The twin's [css "#rrggbb", alpha 0..1] as Qt's 0..255 rgba.
const rgba = ([css, a]) => [1, 3, 5].map((i) => parseInt(css.slice(i, i + 2), 16)).concat(Math.round(a * 255));
const label = (c) => `${c.state} ${c.w}x${c.h} ${JSON.stringify(c.options)}`;

test("the twin's font table is Qt's metrics, value for value", () => {
  const M = FIXTURE.metrics, cols = M.columns;
  for (const [fam, t] of Object.entries(M.families)) {
    for (const r of t.rows) {
      const f = twin.qtBoldFont(fam, r[0]);
      assert.equal(f.ascent, r[cols.indexOf("ascent")], `${fam} ${r[0]}px ascent`);
      assert.equal(f.descent, r[cols.indexOf("descent")], `${fam} ${r[0]}px descent`);
      for (const ch of M.chars) assert.equal(f.advance(ch), r[cols.indexOf(ch)], `${fam} ${r[0]}px "${ch}"`);
    }
  }
});

test("a string's advance is Qt's, within 2 px, exact for nearly all", () => {
  // Hinting moves a kerned pair by a pixel at some sizes, which the per-glyph
  // table cannot carry (the oracle's probe: 8 of 2,457 strings at 4-120 px).
  let n = 0, exact = 0;
  for (const c of FIXTURE.cases) {
    for (const o of c.ops.filter((x) => x.op === "text")) {
      const d = Math.abs(twin.qtBoldFont(o.family, o.pixel_size).advance(o.text) - o.advance);
      assert.ok(d <= 2, `${label(c)} "${o.text}" ${o.pixel_size}px: off by ${d}`);
      n++; if (d === 0) exact++;
    }
  }
  assert.ok(exact / n >= 0.98, `${exact} of ${n} exact`);
});

test("the preview block becomes the cells the device shows for it", () => {
  const cells = twin.apStripCells(FIXTURE.schema_preview);
  for (const c of FIXTURE.cases.filter((x) => x.state === "preview")) {
    assert.deepEqual(cells, c.cells, label(c));
  }
});

test("slot rects and dividers are slot_rects / group_dividers", () => {
  for (const c of FIXTURE.cases) {
    const pos = c.options.ap_position == null ? "left" : c.options.ap_position;
    const rects = twin.apStripSlots(pos, c.w, c.h);
    for (const [slot, r] of Object.entries(c.slot_rects)) closeRect(rects[slot], r, `${label(c)} ${slot}`, 1e-9);
  }
});

for (const c of FIXTURE.cases) {
  test(`paint ops match pyEfis: ${label(c)}`, () => {
    const g = twin.apStripGeom(c.options, c.cells, c.w, c.h, c.on);
    const ops = c.ops;
    const fill = ops.filter((o) => o.op === "fill_rect");
    if (!fill.length) { assert.equal(g.bg, null); } else {
      assert.equal(fill.length, 1);
      closeRect({ x: 0, y: 0, w: g.w, h: g.h }, fill[0].rect, "background");
      const want = fill[0].rgba;
      assert.deepEqual(rgba(g.bg).slice(0, 3), want.slice(0, 3), "background colour");
      // Qt stores alpha in 16 bits and reports the top 8; the twin keeps the float.
      close(g.bg[1] * 255, want[3], "background alpha", 1);
    }
    const lines = ops.filter((o) => o.op === "line");
    assert.equal(g.dividers.length, lines.length, "divider count");
    lines.forEach((l, i) => {
      const d = g.dividers[i];
      assert.deepEqual([d.x1, d.y1, d.x2, d.y2], l.args, `divider ${i}`);
      close(d.width, l.pen.width, `divider ${i} width`);
      assert.deepEqual(rgba(d.color), l.pen.rgba, `divider ${i} colour`);
    });
    const rects = ops.filter((o) => o.op === "rect");
    assert.equal(g.boxes.length, rects.length, "box count");
    rects.forEach((r, i) => {
      closeRect(g.boxes[i].rect, r.rect, `box ${i}`, 1e-9);
      close(g.boxes[i].width, r.pen.width, `box ${i} width`);
      assert.deepEqual(rgba(g.boxes[i].color), r.pen.rgba, `box ${i} colour`);
    });
    const texts = ops.filter((o) => o.op === "text");
    assert.deepEqual(g.texts.map((t) => t.text), texts.map((t) => t.text), "texts drawn, in order");
    texts.forEach((t, i) => {
      const tw = g.texts[i];
      assert.equal(tw.px, t.pixel_size, `"${t.text}" pixel size`);
      assert.equal(tw.family, t.family, `"${t.text}" family`);
      assert.deepEqual(rgba(tw.color), t.pen.rgba, `"${t.text}" colour`);
      closeRect(tw.rect, t.rect, `"${t.text}" rect`, 1e-9);
      // Qt's laid-out line box: top = baseline - ascent; left within the
      // advance's rounding (boundingRect can be a pixel wider than it).
      close(tw.baseline - (t.ascent), t.laid_out.y, `"${t.text}" line top`);
      close(tw.x, t.laid_out.x, `"${t.text}" left`, 1);
    });
  });
}

test("ap_position center visibly reorders the groups", () => {
  const pv = FIXTURE.schema_preview, cells = twin.apStripCells(pv);
  const order = (o) => twin.apStripGeom(o, cells, 940, 50).texts
    .slice().sort((a, b) => a.rect.x - b.rect.x).map((t) => t.text);
  assert.deepEqual(order({}), ["AP", "YD", "HDG", "GPS", "VS +500", "ALT"]);
  assert.deepEqual(order({ ap_position: "center" }), ["HDG", "GPS", "AP", "YD", "VS +500", "ALT"]);
  const svgL = twin.apStripSvg(twin.apStripGeom({}, cells, 940, 50));
  const svgC = twin.apStripSvg(twin.apStripGeom({ ap_position: "center" }, cells, 940, 50));
  assert.notEqual(svgL, svgC);
});

test("no cells draws nothing, not even the background", () => {
  const g = twin.apStripGeom({}, {}, 940, 50);
  assert.equal(g.bg, null);
  assert.equal(twin.apStripSvg(g), '<svg viewBox="0 0 940 50" class="gauge-svg">\n</svg>');
});
