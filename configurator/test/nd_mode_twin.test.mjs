// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5272 (G11b twin): the moving map's ND mode, EXPANDED MAP (G11a) and CTR
// MAP (G11b), twinned from pyEfis instruments/map/nd.py (billmallard/pyEfis
// #134); AER-5396 adds the G11c EXPANDED polish (pyEfis #443): the portrait
// arc, the backing panels and filled TRK box, and the ring-label keep-out. The expected values are not hand-written: they are what the REAL
// module and widget compute and paint, dumped by tools/nd_mode_oracle.py into
// test/fixtures/nd_mode_pyefis.json (regenerate with the command in that
// script's header). This test runs the REAL editor functions against them
// (see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, ndModeSource } from "./support/extract.mjs";

const src = editorSource();
const FNS = ["pyFloat", "formatRangeNm", "parseRingFractions", "rangeRingGeom"];
const ND = ["ndMode", "ndOwnYFrac", "ndFontPx", "ndStatusChipHeight", "ndVdevGeometry",
  "ndArcReach", "ndCornerBlockBottom", "ndBlockWidths", "ndKeepOut", "ndRectsIntersect", "ndGeometry", "ndTickBearings", "ndVdevPointerY", "ndTopLeft",
  "ndTopRight", "ndEnabledLayers", "ndVdev", "ndAdvance", "ndPaintOps", "ndOpsSvg",
  "ndPreviewRead"];
const T = new Function(`
  ${FNS.map((f) => extractFunction(src, f)).join("\n\n")}
  ${ndModeSource(src)}
  return { ${[...FNS, ...ND].join(", ")} };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/nd_mode_pyefis.json", import.meta.url), "utf8"));
const EPS = 1e-5;   // the fixture is rounded to 1e-6 px

const close = (a, b, what) =>
  assert.ok(Math.abs(a - b) < EPS, `${what}: twin ${a} vs pyEfis ${b}`);
const closePts = (a, b, what) => {
  assert.equal(a.length, b.length, `${what}: point count`);
  a.forEach((p, i) => { close(p[0], b[i][0], `${what}[${i}].x`); close(p[1], b[i][1], `${what}[${i}].y`); });
};
const closeArr = (a, b, what) => {
  assert.equal(a.length, b.length, `${what}: length`);
  a.forEach((v, i) => close(v, b[i], `${what}[${i}]`));
};
const closeRect = (a, b, what) => {
  for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`);
};
// Text widths are Qt's hinted advances, which the twin follows to a px per
// glyph or two (see ndAdvance): x and width of anything placed by a text
// width are held to TEXT_TOL px, every y and every stroke to EPS.
const TEXT_TOL = 3;
const nearText = (a, b, what) =>
  assert.ok(Math.abs(a - b) <= TEXT_TOL, `${what}: twin ${a} vs pyEfis ${b}`);
const closeTextRect = (a, b, what) => {
  nearText(a.x, b.x, `${what}.x`); nearText(a.w, b.w, `${what}.w`);
  close(a.y, b.y, `${what}.y`); close(a.h, b.h, `${what}.h`);
};
const samePen = (a, b, what) => {
  if (b === null) { assert.equal(a, null, `${what}: no pen`); return; }
  assert.equal(a.rgb, b.rgb, `${what}: pen colour`);
  close(a.width, b.width, `${what}: pen width`);
};

// The device's recorded path: the arc (a move, then curve end points), then a
// move/line pair per tick.
function splitPath(segs) {
  let i = 1;
  while (i < segs.length && segs[i][0] === "C") i++;
  const arc = segs.slice(0, i).map((s) => [s[1], s[2]]);
  const ticks = [];
  for (; i < segs.length; i += 2) {
    assert.equal(segs[i][0], "M");
    assert.equal(segs[i + 1][0], "L");
    ticks.push([[segs[i][1], segs[i][2]], [segs[i + 1][1], segs[i + 1][2]]]);
  }
  return { arc, ticks };
}

function compareOps(twin, dev, what) {
  assert.deepEqual(twin.map((o) => o.op), dev.map((o) => o.op), `${what}: op sequence`);
  twin.forEach((t, i) => {
    const d = dev[i], w = `${what} op ${i} (${d.op} ${d.text ?? ""})`;
    samePen(t.pen, d.pen, w);
    if (d.op === "path") {
      const { arc, ticks } = splitPath(d.segments);
      const { cx, cy, r, half } = t.arc;
      // Qt places the arc's ends on its own Bezier approximation of the
      // ellipse: four cubics per circle, whose radial error peaks at 0.0273%
      // of r mid-quadrant. G11a's 60 and 40 deg ends sat within 0.02%; G11c's
      // portrait sectors (about 23 and 20 deg) end nearer the peak (0.18 px at
      // r 692). So every arc point is held to the curve's maximum radially.
      // The ends also slide along the curve: measured over this fixture, at
      // most 0.043% of r (0.21 px at the 20 deg floor, 0.25 px at r 692),
      // against 0.02% on G11a's sectors, so the ends are held to 0.045%. The
      // ticks are exact to 1e-5.
      const tol = r * 2.75e-4, endTol = r * 4.5e-4;
      const near = (a, b, what, t = tol) => assert.ok(Math.abs(a - b) <= t, `${what}: twin ${b} vs pyEfis ${a}`);
      for (const [x, y] of arc) near(Math.hypot(x - cx, y - cy), r, `${w}: arc point on the circle`);
      // Qt: arcMoveTo(90 + half), arcTo(-2 * half), counter-clockwise from 3 o'clock.
      const at = (deg) => [cx + r * Math.cos(deg * Math.PI / 180), cy - r * Math.sin(deg * Math.PI / 180)];
      [[arc[0], at(90 + half)], [arc[arc.length - 1], at(90 - half)]].forEach(([p, q], k) => {
        near(p[0], q[0], `${w}: arc end ${k} x`, endTol);
        near(p[1], q[1], `${w}: arc end ${k} y`, endTol);
      });
      ticks.forEach((tk, j) => closePts(t.ticks[j], tk, `${w}: tick ${j}`));
      assert.equal(t.ticks.length, ticks.length, `${w}: tick count`);
    } else if (d.op === "text_rect") {
      assert.equal(t.text, d.text, `${w}: text`);
      closeTextRect(t.rect, d.rect, w);
      assert.equal(d.flags, 132, "AlignCenter");
      for (const k of ["px", "bold", "ascent", "descent", "height"]) assert.equal(t.font[k], d.font[k], `${w}: font ${k}`);
    } else if (d.op === "text_at") {
      assert.equal(t.text, d.text, `${w}: text`);
      nearText(t.pt[0], d.pt[0], `${w}: x`);
      close(t.pt[1], d.pt[1], `${w}: baseline`);
      for (const k of ["px", "bold", "ascent"]) assert.equal(t.font[k], d.font[k], `${w}: font ${k}`);
      const adv = T.ndAdvance(t.text, t.font.px, t.font.bold);
      assert.ok(Math.abs(adv - d.advance) <= Math.ceil([...t.text].length / 2),
        `${w}: advance twin ${adv} vs pyEfis ${d.advance}`);
    } else if (d.op === "rect") {
      closeTextRect(t.rect, d.rect, w);
      assert.equal(t.brush, d.brush, `${w}: brush`);
    } else if (d.op === "backing") {
      // nd._backing's rects: placed and sized by text widths in x, exact in y.
      assert.equal(t.brush, d.brush, `${w}: panel colour`);
      assert.equal(t.alpha, d.alpha, `${w}: panel alpha`);
      close(t.radius, d.radius, `${w}: corner radius`);
      assert.equal(t.rects.length, d.rects.length, `${w}: rect count`);
      t.rects.forEach((r, j) => closeTextRect(r, d.rects[j], `${w} rect ${j}`));
    } else if (d.op === "polygon") {
      closePts(t.pts, d.pts, w);
      assert.equal(t.brush, d.brush, `${w}: brush`);
    } else if (d.op === "line") {
      closePts(t.pts, d.pts, w);
    }
  });
}

const readOf = (table) => (key) => (key in table ? table[key] : null);
const ND_IDS = new Set(["airports", "navaids", "fixes"]);

for (const c of FIXTURE.paint) {
  test(`matches pyEfis ND / map transform: ${JSON.stringify(c.options)} @ ${c.w}x${c.h}`, () => {
    assert.equal(T.ndMode(c.options), c.mode, "normalize_mode");
    const g = T.rangeRingGeom(c.options, c.w, c.h);
    close(g.cx, c.cx, "anchor x");
    close(g.cy, c.cy, "anchor y");
    close(g.cx, c.ownship[0], "ownship x");
    close(g.cy, c.ownship[1], "ownship y");
    close(g.rangePx, c.range_px, "range px");
    assert.equal(g.rings.length, c.rings.length, "ring count");
    g.rings.forEach((r, i) => {
      close(r.r, c.rings[i].r, `ring ${i} radius`);
      assert.equal(r.text, c.rings[i].text, `ring ${i} label`);
      closeRect(r.label, c.rings[i].label, `ring ${i} label rect`);
      assert.equal(r.keptOut, c.rings[i].kept_out, `ring ${i} label kept out`);
    });
    const ko = c.nd_active ? T.ndKeepOut(c.mode, c.w, c.h, g.cx, g.cy, c.track) : [];
    assert.equal(ko.length, c.keep_out.length, "keep-out rect count");
    ko.forEach((k, i) => closeTextRect(k, c.keep_out[i], `keep-out ${i}`));
    assert.equal(`${T.formatRangeNm(g.range)} NM  TRK UP`, c.nd_active ? c.status_chip
      : `${T.formatRangeNm(g.range)} NM  TRK UP`, "status chip text");
    if (!c.nd_active) {
      assert.equal(c.geometry, null);
      return;
    }
    const geo = T.ndGeometry(c.mode, c.w, c.h);
    for (const k of ["fs", "own_y", "r", "apex", "box_h", "half"]) close(geo[k], c.geometry[k], k);
    close(T.ndCornerBlockBottom(c.w, c.h), c.corner_block_bottom, "corner block bottom");
    // The worst-case widths set r on a portrait panel, so they must be Qt's
    // to the px, not within TEXT_TOL: the geometry check above holds r to EPS.
    assert.deepEqual(T.ndBlockWidths(geo.fs), c.block_widths, "corner block worst-case widths");
    close(T.ndArcReach(c.w, c.h), c.arc_reach, "arc reach");
    const vg = T.ndVdevGeometry(c.w, c.h);
    for (const k of ["x", "y0", "half", "fs"]) close(vg[k], c.vdev[k], `vdev ${k}`);
    assert.equal(T.ndStatusChipHeight(c.w), c.status_chip_height, "status chip height");
    for (const [p, y] of Object.entries(c.vdev_pointer_y)) close(T.ndVdevPointerY(vg, Number(p)), y, `vdev pointer y @ ${p}`);
    assert.deepEqual(T.ndTickBearings(0, geo.half), c.ticks, "tick bearings");
    const enabled = T.ndEnabledLayers(c.options);
    assert.deepEqual([...enabled].sort(), c.enabled_layers.filter((id) => ND_IDS.has(id)).sort(), "option-list layers");
    const ops = T.ndPaintOps({ mode: c.mode, w: c.w, h: c.h, cx: g.cx, cy: g.cy, up: 0,
      track: c.track, trackOld: c.track_old, read: () => null, now: FIXTURE.now, enabled,
      chip: c.status_chip });
    compareOps(ops, c.ops, "paint");
  });
}

test("matches QRectF.intersects: overlap, touching edges, empty rects", () => {
  const r = ([x, y, w, h]) => ({ x, y, w, h });
  for (const [a, b, want] of FIXTURE.rect_intersects) {
    assert.equal(T.ndRectsIntersect(r(a), r(b)), want, `${a} / ${b}`);
    assert.equal(T.ndRectsIntersect(r(b), r(a)), want, `${b} / ${a}`);
  }
});

for (const c of FIXTURE.read) {
  test(`matches pyEfis ND blocks: ${c.name}`, () => {
    const read = readOf(c.table);
    const tl = T.ndTopLeft(read);
    assert.deepEqual(tl.lines, c.top_left, "top_left lines");
    assert.equal(tl.arrow, c.arrow, "wind arrow");
    assert.deepEqual(T.ndTopRight(read, FIXTURE.now), c.top_right, "top_right");
    assert.deepEqual(T.ndVdev(read), c.vdev, "vdev");
  });
}

for (const c of FIXTURE.paint_data) {
  test(`matches pyEfis ND paint with data: ${c.name} ${c.mode} @ ${c.w}x${c.h}`, () => {
    const g = T.rangeRingGeom({ nd_mode: c.mode }, c.w, c.h);
    const ops = T.ndPaintOps({ mode: c.mode, w: c.w, h: c.h, cx: g.cx, cy: g.cy, up: 0,
      track: 0, trackOld: false, read: readOf(c.table), now: FIXTURE.now,
      enabled: T.ndEnabledLayers({}), chip: "10 NM  TRK UP" });
    compareOps(ops, c.ops, "paint");
  });
}

// The definition-of-done numbers, read off the fixture above, written out so a
// reviewer can check them against pyEfis by eye.
test("pinned: EXPANDED / CTR geometry at 800x480 and 650x1000", () => {
  const e8 = T.ndGeometry("expanded", 800, 480);
  closeArr([e8.r, e8.apex, e8.half, e8.own_y], [357.6, 36, 60, 393.6], "EXPANDED 800x480 r/apex/half/own_y");
  // G11c: the portrait arc uses the height, clear of the top corner blocks
  // measured in DejaVu Sans Condensed (G11a had r 420.05, apex 399.95, 40 deg).
  const e6 = T.ndGeometry("expanded", 650, 1000);
  close(e6.r, 692.116522, "EXPANDED 650x1000 r");
  close(e6.apex, 127.883478, "EXPANDED 650x1000 apex");
  close(e6.half, 22.961202, "EXPANDED 650x1000 half");
  assert.equal(e6.own_y, 820);
  // A tall, narrow panel bottoms out at ARC_MIN_HALF_DEG 20.
  close(T.ndGeometry("expanded", 400, 1200).half, 20, "EXPANDED 400x1200 half (ARC_MIN_HALF_DEG)");
  assert.deepEqual([T.ndGeometry("ctr", 800, 480).r, T.ndGeometry("ctr", 800, 480).half], [204, 180]);
  assert.equal(T.ndGeometry("ctr", 650, 1000).r, 270);
});

test("pinned: G11c polish is EXPANDED only, and keeps the full-range label out", () => {
  const ops = (mode, w, h) => {
    const g = T.rangeRingGeom({ nd_mode: mode }, w, h);
    return T.ndPaintOps({ mode, w, h, cx: g.cx, cy: g.cy, up: 0, track: 0, trackOld: false,
      read: () => null, now: FIXTURE.now, enabled: T.ndEnabledLayers({}), chip: "10 NM  TRK UP" });
  };
  for (const [w, h] of [[800, 480], [650, 1000]]) {
    const e = ops("expanded", w, h), c = ops("ctr", w, h);
    assert.equal(e.find((o) => o.op === "rect").brush, "#000000", `EXPANDED TRK box filled @ ${w}x${h}`);
    assert.equal(e.filter((o) => o.op === "backing").length, 2, `EXPANDED: chip/top-left and option-list panels @ ${w}x${h}`);
    assert.equal(c.find((o) => o.op === "rect").brush, null, `CTR TRK box unfilled @ ${w}x${h}`);
    assert.equal(c.filter((o) => o.op === "backing").length, 0, `CTR: no panels @ ${w}x${h}`);
    assert.deepEqual(T.rangeRingGeom({ nd_mode: "expanded" }, w, h).rings.map((r) => r.keptOut), [false, true]);
    assert.deepEqual(T.rangeRingGeom({ nd_mode: "ctr" }, w, h).rings.map((r) => r.keptOut), [false, false]);
  }
  const svg = T.ndOpsSvg(ops("expanded", 800, 480));
  assert.ok(svg.includes(`fill-opacity="${(200 / 255).toFixed(4)}"`), "panel alpha 200");
});

test("pinned: vdev pointer y (up for positive GSI, as the HSI)", () => {
  const vg = T.ndVdevGeometry(800, 480);
  close(T.ndVdevPointerY(vg, 0.4), 205.44, "GSI +0.4 @ 800x480");
  close(T.ndVdevPointerY(vg, -1), 326.4, "GSI -1 @ 800x480");
  close(T.ndVdevPointerY(T.ndVdevGeometry(650, 1000), 1), 320, "GSI +1 @ 650x1000");
});

test("pinned: rangeRingGeom under each mode", () => {
  const rr = (o) => T.rangeRingGeom(o, 800, 480);
  assert.deepEqual(rr({}).rings.map((r) => r.r), [120, 240]);                      // anchor -> top
  assert.deepEqual(rr({ nd_mode: "off", ownship_position: 25 }).rings.map((r) => r.r), [180, 360]);
  const e = rr({ nd_mode: "expanded", ownship_position: 25 });     // ownship_position ignored
  closeArr([e.cy, e.rangePx], [393.6, 357.6], "EXPANDED anchor / range px");
  closeArr(e.rings.map((r) => r.r), [178.8, 357.6], "EXPANDED rings");
  const c = rr({ nd_mode: "ctr", range_nm: 2.5 });
  assert.deepEqual([c.cy, c.rangePx], [240, 204]);
  assert.deepEqual(c.rings.map((r) => r.r), [102, 204]);
});

test("nd_mode off draws exactly what an unset nd_mode draws", () => {
  for (const [w, h] of [[800, 480], [650, 1000], [1024, 600]]) {
    for (const extra of [{}, { ownship_position: 30 }, { range_nm: 25, range_ring_fractions: "0.25,1" }]) {
      const base = T.rangeRingGeom(extra, w, h);
      for (const off of ["off", "", null, "bogus", true, ["ctr"]]) {
        assert.deepEqual(T.rangeRingGeom({ ...extra, nd_mode: off }, w, h), base, `nd_mode ${JSON.stringify(off)}`);
      }
    }
  }
});

test("no preview value, no block: an empty preview leaves every data block out", () => {
  const read = T.ndPreviewRead({});
  assert.deepEqual(T.ndTopLeft(read), { lines: [], arrow: null });
  assert.deepEqual(T.ndTopRight(read, FIXTURE.now), []);
  assert.equal(T.ndVdev(read), null);
  // A preview value is read as good data under its FIX key.
  assert.deepEqual(T.ndPreviewRead({ GS: 112 })("GS"), [112, false, false, false]);
});

test("the SVG carries every op, escaped", () => {
  const g = T.rangeRingGeom({ nd_mode: "ctr" }, 800, 480);
  const ops = T.ndPaintOps({ mode: "ctr", w: 800, h: 480, cx: g.cx, cy: g.cy, up: 0, track: 0,
    trackOld: false, read: T.ndPreviewRead({ FPLSTATE: 1, WPNAME: "<K&B>" }), now: FIXTURE.now,
    enabled: T.ndEnabledLayers({}) });
  const svg = T.ndOpsSvg(ops);
  assert.ok(svg.includes("&lt;K&amp;B&gt;"));
  assert.equal((svg.match(/<text /g) || []).length, ops.filter((o) => o.op.startsWith("text")).length);
  assert.ok(/ A204,204 0 0 0 /.test(svg), "closed rose: two half arcs at r 204");
});
