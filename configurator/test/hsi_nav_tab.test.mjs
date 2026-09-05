// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-390 (configurator twin of pyEfis#140 / AER-388/389, lockstep): the
// HSI's nav-source annunciation moved from a floating top-left label to a
// coloured tab hanging off the HDG|MAG|CRS panel's left edge, in
// `top_panel` layout only. Runs the REAL buildHSI() out of editor.html
// (brace-matched, not reimplemented -- see test/support/extract.mjs), so
// this fails the moment the twin's geometry drifts from what's asserted
// here without the assertions being updated deliberately.
//
// This pins the twin's OWN invariants (flush right edge, tab height ==
// panel height less both corner radii, dark-tint derived label clearing the
// WCAG floor, source-coloured fill, constant width, old layouts untouched).
// It cannot assert pixel parity against the pyEfis widget -- that's AER-389,
// still in progress at the time this test was written; the lockstep gate
// (Elon) covers cross-repo parity.
//
// AER-474 (lockstep with AER-473/pyEfis#147+#148): the label used to be
// hardcoded white (1.37:1 on stock green / 3.14:1 on magenta, both WCAG AA
// fails) and the width tracked the label's own glyph count. The label is now
// darkenToContrast(fill) -- ported 1:1 from helpers.darken_to_contrast -- and
// the width is pinned to the widest of SOURCE_TAB_LABELS regardless of which
// label is drawn.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

// darkenToContrast + its helpers (AER-474, lockstep with AER-473/pyEfis#147):
// buildHSI's tab draw calls into these for the label colour, so they must be
// pulled into the sandbox alongside buildHSI itself.
const CONSTS = ["SOURCE_LABEL_MIN_CONTRAST"];
const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity",
  "normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance", "contrastRatio",
  "_rgbToHsv", "_hsvToRgb", "darkenToContrast", "buildHSI"];
const body = CONSTS.map((c) => extractConst(editorSource(), c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

// svgFromString does `t.innerHTML = s.trim(); return t.content.firstChild;`
// -- stub just enough of that contract to get the rendered markup back out
// without a real DOM/jsdom dependency.
function makeSandbox() {
  const doc = {
    createElement() {
      const t = {};
      Object.defineProperty(t, "innerHTML", { set(v) { t._html = v; } });
      Object.defineProperty(t, "content", { get() { return { firstChild: { outerHTML: t._html } }; } });
      return t;
    },
  };
  // buildHSI reaches out to the module-level `_svgUid` counter (AER-393,
  // shared with the other builders for unique per-instance def ids) --
  // stub it the same way this sandbox stubs `document`. Also exposes
  // darkenToContrast/contrastRatio/SOURCE_LABEL_MIN_CONTRAST (AER-474) so
  // tests can independently reproduce the label derivation, not just render
  // through buildHSI.
  const build = new Function("document",
    `let _svgUid = 0; ${body}\nreturn { buildHSI, darkenToContrast, contrastRatio, SOURCE_LABEL_MIN_CONTRAST };`);
  return build(doc);
}

const { darkenToContrast, contrastRatio, SOURCE_LABEL_MIN_CONTRAST } = makeSandbox();

function render(layout, orientation, options = {}) {
  const { buildHSI } = makeSandbox();
  const inst = { options: { readout_layout: layout, orientation, ...options } };
  return buildHSI(inst).outerHTML;
}

// Panel rect: `<rect x="P" y="P" width="P" height="P" rx="1.4" fill="rgba(0,0,0,0.62)" ...>`
// (shared literal between segPanel's rect and the arc path's inline rect).
function panelRect(svg) {
  const m = svg.match(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="1\.4" fill="rgba\(0,0,0,0\.62\)"/);
  assert.ok(m, "expected a top_panel readout panel rect in the SVG");
  return { x: +m[1], y: +m[2], w: +m[3], h: +m[4] };
}

// Tab: a filled path with a left-side rounded corner (the "A" arc command)
// immediately followed by the centred "GPS" text (colour now derived, not
// fixed).
function tabMarkup(svg) {
  const m = svg.match(/<path d="([^"]*A[^"]*)" fill="(#[0-9a-f]+)"\/><text x="([\d.]+)" y="([\d.]+)" fill="(#[0-9a-f]+)" font-size="7" text-anchor="middle">GPS<\/text>/);
  if (!m) return null;
  // x0 (the tab's left/far edge) is the target of the path's first rounded-
  // corner arc: "...A${r} ${r} 0 0 1 ${x0} ${y1 - r}..." -- pull it out so
  // callers can derive the tab's actual width (panel.x - x0) without
  // re-parsing the whole path themselves.
  const x0Match = m[1].match(/A[\d.]+ [\d.]+ 0 0 1 (-?[\d.]+) /);
  return {
    d: m[1], fill: m[2], textX: +m[3], textY: +m[4], textFill: m[5],
    x0: x0Match ? +x0Match[1] : null,
  };
}

// The exact candidate set the widget measures the constant width against
// (hsi._SOURCE_TAB_LABELS / pyEfis#148) -- extracted from editor.html so this
// tracks the real source rather than a hand-copied duplicate.
const SOURCE_TAB_LABELS = new Function(
  `${extractConst(editorSource(), "SOURCE_TAB_LABELS")}\nreturn SOURCE_TAB_LABELS;`,
)();

for (const orientation of [undefined, "arc"]) {
  const tag = orientation === "arc" ? "arc" : "rose";

  test(`${tag} HSI: top_panel renders the nav-source tab flush against the panel's left edge`, () => {
    const svg = render("top_panel", orientation);
    const panel = panelRect(svg);
    const tab = tabMarkup(svg);
    assert.ok(tab, "expected a rounded-left tab path + white GPS text");

    // Right edge of the tab is flush with the panel's left edge (px) --
    // the two L-commands that draw the tab's square right side both land
    // on panel.x, e.g. "...L28 3.32 L28 12.68...".
    const rightEdgeCount = (tab.d.match(new RegExp(`L${panel.x} `, "g")) || []).length;
    assert.equal(rightEdgeCount, 2, `both right-edge vertices of the tab should sit at the panel's left edge (x=${panel.x})`);

    // Tab height is the panel height less both corner radii (rr = 1.4, the
    // same rx the panel rect itself uses) -- top edge at the bottom of the
    // panel's top-left arc, bottom edge at the top of the bottom-left arc.
    const rr = 1.4;
    const ys = [...tab.d.matchAll(/[ML]-?[\d.]+ (-?[\d.]+)/g)].map((m) => +m[1]);
    const top = Math.min(...ys), bottom = Math.max(...ys);
    assert.ok(Math.abs(top - (panel.y + rr)) < 1e-6, "tab top should be panel.y + rr");
    assert.ok(Math.abs(bottom - (panel.y + panel.h - rr)) < 1e-6, "tab bottom should be panel.y + panel.h - rr");

    // Fill is the active source colour, full brightness (HSI-COLOR-001).
    // Label is a darkened tint of that SAME fill (pyEfis#147/AER-473), not
    // hardcoded white -- verify both the exact derived hex (independently
    // reproduced here) and that it clears the WCAG floor against the fill.
    assert.equal(tab.fill, "#ff00ff", "default source is GPS -- magenta");
    assert.equal(tab.textFill, darkenToContrast(tab.fill));
    assert.notEqual(tab.textFill, "#fff", "no longer hardcoded white");
    assert.ok(contrastRatio(tab.textFill, tab.fill) >= SOURCE_LABEL_MIN_CONTRAST);
  });

  test(`${tag} HSI: top_panel tab fill follows a VLOC source colour (green)`, () => {
    const svg = render("top_panel", orientation, { source_auto_color: false, needle_color: "#00ff00" });
    const tab = tabMarkup(svg);
    assert.ok(tab);
    assert.equal(tab.fill, "#00ff00");
    assert.equal(tab.textFill, darkenToContrast(tab.fill));
  });

  test(`${tag} HSI: top_panel tab label colour tracks a custom source colour`, () => {
    // vloc_color/course_color are config-selectable (pyEfis#147 anchor #1) --
    // the derivation must be parametric, not two hardcoded dark hexes.
    const svg = render("top_panel", orientation, { source_auto_color: false, needle_color: "#00ccff" });
    const tab = tabMarkup(svg);
    assert.ok(tab);
    assert.equal(tab.fill, "#00ccff");
    assert.ok(contrastRatio(tab.textFill, tab.fill) >= SOURCE_LABEL_MIN_CONTRAST);
  });

  test(`${tag} HSI: top_panel tab width is pinned to the widest SOURCE_TAB_LABELS candidate`, () => {
    // The call sites always draw the "GPS" sample label, but the width must
    // not track it (pyEfis#148/AER-473) -- it's the widest of
    // SOURCE_TAB_LABELS at navTab's own glyph-width approximation, so the
    // tab wouldn't breathe if a future preview ever cycled the label.
    const svg = render("top_panel", orientation);
    const panel = panelRect(svg);
    const tab = tabMarkup(svg);
    assert.ok(tab && tab.x0 != null, "expected to recover the tab's left edge (x0) from its path");
    const rr = 1.4;
    const hpad = rr;
    const expectedWidth = Math.max(...SOURCE_TAB_LABELS.map((l) => l.length * 4.6)) + 2 * hpad;
    const actualWidth = panel.x - tab.x0;
    assert.ok(Math.abs(actualWidth - expectedWidth) < 1e-6,
      `expected width ${expectedWidth} (widest of ${SOURCE_TAB_LABELS}), got ${actualWidth}`);
  });

  for (const layout of ["corners", "split", "none"]) {
    test(`${tag} HSI: ${layout} layout keeps the plain corner label, no tab`, () => {
      const svg = render(layout, orientation);
      assert.equal(tabMarkup(svg), null, `${layout} draws no readout panel, so there is nothing for a tab to hang off of`);
      assert.match(svg, />GPS<\/text>/, "the old plain source-coloured label should still render");
    });
  }
}
