// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6348 (ANN2): the annunciator twin against what the REAL pyEfis
// annunciator (pyEfis #551) decides and paints. The expected values are not
// hand-written: they are dumped by tools/annunciator_oracle.py into
// test/fixtures/annunciator_pyefis.json (regenerate with the command in that
// script's header; QT_BOLD_METRICS comes from the same fixture through
// tools/qt_bold_metrics.py). The schema the twin reads (the instrument's
// options and the `annunciator` block) is the one the oracle's pyEfis built.
// This test runs the REAL editor functions against them.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["QT_BOLD_METRICS", "ANN_STALE_COLOR", "ANN_REVERSE_TEXT", "ANN_QUALITY_FLAGS"];
const FUNCS = ["apColor", "apEsc", "apRectSvg", "qtBoldFont", "annIsMap", "annSpec", "annLevel",
  "annAlertBound", "annPreviewTile", "annInt", "annFloat", "annGrid", "annRects", "annFitPx",
  "annGeom", "annSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { qtBoldFont, annSpec, annPreviewTile, annGrid, annRects, annGeom, annSvg };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/annunciator_pyefis.json", import.meta.url), "utf8"));
const META = FIXTURE.schema.instrument;
const RULE = FIXTURE.schema.annunciator;

const close = (a, b, what, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what) => { for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`); };
const rgba = ([css, a]) => [1, 3, 5].map((i) => parseInt(css.slice(i, i + 2), 16)).concat(Math.round(a * 255));
const label = (c) => `${c.w}x${c.h} ${JSON.stringify(c.states)} ${JSON.stringify(c.options).slice(0, 160)}`;
const tilesOf = (o) => (Array.isArray(o.tiles) ? o.tiles : []);

test("the shared font table is Qt's metrics over printable ASCII, value for value", () => {
  const M = FIXTURE.metrics, cols = M.columns;
  assert.equal(M.chars.length, 95);
  for (const [fam, t] of Object.entries(M.families)) {
    for (const r of t.rows) {
      const f = twin.qtBoldFont(fam, r[0]);
      assert.equal(f.ascent, r[cols.indexOf("ascent")], `${fam} ${r[0]}px ascent`);
      assert.equal(f.descent, r[cols.indexOf("descent")], `${fam} ${r[0]}px descent`);
      for (const ch of M.chars) assert.equal(f.advance(ch), r[cols.indexOf(ch)], `${fam} ${r[0]}px "${ch}"`);
    }
  }
});

test("the fixture covers every preview state, level, binding kind and the level rule", () => {
  const seen = new Set();
  for (const c of FIXTURE.cases) {
    for (const s of c.states) seen.add("state:" + s);
    for (const t of c.tiles) if (t) seen.add("level:" + t.level + (t.stale ? ":stale" : ""));
  }
  for (const s of ["lit", "off", "stale"]) assert.ok(seen.has("state:" + s), s);
  for (const l of RULE.levels) assert.ok(seen.has("level:" + l), l);
  // Red and amber appear only on an annunciate-flag tile.
  for (const c of FIXTURE.cases) {
    tilesOf(c.options).forEach((e, i) => {
      const t = c.tiles[i];
      if (t && RULE.alert_levels.includes(t.level)) assert.equal(e.flag, "annunciate", label(c));
    });
  }
  assert.ok(FIXTURE.cases.length >= 100, `${FIXTURE.cases.length} cases`);
});

for (const c of FIXTURE.cases) {
  test(`matches pyEfis annunciator: ${label(c)}`, () => {
    const o = c.options;
    const n = tilesOf(o).length;
    // What the model decided for each tile in its preview state.
    const decided = tilesOf(o).map((e, i) => twin.annPreviewTile(twin.annSpec(e, RULE), c.states[i]));
    assert.deepEqual(decided, c.tiles.map((t) => t && { text: t.text, level: t.level, stale: t.stale }), "tiles");
    for (const t of c.tiles) if (t) assert.ok(!t.boxed && !t.flash, "no flash in a preview");

    assert.deepEqual(twin.annGrid(o, n), c.grid, "grid");
    const rects = twin.annRects(o, n, c.w, c.h);
    assert.equal(rects.length, c.rects.length);
    rects.forEach((r, i) => (r === null ? assert.equal(c.rects[i], null) : closeRect(r, c.rects[i], `rect ${i}`)));

    const g = twin.annGeom(o, META, RULE, c.states, c.w, c.h);
    if (!n) {   // the device paints nothing; the canvas shows the sample (below)
      assert.deepEqual(c.ops, []);
      assert.ok(g.sample);
      return;
    }
    const ops = c.ops.slice();
    if (n && c.tiles.some((t) => t)) {
      const fill = ops.findIndex((x) => x.op === "fill_rect");
      if (g.bg) {
        assert.equal(fill, 0, "the block fill comes first");
        const f = ops.shift();
        assert.deepEqual(f.rect, { x: 0, y: 0, w: c.w, h: c.h });
        assert.deepEqual(rgba(g.bg).slice(0, 3), f.rgba.slice(0, 3), "fill colour");
        close(g.bg[1], f.alpha, "fill alpha", 1e-4);
      } else {
        assert.equal(fill, -1, "no fill");
      }
    } else {
      assert.equal(g.bg, null);
      assert.deepEqual(ops, [], "an empty block paints nothing");
    }
    let k = 0;
    for (const t of g.tiles) {
      const box = ops[k++];
      assert.equal(box.op, "rect", `${k}: a tile box`);
      closeRect(t.box, box.rect, "box");
      assert.deepEqual(rgba(t.color), box.pen.rgba, "outline colour");
      close(t.width, box.pen.width, "outline width");
      assert.equal(box.pen.style, t.stale ? "DashLine" : "SolidLine", "outline style");
      assert.equal(`${t.cap}Cap`, box.pen.cap.replace("Square", "square").replace("Flat", "butt").replace("Round", "round"), "cap");
      assert.equal(`${t.join}Join`, box.pen.join.replace("Bevel", "bevel").replace("Miter", "miter").replace("Round", "round"), "join");
      if (t.stale) assert.deepEqual(box.pen.dashes, [4, 2]);
      assert.equal(box.brush.style, t.fill ? "SolidPattern" : "NoBrush", "fill");
      if (t.fill) assert.deepEqual(rgba(t.color), box.brush.rgba, "fill colour");
      close(t.radius, box.rx, "corner radius");
      if (!t.text) continue;
      const txt = ops[k++];
      assert.equal(txt.op, "text", "a caption");
      assert.equal(t.lines.map((l) => l.text).join("\n"), txt.text);
      closeRect(t.box, txt.rect, "caption box");
      assert.equal(t.px, txt.pixel_size, `"${txt.text}" px`);
      assert.equal(t.family, txt.family);
      assert.ok(txt.bold);
      assert.deepEqual(rgba(t.textColor), txt.pen.rgba, "caption colour");
      close(t.lines[0].baseline, txt.laid_out.y + txt.ascent, "first baseline");
      close(t.lines.length * (txt.ascent + txt.descent), txt.laid_out.h, "line stack");
    }
    assert.equal(k, ops.length, "every paint op accounted for");
  });
}

test("with no tiles the canvas shows the schema's sample; the device paints nothing", () => {
  const empty = FIXTURE.cases.find((c) => Array.isArray(c.options.tiles) && !c.options.tiles.length);
  assert.deepEqual(empty.ops, []);
  const g = twin.annGeom({}, META, RULE, [], 330, 120);
  assert.ok(g.sample);
  assert.deepEqual(g.tiles.map((t) => t.text), META.preview.tiles.map((t) => t.text));
});

test("the SVG carries each tile's box, dash and captions", () => {
  const c = FIXTURE.cases.find((x) => x.states[0] === "stale" && x.w === 330 && x.options.tiles?.[0]?.label === "AP");
  const svg = twin.annSvg(twin.annGeom(c.options, META, RULE, c.states, c.w, c.h));
  assert.equal((svg.match(/stroke-dasharray="8 4"/g) || []).length, 6);
  assert.match(svg, />ALT ALERT</);
  const lit = FIXTURE.cases.find((x) => x.states[0] === "lit" && x.w === 330 && x.options.tiles?.[0]?.label === "AP");
  const s2 = twin.annSvg(twin.annGeom(lit.options, META, RULE, lit.states, lit.w, lit.h));
  assert.match(s2, /fill="#ffaa00"/, "caution is reverse video");
  assert.match(s2, />VLOC1</, "the enum shows its first mapped text");
});
