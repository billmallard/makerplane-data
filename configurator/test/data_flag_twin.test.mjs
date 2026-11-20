// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5654: the DATA flag (`data_annunciation`) twin, in the amber state of
// pyEfis data_status.DataAnnunciation. The expected values are not
// hand-written: they are what the REAL widget paints, dumped by
// tools/data_flag_oracle.py into test/fixtures/data_flag_pyefis.json
// (regenerate with the command in that script's header). This test runs the
// REAL editor functions against them (see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const { dataFlagGeom, dataFlagSvg } = new Function(`
  ${["DATA_FLAG_AMBER", "DEJAVU_ASCENT"].map((c) => extractConst(src, c)).join("\n")}
  ${["fontScale", "dataFlagGeom", "dataFlagSvg"].map((f) => extractFunction(src, f)).join("\n\n")}
  return { dataFlagGeom, dataFlagSvg };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/data_flag_pyefis.json", import.meta.url), "utf8"));

const close = (a, b, what) =>
  assert.ok(Math.abs(a - b) < 1e-9, `${what}: twin ${a} vs pyEfis ${b}`);
const rgb = (rgba) => `rgb(${rgba[0]},${rgba[1]},${rgba[2]})`;
const attrs = (tag) => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map((m) => [m[1], m[2]]));

const amber = FIXTURE.cases.filter((c) => c.severity === "amber");

test("the fixture covers the amber state and the hidden one", () => {
  assert.ok(amber.length >= 40, `${amber.length} amber cases`);
  const hidden = FIXTURE.cases.filter((c) => c.severity === "none");
  assert.equal(hidden.length, 1);
  // Every pack current: the widget paints nothing. The canvas still shows the
  // amber state, on purpose (the PR's open point).
  assert.deepEqual(hidden[0].ops, []);
});

for (const c of amber) {
  test(`matches pyEfis DataAnnunciation (amber): ${JSON.stringify(c.options)} @ ${c.w}x${c.h}`, () => {
    assert.deepEqual(c.ops.map((op) => op.op), ["rounded_rect", "text"]);
    const [box, txt] = c.ops;
    const g = dataFlagGeom(c.options, c.w, c.h);

    // The outline: a 1 px solid amber stroke on QRectF(1, 1, w-2, h-2), r 3,
    // antialiased, no fill.
    for (const k of ["x", "y", "w", "h"]) close(g.rect[k], box.rect[k], `rect.${k}`);
    close(g.radius, box.rx, "rx");
    close(g.radius, box.ry, "ry");
    assert.equal(box.pen.style, "SolidLine");
    assert.equal(box.pen.cosmetic, false, "a 1 px stroke in widget px");
    close(g.strokeW, box.pen.width, "stroke width");
    assert.equal(g.color, rgb(box.pen.rgba), "outline colour");
    assert.equal(box.pen.rgba[3], 255, "opaque");
    assert.equal(box.brush, "NoBrush");
    assert.ok(box.antialias);

    // The text: "DATA" in the same colour, centred in the same rect.
    assert.equal(g.text, txt.text);
    assert.ok(txt.align_center);
    for (const k of ["x", "y", "w", "h"]) close(g.rect[k], txt.rect[k], `text rect.${k}`);
    assert.equal(g.color, rgb(txt.pen.rgba), "text colour");
    assert.equal(g.px, txt.pixel_size, "font px");
    assert.equal(g.family, txt.family, "font family");
    assert.equal(txt.bold, false);
    close(g.cx, txt.laid_out.x + txt.laid_out.w / 2, "text centre x");
    close(g.baseline, txt.laid_out.y + txt.ascent, "baseline y");

    // The markup carries those values and nothing else.
    const svg = dataFlagSvg(g, c.w, c.h);
    const rect = attrs(svg.match(/<rect [^>]*>/)[0]);
    assert.deepEqual(
      [rect.x, rect.y, rect.width, rect.height, rect.rx, rect.ry].map(Number),
      [box.rect.x, box.rect.y, box.rect.w, box.rect.h, box.rx, box.ry]);
    assert.equal(rect.fill, "none");
    assert.equal(rect.stroke, rgb(box.pen.rgba));
    assert.equal(Number(rect["stroke-width"]), box.pen.width);
    const clip = attrs(svg.match(/<svg x=[^>]*>/)[0]);
    assert.deepEqual([clip.x, clip.y, clip.width, clip.height].map(Number),
      [txt.rect.x, txt.rect.y, txt.rect.w, txt.rect.h], "text clipped to the rect");
    assert.equal(clip.overflow, "hidden");
    const [, textTag, body] = svg.match(/(<text [^>]*>)([^<]*)<\/text>/);
    const t = attrs(textTag);
    assert.equal(body, "DATA");
    assert.equal(t["text-anchor"], "middle");
    close(Number(t.x) + Number(clip.x), txt.laid_out.x + txt.laid_out.w / 2, "markup centre x");
    close(Number(t.y) + Number(clip.y), txt.laid_out.y + txt.ascent, "markup baseline");
    assert.equal(Number(t["font-size"]), txt.pixel_size);
    assert.equal(t["font-family"], txt.family);
    assert.equal(t.fill, rgb(txt.pen.rgba));
    assert.equal((svg.match(/<(rect|text|path|circle|line|polygon)\b/g) || []).length, 2,
      "an outline and a label, nothing more");
  });
}

test("the canvas draws the flag with its twin, not the palette thumbnail", () => {
  const dispatch = extractFunction(src, "buildInstrumentTwin");
  assert.match(dispatch,
    /inst\.type === "data_annunciation"\) \{\s*el\.appendChild\(buildDataAnnunciation\(inst\)\);/);
  assert.match(extractFunction(src, "buildDataAnnunciation"), /mapDevicePx\(inst\)/,
    "drawn in device px, like the device widget");
});
