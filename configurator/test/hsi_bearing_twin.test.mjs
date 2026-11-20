// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5553 (HSIBRG1 twin of pyEfis#464): bearing pointers are bearing_color
// (cyan) whatever the source, start Off at boot, and under top_panel are
// stepped by a BRG tab on the panel's right edge. AER-5848 (HSIBRG4 twin of
// pyEfis#504): that tab is one button reading NONE at boot, the nav-source
// tab mirrored (HSI._side_tab_metrics). Runs the REAL buildHSI()
// out of editor.html (brace-matched, see test/support/extract.mjs) against
// test/fixtures/hsi_bearing_pyefis.json, written by tools/hsi_bearing_oracle.py
// from the merged widget (pyEfis#504) at 425 px (the size where the twin's 44-unit panel
// is the widget's 12.5 fontSize wide). Regenerate the fixture with that tool;
// never hand-edit it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const ORACLE = JSON.parse(readFileSync(new URL("./fixtures/hsi_bearing_pyefis.json", import.meta.url), "utf8"));
const CONSTS = ["SOURCE_LABEL_MIN_CONTRAST", "ND_ADV", "ND_KERN"];
const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity",
  "normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance", "contrastRatio",
  "_rgbToHsv", "_hsvToRgb", "darkenToContrast", "ndAdvance", "buildHSI"];
const body = CONSTS.map((c) => extractConst(editorSource(), c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

function render(options) {
  const doc = {
    createElement() {
      const t = {};
      Object.defineProperty(t, "innerHTML", { set(v) { t._html = v; } });
      Object.defineProperty(t, "content", { get() { return { firstChild: { outerHTML: t._html } }; } });
      return t;
    },
  };
  const { buildHSI } = new Function("document", `let _svgUid = 0; ${body}\nreturn { buildHSI };`)(doc);
  return buildHSI({ options: { heading_bug_enabled: true, ...options } }).outerHTML;
}

const num = (s) => Number(s);
// Every <text> as { x, y, size, weight, fill, anchor, text }.
function texts(svg) {
  return [...svg.matchAll(/<text ([^>]*)>([^<]*)<\/text>/g)].map(([, a, text]) => {
    const at = (k) => (a.match(new RegExp(`(?:^|\\s)${k}="([^"]*)"`)) || [])[1];
    return { x: num(at("x")), y: num(at("y")), size: num(at("font-size")), weight: at("font-weight"),
             fill: at("fill"), anchor: at("text-anchor"), text };
  });
}
const TAB_LABELS = ["NONE", "BRG 1", "BRG 2", "BOTH"];
const tabTexts = (svg) => texts(svg).filter((t) => TAB_LABELS.includes(t.text));
const navTabText = (svg) => texts(svg).find((t) => t.text === "GPS" && t.anchor === "middle");
const cornerTexts = (svg) => texts(svg).filter((t) => /^[12] /.test(t.text));
// The twin's panel rect (segPanel / arc inline rect share the literal).
function panel(svg) {
  const m = svg.match(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="1\.4" fill="rgba\(0,0,0,0\.62\)"/);
  return m && { x: +m[1], y: +m[2], w: +m[3], h: +m[4] };
}
// The tab's outline: the open path stroked in the bearing colour, M x0 top ... L x0 bottom.
function tabOutline(svg, color = "#00ffff") {
  const m = svg.match(new RegExp(`<path d="M([\\d.]+) ([\\d.]+) L([\\d.]+) [\\d.]+ A[^"]* L([\\d.]+) [\\d.]+ A[^"]* L[\\d.]+ ([\\d.]+)" fill="none" stroke="${color}"`));
  return m && { x0: +m[1], top: +m[2], x1: +m[4], bottom: +m[5] };
}

// The nav-source tab's span, from its filled path: x0 is the target of the
// first rounded-corner arc, the right edge is the panel's left edge.
function navTabSpan(svg) {
  const m = svg.match(/<path d="M([\d.]+) ([\d.]+) L([\d.]+) [\d.]+ L[\d.]+ ([\d.]+) [^"]*?A[\d.]+ [\d.]+ 0 0 1 ([\d.]+) [^"]*" fill="#[0-9a-f]+"\/><text [^>]*>GPS</);
  return m && { x0: +m[5], x1: +m[3], top: +m[2], bottom: +m[4] };
}

// How far the twin may sit from the widget, in viewBox units (0..100), and
// why. The twin's corner radius is its own panel's 1.4, the widget's is
// 0.145 of the panel height (1.76 here), so the tab's top and bottom sit up
// to 0.4 off (measured 0.36). Its width uses DejaVu Sans Condensed's
// unhinted advances, the widget's are hinted to whole px; measured 0.073
// under at 425 px, held to 0.2. Font sizes agree to 0.1 because the twin's
// fontSize (44/12.5) is the widget's 15 px at 425.
const TOL_EDGE = 0.4, TOL_WIDTH = 0.2, TOL_FONT = 0.1;

for (const [name, cfg] of Object.entries(ORACLE.configs)) {
  test(`${name}: BRG tab label and font match the widget at boot`, () => {
    const svg = render(cfg.options);
    const got = tabTexts(svg);
    assert.deepEqual(got.map((t) => t.text), cfg.tab_text.map((t) => t.label));
    for (const [i, t] of got.entries()) {
      assert.equal(t.fill, "#00ffff", "the NONE label is cyan");
      assert.equal(cfg.tab_text[i].bold, false);
      assert.equal(t.weight, undefined, "the tab label is not bold");
      assert.ok(Math.abs(t.size - cfg.tab_text[i].px / ORACLE.size * 100) <= TOL_FONT,
        `${t.text}: font ${t.size} vs widget ${cfg.tab_text[i].px / ORACLE.size * 100}`);
      assert.equal(t.size, navTabText(svg).size, "the BRG tab's font is the source tab's");
    }
  });

  test(`${name}: BRG tab geometry matches the widget`, () => {
    const svg = render(cfg.options);
    const out = tabOutline(svg);
    if (!cfg.tab) { assert.equal(out, null, "no pointer available, no tab"); return; }
    assert.ok(out, "expected the BRG tab outline");
    assert.match(svg, /fill="rgba\(0,0,0,0\.55\)"\/><text [^>]*>NONE</, "NONE is the panel's dark fill");
    // The widget's two tabs share one size (_side_tab_metrics).
    assert.deepEqual(cfg.tab.slice(1), cfg.source_tab.slice(1), "widget: BRG tab is the source tab's size");
    const p = panel(svg);
    assert.equal(out.x0, p.x + p.w, "the tab's square left edge is flush with the panel's right edge");
    const [, top, w, h] = cfg.tab.map((v) => v * 100);
    assert.ok(Math.abs(out.top - top) <= TOL_EDGE, `top ${out.top} vs ${top}`);
    assert.ok(Math.abs(out.bottom - (top + h)) <= TOL_EDGE, `bottom ${out.bottom} vs ${top + h}`);
    assert.ok(Math.abs((out.x1 - out.x0) - w) <= TOL_WIDTH, `width ${out.x1 - out.x0} vs ${w}`);
    // And the twin's two tabs are one size, mirrored about the panel.
    const nav = navTabSpan(svg);
    assert.ok(nav, "expected the nav-source tab");
    assert.equal(nav.x1, p.x, "the source tab's square edge is flush with the panel's left edge");
    assert.ok(Math.abs((out.x1 - out.x0) - (nav.x1 - nav.x0)) < 1e-9, "same width as the source tab");
    assert.equal(out.top, nav.top);
    assert.equal(out.bottom, nav.bottom);
    assert.doesNotMatch(svg, /<line [^>]*stroke="#00ffff" stroke-width="0.5"\/>/, "one button, no row hairline");
  });

  test(`${name}: corner labels match the widget at boot`, () => {
    const svg = render(cfg.options);
    const want = Object.entries(cfg.corner).filter(([, c]) => c.text).map(([n, c]) => ({ n, ...c }));
    const got = cornerTexts(svg);
    assert.deepEqual(got.map((t) => t.text), want.map((c) => c.text));
    for (const [i, t] of got.entries()) {
      const r = want[i].rect.map((v) => v * 100);
      assert.equal(t.fill, "#00ffff");
      assert.ok(t.y > r[1] && t.y < r[1] + r[3], `${t.text}: baseline ${t.y} outside the widget's label rect ${r}`);
      if (want[i].n === "1") assert.equal(t.x, 3);
      else { assert.equal(t.x, 97); assert.equal(t.anchor, "end"); }
    }
  });
}

test("no bearing needle is drawn at boot, in either orientation", () => {
  for (const orientation of ["heading_up", "arc"]) {
    const svg = render({ orientation, bearing1_enabled: true, bearing2_enabled: true, bearing_color: "#123456" });
    assert.doesNotMatch(svg, /stroke="#123456" stroke-width="[\d.]+" stroke-linecap="round"/, orientation);
    assert.doesNotMatch(svg, /#00ff00/, `${orientation}: nothing source-green from a bearing pointer`);
  }
});

test("bearing_color drives the tab and the corner labels, not the source colour", () => {
  const tp = render({ bearing1_enabled: true, bearing_color: "#ffaa00" });
  assert.deepEqual(tabTexts(tp).map((t) => t.fill), ["#ffaa00"]);
  assert.ok(tabOutline(tp, "#ffaa00"));
  const co = render({ readout_layout: "corners", bearing1_enabled: true, bearing2_enabled: true, bearing_color: "#ffaa00" });
  assert.deepEqual(cornerTexts(co).map((t) => t.fill), ["#ffaa00", "#ffaa00"]);
});

test("one pointer or two, the tab is one button reading NONE", () => {
  for (const opts of [{ bearing1_enabled: true }, { bearing2_enabled: true },
                      { bearing1_enabled: true, bearing2_enabled: true }]) {
    assert.deepEqual(tabTexts(render(opts)).map((t) => t.text), ["NONE"], JSON.stringify(opts));
  }
  assert.doesNotMatch(render({ bearing1_enabled: true, bearing2_enabled: true }), /BRG[12] OFF|BRG OFF/);
});
