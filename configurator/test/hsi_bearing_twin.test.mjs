// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5553 (HSIBRG1 twin of pyEfis#464): bearing pointers are bearing_color
// (cyan) whatever the source, start Off at boot, and under top_panel are
// stepped by a BRG tab on the panel's right edge. Runs the REAL buildHSI()
// out of editor.html (brace-matched, see test/support/extract.mjs) against
// test/fixtures/hsi_bearing_pyefis.json, written by tools/hsi_bearing_oracle.py
// from the merged widget at 425 px (the size where the twin's 44-unit panel
// is the widget's 12.5 fontSize wide). Regenerate the fixture with that tool;
// never hand-edit it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const ORACLE = JSON.parse(readFileSync(new URL("./fixtures/hsi_bearing_pyefis.json", import.meta.url), "utf8"));
const CONSTS = ["SOURCE_LABEL_MIN_CONTRAST"];
const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity",
  "normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance", "contrastRatio",
  "_rgbToHsv", "_hsvToRgb", "darkenToContrast", "buildHSI"];
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
const tabTexts = (svg) => texts(svg).filter((t) => t.text.startsWith("BRG"));
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

// How far the twin may sit from the widget, in viewBox units (0..100), and
// why. The twin's corner radius is its own panel's 1.4, the widget's is
// 0.145 of the panel height (1.76 here), so the tab's top and bottom sit up
// to 0.4 off (measured 0.36). Its glyph widths are navTab's per-character
// unit, not hinted DejaVu advances; measured 0.18 off at the right edge, held
// to 0.5. Font sizes agree to
// 0.1 because the twin's fontSize (44/12.5) is the widget's 15 px at 425.
const TOL_EDGE = 0.4, TOL_RIGHT = 0.5, TOL_FONT = 0.1;

for (const [name, cfg] of Object.entries(ORACLE.configs)) {
  test(`${name}: BRG tab labels and font match the widget at boot`, () => {
    const svg = render(cfg.options);
    const got = tabTexts(svg);
    assert.deepEqual(got.map((t) => t.text), cfg.tab_text.map((t) => t.label));
    for (const [i, t] of got.entries()) {
      assert.equal(t.fill, "#00ffff", "an Off row's label is cyan");
      assert.equal(t.weight, "700", "the tab label is bold");
      assert.ok(Math.abs(t.size - cfg.tab_text[i].px / ORACLE.size * 100) <= TOL_FONT,
        `${t.text}: font ${t.size} vs widget ${cfg.tab_text[i].px / ORACLE.size * 100}`);
    }
  });

  test(`${name}: BRG tab geometry matches the widget`, () => {
    const svg = render(cfg.options);
    const rows = Object.values(cfg.tab_rows);
    const out = tabOutline(svg);
    if (!rows.length) { assert.equal(out, null, "no pointer available, no tab"); return; }
    assert.ok(out, "expected the BRG tab outline");
    const p = panel(svg);
    assert.equal(out.x0, p.x + p.w, "the tab's square left edge is flush with the panel's right edge");
    const top = rows[0][1] * 100, bottom = (rows.at(-1)[1] + rows.at(-1)[3]) * 100;
    const right = (rows[0][0] + rows[0][2]) * 100, wPanelRight = (cfg.panel[0] + cfg.panel[2]) * 100;
    assert.ok(Math.abs(out.top - top) <= TOL_EDGE, `top ${out.top} vs ${top}`);
    assert.ok(Math.abs(out.bottom - bottom) <= TOL_EDGE, `bottom ${out.bottom} vs ${bottom}`);
    // Width from each one's own panel edge: the twin's panel is 44 wide, the
    // widget's 12.5 fontSize, so compare how far each tab reaches past it.
    assert.ok(Math.abs((out.x1 - out.x0) - (right - wPanelRight)) <= TOL_RIGHT,
      `width ${out.x1 - out.x0} vs ${right - wPanelRight}`);
    if (rows.length === 2) assert.match(svg, /<line x1="[\d.]+" y1="[\d.]+" x2="[\d.]+" y2="[\d.]+" stroke="#00ffff" stroke-width="0.5"\/>/,
      "two rows share a hairline");
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

test("pointer 2 alone reads BRG OFF, one row", () => {
  const svg = render({ bearing2_enabled: true });
  assert.deepEqual(tabTexts(svg).map((t) => t.text), ["BRG OFF"]);
});
