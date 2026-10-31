// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4374: the common Font size (font_percent) on the tab_section and button
// twins, lockstep with pyEfis #356 (helpers.font_scale, tab_section's
// font_percent setter, Button.setStyle). Font size is a multiplier on the
// widget's own built-in text size; unset means today's size exactly.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const FUNCS = ["fontScale", "tabLabelPx", "tabBarThickness", "tabStripGeom", "tabContentGeom", "buildButton"];
const body = ["TAB_BAR_THICKNESS", "TAB_LABEL_PX", "TAB_MIN_LABEL_PX"]
  .map((c) => extractConst(src, c)).join("\n") + "\n\n"
  + FUNCS.map((f) => extractFunction(src, f)).join("\n\n");

// buildButton needs only createElement and rect(); a style bag is enough.
const document = { createElement: () => ({ style: {} }) };
const H = 80;
const rect = () => ({ x: 0, y: 0, w: 160, h: H });
const { fontScale, tabLabelPx, tabBarThickness, tabStripGeom, tabContentGeom, buildButton } =
  new Function("document", "rect", `
    ${body}
    return { fontScale, tabLabelPx, tabBarThickness, tabStripGeom, tabContentGeom, buildButton };
  `)(document, rect);

const fp = (v) => ({ options: v === undefined ? {} : { font_percent: v } });

test("fontScale normalises raw YAML like normalize_font_percent + font_scale", () => {
  assert.equal(fontScale(undefined), 1);
  assert.equal(fontScale(null), 1);
  assert.equal(fontScale(""), 1);
  assert.equal(fontScale(0), 1);
  assert.equal(fontScale(-50), 1);
  assert.equal(fontScale("abc"), 1);
  assert.equal(fontScale(1), 1);
  assert.equal(fontScale(100), 1);
  assert.equal(fontScale(150), 1.5);
  assert.equal(fontScale("80"), 0.8);
  assert.equal(fontScale(0.2), 0.2);
  // A hand-written 1.5 is > 1, so it reads as a whole-number percent (1.5%),
  // exactly as on the device, where it lands on the 8 px floor.
  assert.equal(fontScale(1.5), 0.015);
});

test("tab label: unset (and an explicit 1.0) leave the inherited CSS size alone", () => {
  assert.equal(tabLabelPx(fp()), null);
  assert.equal(tabLabelPx({}), null);
  assert.equal(tabLabelPx(fp(null)), null);
  assert.equal(tabLabelPx(fp(1)), null);
  assert.equal(tabLabelPx(fp(100)), null);
});

test("tab label scales the 11 px chip, floored at the device's 8 px", () => {
  assert.equal(tabLabelPx(fp(150)), 17);
  assert.equal(tabLabelPx(fp(200)), 22);
  assert.equal(tabLabelPx(fp(0.9)), 10);
  assert.equal(tabLabelPx(fp(0.2)), 8);   // Bill's bench panel: floor, not 2 px
  assert.equal(tabLabelPx(fp(1.5)), 8);   // 1.5% -> floor, as on the device
});

test("tab bar thickness is unchanged when unset", () => {
  assert.equal(tabBarThickness(fp()), 22);
  assert.equal(tabBarThickness(fp(1)), 22);
});

test("tab bar grows and shrinks by the label's change, as QTabBar.sizeHint does", () => {
  // Device (offscreen render of pyEfis #356): bar = label px + a fixed pad at
  // every size (8 -> 20, 12 -> 24, 18 -> 30, 36 -> 48).
  for (const v of [0.2, 0.9, 120, 150, 200, 300]) {
    assert.equal(tabBarThickness(fp(v)) - tabLabelPx(fp(v)), 22 - 11, `font_percent ${v}`);
  }
});

test("scaled strip + content still tile the box on every edge", () => {
  const box = { x: 0, y: 0, w: 320, h: 160 };
  for (const v of [0.2, 150, 300]) {
    const t = tabBarThickness(fp(v));
    for (const position of ["top", "bottom", "left", "right"]) {
      const strip = tabStripGeom(box, position, t);
      const content = tabContentGeom(box, position, t);
      if (position === "top" || position === "bottom") {
        assert.equal(strip.h, t);
        assert.equal(strip.h + content.h, box.h);
      } else {
        assert.equal(strip.w, t);
        assert.equal(strip.w + content.w, box.w);
      }
    }
  }
});

test("button label: unset is today's size, Font size multiplies it", () => {
  const size = (opts) => buildButton({ options: { text: "Units", ...opts } }).style.fontSize;
  assert.equal(size({}), 0.45 * H + "px");
  assert.equal(size({ font_percent: null }), 0.45 * H + "px");
  assert.equal(size({ font_percent: 0.5 }), 0.45 * H * 0.5 + "px");
  assert.equal(size({ font_percent: 150 }), 0.45 * H * 1.5 + "px");
  assert.equal(size({ font_percent: 0.1 }), "8px");   // the twin's own 8 px floor
  // Two-line labels keep the per-line split, scaled the same way.
  assert.equal(size({ text: "A\\nB", font_percent: 0.5 }), (0.45 / 2) * H * 0.5 + "px");
});
