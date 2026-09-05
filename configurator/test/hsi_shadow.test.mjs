// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-393 (configurator twin of pyEfis#142, lockstep with AER-392): the
// HSI's static-element drop shadow -- rose disc + HDG|MAG|CRS readout panel
// -- reproduced in the editor preview, config-gated and default off, mirroring
// AER-392's shipped per-layer decision (rose: Option 2 / CSS drop-shadow,
// zero offset; panel: Option 4 / a punched blurred silhouette, mirroring the
// AER-415 fix so the halo does not tint the panel's own translucent fill).
// Runs the REAL buildHSI() out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs), so this fails the moment
// the twin's shadow markup drifts from what's asserted here without the
// assertions being updated deliberately.

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

function makeSandbox() {
  const doc = {
    createElement() {
      const t = {};
      Object.defineProperty(t, "innerHTML", { set(v) { t._html = v; } });
      Object.defineProperty(t, "content", { get() { return { firstChild: { outerHTML: t._html } }; } });
      return t;
    },
  };
  // buildHSI reaches out to the module-level `_svgUid` counter (shared with
  // the other builders, for unique per-instance def ids) -- stub it the same
  // way the sandbox stubs `document`.
  const build = new Function("document", `let _svgUid = 0; ${body}\nreturn buildHSI;`);
  return build(doc);
}

function render(layout, orientation, options = {}) {
  const build = makeSandbox();
  const inst = { options: { readout_layout: layout, orientation, ...options } };
  return build(inst).outerHTML;
}

function roseDiscMarkup(svg) {
  const m = svg.match(/<circle cx="[\d.]+" cy="[\d.]+" r="[\d.]+" fill="[^"]*" stroke="[^"]*" stroke-width="0\.8"([^/]*)\/>/);
  return m && m[1];
}

function panelShadowFilter(svg) {
  const m = svg.match(/<filter id="(hsiPanelShadow\d+)"[^>]*>(.*?)<\/filter>/s);
  return m && { id: m[1], body: m[2] };
}

for (const orientation of [undefined, "arc"]) {
  const tag = orientation === "arc" ? "arc" : "rose";

  test(`${tag} HSI: shadow off by default -- no filter/drop-shadow markup at all`, () => {
    const svg = render("top_panel", orientation);
    assert.ok(!svg.includes("drop-shadow"), "no CSS drop-shadow should appear when shadow_enabled is unset");
    assert.ok(!svg.includes("hsiPanelShadow"), "no panel-shadow filter should appear when shadow_enabled is unset");
  });

  test(`${tag} HSI: shadow_enabled=false is explicitly off too`, () => {
    const svg = render("top_panel", orientation, { shadow_enabled: false });
    assert.ok(!svg.includes("drop-shadow"));
    assert.ok(!svg.includes("hsiPanelShadow"));
  });

  test(`${tag} HSI: top_panel + shadow_enabled renders the punched panel-shadow filter, ahead of the panel fill`, () => {
    const svg = render("top_panel", orientation, { shadow_enabled: true });
    const filt = panelShadowFilter(svg);
    assert.ok(filt, "expected a hsiPanelShadowN filter def");
    // Punch-out (AER-415 twin): blur the panel's own alpha, tint it, then
    // subtract the panel's own (unblurred) alpha back out -- leaves only the
    // falloff past the edge, not a solid tint under the translucent fill.
    assert.match(filt.body, /feGaussianBlur in="SourceAlpha"/);
    assert.match(filt.body, /feComposite in="shadow" in2="SourceAlpha" operator="out"/);
    // The shadow rect (referencing the filter) must appear in the markup
    // BEFORE the panel's own translucent fill rect, so the panel paints over
    // the shadow rather than under it (same z-order as _draw_readouts: tab,
    // then shadow, then panel).
    const shadowIdx = svg.indexOf(`filter="url(#${filt.id})"`);
    const panelIdx = svg.indexOf('fill="rgba(0,0,0,0.62)"');
    assert.ok(shadowIdx >= 0 && panelIdx >= 0 && shadowIdx < panelIdx,
      "panel shadow rect should precede the panel's own translucent fill");
  });

  for (const layout of ["corners", "split", "none"]) {
    test(`${tag} HSI: ${layout} layout draws no panel shadow even with shadow_enabled (no panel to shadow)`, () => {
      const svg = render(layout, orientation, { shadow_enabled: true });
      assert.ok(!svg.includes("hsiPanelShadow"), `${layout} has no readout panel, so there is nothing for a shadow to sit under`);
    });
  }
}

// Rose disc shadow (Option 2 twin) only applies to the rose orientation --
// arc mode draws no compass disc at all.
test("rose HSI: shadow_enabled applies a zero-offset CSS drop-shadow to the compass disc", () => {
  const svg = render("top_panel", undefined, { shadow_enabled: true });
  const style = roseDiscMarkup(svg);
  assert.ok(style, "expected the rose disc circle to carry extra shadow markup");
  const m = /filter: drop-shadow\(0px 0px ([\d.]+)px rgba\(0,0,0,0\.6\)\)/.exec(style);
  assert.ok(m, `expected a zero-offset rgba(0,0,0,0.6) drop-shadow, got: ${style}`);
  assert.ok(+m[1] > 0, "blur radius should be positive when shadows are enabled");
});

test("rose HSI: shadow_enabled shrinks the rose radius to reserve halo clearance", () => {
  const on = render("top_panel", undefined, { shadow_enabled: true });
  const off = render("top_panel", undefined, { shadow_enabled: false });
  const rOf = (svg) => +/<circle cx="[\d.]+" cy="[\d.]+" r="([\d.]+)"/.exec(svg)[1];
  assert.ok(rOf(on) < rOf(off), "the rose should be smaller with shadows on, reserving room for the halo to resolve");
});

test("arc HSI: shadow_enabled draws no rose-disc shadow -- arc mode has no compass disc", () => {
  const svg = render("top_panel", "arc", { shadow_enabled: true });
  assert.ok(!roseDiscMarkup(svg), "arc mode should not draw a compass-disc circle at all");
  assert.ok(!svg.includes("drop-shadow"), "no rose-disc drop-shadow without a disc to attach it to");
  // The panel shadow (Option 4) is unaffected -- still present.
  assert.ok(svg.includes("hsiPanelShadow"));
});
