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
// panel height less both corner radii, white text, source-coloured fill,
// old layouts untouched). It cannot assert pixel parity against the pyEfis
// widget -- that's AER-389, still in progress at the time this test was
// written; the lockstep gate (Elon) covers cross-repo parity.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity", "buildHSI"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

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
  // stub it the same way this sandbox stubs `document`.
  const build = new Function("document", `let _svgUid = 0; ${body}\nreturn buildHSI;`);
  return build(doc);
}

function render(layout, orientation, options = {}) {
  const build = makeSandbox();
  const inst = { options: { readout_layout: layout, orientation, ...options } };
  return build(inst).outerHTML;
}

// Panel rect: `<rect x="P" y="P" width="P" height="P" rx="1.4" fill="rgba(0,0,0,0.62)" ...>`
// (shared literal between segPanel's rect and the arc path's inline rect).
function panelRect(svg) {
  const m = svg.match(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="1\.4" fill="rgba\(0,0,0,0\.62\)"/);
  assert.ok(m, "expected a top_panel readout panel rect in the SVG");
  return { x: +m[1], y: +m[2], w: +m[3], h: +m[4] };
}

// Tab: a filled path with a left-side rounded corner (the "A" arc command)
// immediately followed by the white centred "GPS" text.
function tabMarkup(svg) {
  const m = svg.match(/<path d="([^"]*A[^"]*)" fill="(#[0-9a-f]+)"\/><text x="([\d.]+)" y="([\d.]+)" fill="(#[0-9a-f]+)" font-size="7" text-anchor="middle">GPS<\/text>/);
  return m && { d: m[1], fill: m[2], textX: +m[3], textY: +m[4], textFill: m[5] };
}

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

    // Fill is the active source colour; text is white, not the source colour.
    assert.equal(tab.fill, "#ff00ff", "default source is GPS -- magenta");
    assert.equal(tab.textFill, "#fff");
  });

  test(`${tag} HSI: top_panel tab fill follows a VLOC source colour (green)`, () => {
    const svg = render("top_panel", orientation, { source_auto_color: false, needle_color: "#00ff00" });
    const tab = tabMarkup(svg);
    assert.ok(tab);
    assert.equal(tab.fill, "#00ff00");
  });

  for (const layout of ["corners", "split", "none"]) {
    test(`${tag} HSI: ${layout} layout keeps the plain corner label, no tab`, () => {
      const svg = render(layout, orientation);
      assert.equal(tabMarkup(svg), null, `${layout} draws no readout panel, so there is nothing for a tab to hang off of`);
      assert.match(svg, />GPS<\/text>/, "the old plain source-coloured label should still render");
    });
  }
}
