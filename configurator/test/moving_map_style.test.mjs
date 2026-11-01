// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4472: the moving-map twin honours map_style, lockstep with pyEfis
// AER-4462 (#135). Under nav_only the device draws NAV_ONLY_PALETTE's
// background / range_ring / range_ring_label; standard, missing, or any
// unknown value draws the standard background, with the rings styled by
// range_ring_color / range_ring_opacity (AER-4461; RangeRingsLayer.colors).
// map_layers.map_style() normalises the same way: strip + lower, unknown ->
// standard.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const body = ["movingMapPalette", "pyFloat", "formatRangeNm", "parseRingFractions",
  "rangeRingGeom", "mapDevicePx", "buildMovingMap"]
  .map((f) => extractFunction(src, f)).join("\n\n");

// svgFromString and the editor state are the only free dependencies;
// returning the markup lets the test read exactly what the twin would draw.
const state = { layout: { columns: 110, rows: 200 }, screen: { width: 1280, height: 800 } };
const { movingMapPalette, buildMovingMap } = new Function("svgFromString", "state", `
  ${body}
  return { movingMapPalette, buildMovingMap };
`)((s) => s, state);

// Standard leaves the ring colours to range_ring_color / range_ring_opacity:
// by default #ffffff at 55% (alpha 140), ring and label alike, which is
// pyEfis STANDARD_PALETTE's (255, 255, 255, 140).
const STANDARD = { background: "#101820", ring: null, ringLabel: null };
const STANDARD_DRAWN = { background: "#101820", ring: "#ffffff", ringLabel: "#ffffff" };
const NAV_ONLY = { background: "#000000", ring: "rgb(120,120,120)", ringLabel: "rgb(170,170,170)" };

const svg = (options) => buildMovingMap({ options, span: { columns: 40, rows: 80 } });

function assertDraws(markup, pal, other, opacity) {
  assert.ok(markup.includes(`fill="${pal.background}"`), "background");
  assert.ok(markup.includes(`stroke="${pal.ring}" stroke-opacity="${opacity}"`), "ring stroke");
  assert.ok(markup.includes(`fill="${pal.ringLabel}" fill-opacity="${opacity}"`), "ring label");
  for (const v of Object.values(other)) assert.ok(!markup.includes(`"${v}"`), `stray ${v}`);
}

test("nav_only selects NAV_ONLY_PALETTE colours", () => {
  assert.deepEqual(movingMapPalette({ map_style: "nav_only" }), NAV_ONLY);
  assertDraws(svg({ map_style: "nav_only" }), NAV_ONLY, STANDARD_DRAWN, "1.0000");
});

test("nav_only is normalised like the device (strip + lower)", () => {
  assert.deepEqual(movingMapPalette({ map_style: " NAV_ONLY " }), NAV_ONLY);
  assert.deepEqual(movingMapPalette({ map_style: "Nav_Only" }), NAV_ONLY);
});

test("standard, missing and unknown values draw the standard map", () => {
  for (const options of [{}, { map_style: "standard" }, { map_style: "" },
    { map_style: null }, { map_style: "navonly" }, { map_style: "topo" }]) {
    assert.deepEqual(movingMapPalette(options), STANDARD, JSON.stringify(options));
    assertDraws(svg(options), STANDARD_DRAWN, NAV_ONLY, "0.5490");
  }
  assert.deepEqual(movingMapPalette(undefined), STANDARD);
  assertDraws(buildMovingMap({}), STANDARD_DRAWN, NAV_ONLY, "0.5490");
});

test("range_ring_color / opacity style the standard map only", () => {
  const opts = { range_ring_color: "#ff0000", range_ring_opacity: 100 };
  assertDraws(svg(opts), { ...STANDARD_DRAWN, ring: "#ff0000", ringLabel: "#ff0000" },
    NAV_ONLY, "1.0000");
  assertDraws(svg({ ...opts, map_style: "nav_only" }), NAV_ONLY,
    { background: "#101820", ring: "#ff0000" }, "1.0000");
});

test("nav_only changes only the palette, not the geometry", () => {
  // Blank every colour and opacity; what is left is the geometry.
  const strip = (s) => s.replace(/(fill|stroke|fill-opacity|stroke-opacity)="[^"]*"/g, '$1="X"');
  for (const extra of [{}, { layer_range_rings: false }, { range_nm: 25, ownship_position: 30 }]) {
    assert.equal(
      strip(svg({ ...extra, map_style: "nav_only" })),
      strip(svg(extra)),
      JSON.stringify(extra));
  }
});
