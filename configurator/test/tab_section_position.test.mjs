// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-665: tabStripGeom()/tabContentGeom() are the JS port of
// tab_section/__init__.py _layout_pages' bar_geom/stack_geom -- the function
// that decides where the tab bar sits and how much of the container's box is
// left for the active tab's content. This pins the port down numerically
// against the same four tab_position values the device supports, so the
// "Phase B must not hardcode bar-on-top" requirement (#137) has a permanent
// regression guard even without a browser to screenshot against.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs), same technique as
// tab_section_roundtrip.test.mjs.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const FUNCS = ["tabPosition", "tabStripGeom", "tabContentGeom"];
const body = extractConst(src, "TAB_BAR_THICKNESS") + "\n\n"
  + FUNCS.map((f) => extractFunction(src, f)).join("\n\n");
const { tabPosition, tabStripGeom, tabContentGeom } = new Function(`
  ${body}
  return { tabPosition, tabStripGeom, tabContentGeom };
`)();

const BOX = { x: 0, y: 0, w: 200, h: 100 };
const THICKNESS = 22; // must track TAB_BAR_THICKNESS in editor.html

test("tabPosition defaults to top for unset/unrecognised values", () => {
  assert.equal(tabPosition({ options: {} }), "top");
  assert.equal(tabPosition({ options: { tab_position: "sideways" } }), "top");
  assert.equal(tabPosition({}), "top");
  for (const p of ["top", "bottom", "left", "right"]) {
    assert.equal(tabPosition({ options: { tab_position: p } }), p);
  }
});

test("top: strip spans the width at y=0; content is everything below it", () => {
  const strip = tabStripGeom(BOX, "top");
  const content = tabContentGeom(BOX, "top");
  assert.deepEqual(strip, { x: 0, y: 0, w: 200, h: THICKNESS });
  assert.deepEqual(content, { x: 0, y: THICKNESS, w: 200, h: 100 - THICKNESS });
});

test("bottom: strip spans the width at the bottom edge; content is everything above it", () => {
  const strip = tabStripGeom(BOX, "bottom");
  const content = tabContentGeom(BOX, "bottom");
  assert.deepEqual(strip, { x: 0, y: 100 - THICKNESS, w: 200, h: THICKNESS });
  assert.deepEqual(content, { x: 0, y: 0, w: 200, h: 100 - THICKNESS });
});

test("left: strip spans the height at x=0; content is everything to its right", () => {
  const strip = tabStripGeom(BOX, "left");
  const content = tabContentGeom(BOX, "left");
  assert.deepEqual(strip, { x: 0, y: 0, w: THICKNESS, h: 100 });
  assert.deepEqual(content, { x: THICKNESS, y: 0, w: 200 - THICKNESS, h: 100 });
});

test("right: strip spans the height at the right edge; content is everything to its left", () => {
  const strip = tabStripGeom(BOX, "right");
  const content = tabContentGeom(BOX, "right");
  assert.deepEqual(strip, { x: 200 - THICKNESS, y: 0, w: THICKNESS, h: 100 });
  assert.deepEqual(content, { x: 0, y: 0, w: 200 - THICKNESS, h: 100 });
});

test("strip + content never overlap and always exactly tile the box, on every edge", () => {
  for (const position of ["top", "bottom", "left", "right"]) {
    const strip = tabStripGeom(BOX, position);
    const content = tabContentGeom(BOX, position);
    if (position === "top" || position === "bottom") {
      assert.equal(strip.h + content.h, BOX.h);
      assert.equal(strip.w, BOX.w);
      assert.equal(content.w, BOX.w);
    } else {
      assert.equal(strip.w + content.w, BOX.w);
      assert.equal(strip.h, BOX.h);
      assert.equal(content.h, BOX.h);
    }
  }
});

test("degenerate box (thinner than the bar) clamps to zero, never negative", () => {
  const tiny = { x: 0, y: 0, w: 10, h: 10 };
  for (const position of ["top", "bottom", "left", "right"]) {
    const content = tabContentGeom(tiny, position);
    assert.ok(content.w >= 0 && content.h >= 0, `${position}: negative content size`);
  }
});
