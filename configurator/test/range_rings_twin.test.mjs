// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4461 (G13, pyEfis #136): the moving map's range-ring options, twinned
// from pyEfis RangeRingsLayer. The expected values are not hand-written: they
// are what the REAL layer computes, dumped by tools/range_rings_oracle.py into
// test/fixtures/range_rings_pyefis.json (regenerate with the command in that
// script's header). This test runs the REAL editor functions against them
// (see test/support/extract.mjs), then checks the options survive a save.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const GEOM = ["pyFloat", "formatRangeNm", "parseRingFractions", "rangeRingGeom"];
const { formatRangeNm, parseRingFractions, rangeRingGeom } = new Function(`
  ${GEOM.map((f) => extractFunction(src, f)).join("\n\n")}
  return { formatRangeNm, parseRingFractions, rangeRingGeom };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/range_rings_pyefis.json", import.meta.url), "utf8"));

// QColor.name() spells every colour #rrggbb; the twin hands SVG the colour as
// written. Normalise the forms the fixture uses so the two can be compared.
const NAMED = { red: "#ff0000", white: "#ffffff" };
function hex(c) {
  c = c.toLowerCase();
  if (NAMED[c]) return NAMED[c];
  const m = c.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/);
  return m ? `#${m[1]}${m[1]}${m[2]}${m[2]}${m[3]}${m[3]}` : c;
}

const close = (a, b, what) =>
  assert.ok(Math.abs(a - b) < 1e-9, `${what}: twin ${a} vs pyEfis ${b}`);

for (const c of FIXTURE.cases) {
  test(`matches pyEfis RangeRingsLayer: ${JSON.stringify(c.options)} @ ${c.w}x${c.h}`, () => {
    const g = rangeRingGeom(c.options, c.w, c.h);
    close(g.cx, c.cx, "cx");
    close(g.cy, c.cy, "cy");
    assert.equal(g.labels, c.labels, "labels on/off");
    assert.equal(hex(g.color), c.color, "colour");
    assert.equal(g.alpha, c.alpha, "alpha");
    close(g.strokeW, c.strokeW, "ring width");
    assert.equal(g.labelPx, c.labelPx, "label px");
    assert.equal(formatRangeNm(g.range), c.chip, "status chip range");
    assert.deepEqual(g.rings.map((r) => r.frac), c.rings.map((r) => r.frac), "fractions");
    g.rings.forEach((r, i) => {
      const e = c.rings[i];
      close(r.r, e.r, `ring ${i} radius`);
      assert.equal(r.text, e.text, `ring ${i} label text`);
      for (const k of ["x", "y", "w", "h"]) close(r.label[k], e.label[k], `ring ${i} label ${k}`);
    });
  });
}

test("the fixture covers the default: unset options give exactly half + full range", () => {
  const def = FIXTURE.cases.find((c) => Object.keys(c.options).length === 0);
  assert.deepEqual(def.rings.map((r) => r.frac), [0.5, 1]);
  const g = rangeRingGeom({}, 800, 480);
  assert.deepEqual(g.rings.map((r) => r.r), [120, 240]);   // cy 240 = anchor -> top
  assert.equal(g.alpha, 140);
});

test("a YAML list is not a comma string: falls back like pyEfis's str() parse", () => {
  // str([0.25, 1]) is "[0.25, 1]", which float() rejects -> default rings.
  // JS String() would give "0.25,1" and quietly diverge.
  assert.deepEqual(parseRingFractions([0.25, 1]), [0.5, 1]);
});

test("format_range_nm ties round half-even like Python", () => {
  assert.equal(formatRangeNm(0.25), "0.2");
  assert.equal(formatRangeNm(0.75), "0.8");
  assert.equal(formatRangeNm(1.25), "1.2");
  assert.equal(formatRangeNm(12.5), "12");
  assert.equal(formatRangeNm(13.5), "14");
  assert.equal(formatRangeNm(9.96), "10");
  assert.equal(formatRangeNm(5), "5");
});

// --- save round-trip: what the property panel writes is what pyEfis reads ---

const SAVE = ["round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc", "fromStoredDoc"];
const RING_SPECS = {
  // Copied from the live dev schema.json (pyEfis #363 Props).
  layer_range_rings: { type: "boolean", default: true },
  range_ring_fractions: { type: "string", default: "0.5,1" },
  range_ring_labels: { type: "boolean", default: true },
  range_ring_label_angle: { type: "integer", default: 0, min: 0, max: 359, step: 1 },
  range_ring_color: { type: "color", default: "#ffffff" },
  range_ring_opacity: { type: "integer", default: 55, min: 0, max: 100, step: 1 },
  range_ring_width: { type: "number", default: 0, min: 0, max: 10, step: 0.5 },
  range_ring_label_size: { type: "integer", default: 0, min: 0, max: 48, step: 1 },
};

function saveDoc(options) {
  const state = {
    schema: { common_options: {}, instruments: { moving_map: { options: RING_SPECS } } },
    groupsById: {},
    screen: { width: 1024, height: 600 },
    defaultScreen: "MAP",
    screens: [{
      name: "MAP", layout: { rows: 110, columns: 200 },
      instruments: [{ type: "moving_map", row: 0, column: 0,
                      span: { rows: 110, columns: 200 }, options }],
    }],
  };
  const api = new Function("state", `
    ${SAVE.map((f) => extractFunction(src, f)).join("\n\n")}
    return { toPyefisDoc, fromStoredDoc };
  `)(state);
  return { state, api };
}

test("range-ring options round-trip through save and reload with their types", () => {
  // Exactly what optionField() stores: text input -> string, checkbox -> bool,
  // number input -> parseFloat, colour input -> #rrggbb.
  const opts = {
    range_ring_fractions: "0.25,0.5,0.75,1",
    range_ring_labels: false,
    range_ring_label_angle: 135,
    range_ring_color: "#00ffcc",
    range_ring_opacity: 80,
    range_ring_width: 1.5,
    range_ring_label_size: 18,
  };
  const { api } = saveDoc({ ...opts });
  const yaml = jsyaml.dump(api.toPyefisDoc(), { lineWidth: 100, noRefs: true });
  const saved = jsyaml.load(yaml).screens.MAP.instruments[0].options;
  assert.deepEqual(saved, opts);
  // and they reload into the editor unchanged
  const back = api.fromStoredDoc(jsyaml.load(yaml)).screens[0].instruments[0].options;
  assert.deepEqual(back, opts);
});

test("unset range-ring options stay unset in the saved YAML (device defaults apply)", () => {
  const { api } = saveDoc({});
  const saved = api.toPyefisDoc().screens.MAP.instruments[0].options || {};
  assert.ok(!Object.keys(saved).some((k) => k.startsWith("range_ring")));
});
