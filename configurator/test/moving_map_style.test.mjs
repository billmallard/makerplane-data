// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4472: the moving-map twin honours map_style, lockstep with pyEfis
// AER-4462 (#135). Under nav_only the device draws NAV_ONLY_PALETTE's
// background / range_ring / range_ring_label; standard, missing, or any
// unknown value keeps today's look exactly (map_layers.map_style()
// normalises the same way: strip + lower, unknown -> standard).
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const body = ["movingMapPalette", "buildMovingMap"]
  .map((f) => extractFunction(src, f)).join("\n\n");

// svgFromString is the only free dependency; returning the markup lets the
// test read exactly what the twin would draw.
const { movingMapPalette, buildMovingMap } = new Function("svgFromString", `
  ${body}
  return { movingMapPalette, buildMovingMap };
`)((s) => s);

const STANDARD = { background: "#101820", ring: "rgba(255,255,255,0.55)", ringLabel: "#cfd6dd" };
const NAV_ONLY = { background: "#000000", ring: "rgb(120,120,120)", ringLabel: "rgb(170,170,170)" };

const svg = (options) => buildMovingMap({ options });

function assertDraws(markup, pal, other) {
  assert.ok(markup.includes(`fill="${pal.background}"`), "background");
  assert.ok(markup.includes(`stroke="${pal.ring}"`), "ring stroke");
  assert.ok(markup.includes(`fill="${pal.ringLabel}"`), "ring label");
  for (const v of Object.values(other)) assert.ok(!markup.includes(`"${v}"`), `stray ${v}`);
}

test("nav_only selects NAV_ONLY_PALETTE colours", () => {
  assert.deepEqual(movingMapPalette({ map_style: "nav_only" }), NAV_ONLY);
  assertDraws(svg({ map_style: "nav_only" }), NAV_ONLY, STANDARD);
});

test("nav_only is normalised like the device (strip + lower)", () => {
  assert.deepEqual(movingMapPalette({ map_style: " NAV_ONLY " }), NAV_ONLY);
  assert.deepEqual(movingMapPalette({ map_style: "Nav_Only" }), NAV_ONLY);
});

test("standard, missing and unknown values keep today's look", () => {
  for (const options of [{}, { map_style: "standard" }, { map_style: "" },
    { map_style: null }, { map_style: "navonly" }, { map_style: "topo" }]) {
    assert.deepEqual(movingMapPalette(options), STANDARD, JSON.stringify(options));
    assertDraws(svg(options), STANDARD, NAV_ONLY);
  }
  assert.deepEqual(movingMapPalette(undefined), STANDARD);
  assertDraws(buildMovingMap({}), STANDARD, NAV_ONLY);
});

test("nav_only changes only the palette, not the geometry", () => {
  const swap = (s, pal) => s.split(pal.background).join("BG")
    .split(pal.ringLabel).join("LBL").split(pal.ring).join("RING");
  for (const extra of [{}, { layer_range_rings: false }, { range_nm: 25, ownship_position: 30 }]) {
    assert.equal(
      swap(svg({ ...extra, map_style: "nav_only" }), NAV_ONLY),
      swap(svg(extra), STANDARD),
      JSON.stringify(extra));
  }
});
