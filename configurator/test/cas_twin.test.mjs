// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6356 (CAS3): the cas_window and cas_marker twins against what the REAL
// pyEfis CASWindow and CASMarker paint. The expected values are not
// hand-written: they are dumped by tools/cas_oracle.py into
// test/fixtures/cas_pyefis.json (regenerate with the command in that script's
// header). This test runs the REAL editor functions against them (see
// test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["QT_BOLD_METRICS", "CAS_TILE_LEVEL", "CAS_MARKER_TEXT", "CAS_COLORS",
  "CAS_REVERSE_TEXT", "CAS_PREVIEW_LEVEL"];
const FUNCS = ["fontScale", "apColor", "apEsc", "qtBoldFont", "casParseSlot", "casMessages",
  "casLines", "casWindowRows", "casFitPx", "casBackground", "casTile", "casWindowGeom",
  "casMarkerGeom", "casSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { casMessages, casWindowRows, casWindowGeom, casMarkerGeom, casSvg, CAS_PREVIEW_LEVEL };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/cas_pyefis.json", import.meta.url), "utf8"));
const WINDOW = FIXTURE.cases.filter((c) => c.type === "cas_window");
const MARKER = FIXTURE.cases.filter((c) => c.type === "cas_marker");

const close = (a, b, what, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what, tol = 1e-9) => { for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`, tol); };
// The twin's [css "#rrggbb", alpha 0..1] as Qt's 0..255 rgba.
const rgba = ([css, a]) => [1, 3, 5].map((i) => parseInt(css.slice(i, i + 2), 16)).concat(Math.round(a * 255));
const label = (c) => `${c.type} ${c.state} ${c.w}x${c.h} t=${c.t} ${JSON.stringify(c.options)}`;
// The device's model output, in the twin's message shape.
const msgsOf = (c) => c.messages.map((m) => ({ level: m.level, acked: m.acked, id: m.id, line: m.line }));
const LEVEL_NUM = { warning: 3, caution: 2, advisory: 1 };

function checkOps(c, g) {
  const ops = c.ops;
  const fill = ops.filter((o) => o.op === "fill_rect");
  if (!fill.length) {
    assert.equal(g.bg, null, "nothing drawn: no background");
  } else {
    assert.equal(fill.length, 1);
    closeRect(g.bg.rect, fill[0].rect, "background");
    assert.deepEqual(rgba([g.bg.color, 1]).slice(0, 3), fill[0].rgba.slice(0, 3), "background colour");
    // Qt stores alpha in 16 bits and reports the top 8; the twin keeps the float.
    close(g.bg.alpha * 255, fill[0].rgba[3], "background alpha", 1);
  }
  const rects = ops.filter((o) => o.op === "rect");
  const texts = ops.filter((o) => o.op === "text");
  assert.equal(ops.filter((o) => o.op === "rounded_rect").length, 0);
  assert.equal(g.tiles.length, rects.length, "tile count");
  assert.equal(g.tiles.length, texts.length, "caption count");
  g.tiles.forEach((t, i) => {
    const r = rects[i], x = texts[i];
    closeRect(t.box, r.rect, `tile ${i} box`);
    close(t.width, r.pen.width, `tile ${i} border`);
    assert.deepEqual(rgba(t.color), r.pen.rgba, `tile ${i} outline colour`);
    assert.equal(t.fill ? "SolidPattern" : "NoBrush", r.brush, `tile ${i} fill`);
    if (t.fill) assert.deepEqual(rgba(t.color), r.brush_rgba, `tile ${i} fill colour`);
    assert.equal(t.text.text, x.text, `tile ${i} text`);
    assert.equal(t.text.px, x.pixel_size, `"${x.text}" pixel size`);
    assert.equal(t.text.family, x.family, `"${x.text}" family`);
    assert.ok(x.bold);
    assert.deepEqual(rgba(t.text.color), x.pen.rgba, `"${x.text}" colour`);
    closeRect(t.text.rect, x.rect, `"${x.text}" rect`);
    // Qt's laid-out line box: top = baseline - ascent; the left edge within
    // the advance's rounding (boundingRect can be a pixel wider than it).
    close(t.text.baseline - x.ascent, x.laid_out.y, `"${x.text}" line top`);
    close(t.text.x, x.laid_out.x, `"${x.text}" left`, 1);
  });
}

test("the window's preview slots parse and sort as the device's model does", () => {
  const msgs = twin.casMessages(FIXTURE.schema_preview.cas_window.lines);
  for (const c of WINDOW.filter((x) => x.state === "preview")) assert.deepEqual(msgs, msgsOf(c), label(c));
});

test("the marker's preview level is the device's (CAUTION)", () => {
  const pv = FIXTURE.schema_preview.cas_marker;
  const lvl = twin.CAS_PREVIEW_LEVEL[pv.level];
  for (const c of MARKER.filter((x) => x.state === "preview")) {
    assert.equal(lvl, LEVEL_NUM[c.tile.level], label(c));
    assert.equal(pv.text, c.tile.text, label(c));
  }
});

test("rows (lines, +k overflow, flash) are CASWindow.rows", () => {
  for (const c of WINDOW) {
    const rows = twin.casWindowRows(msgsOf(c), c.count, c.options.lines);
    assert.deepEqual(rows.map(({ text, level, flash, kind }) => ({ text, level, flash, kind })), c.rows, label(c));
  }
});

for (const c of WINDOW) {
  test(`window paint ops match pyEfis: ${label(c)}`, () => {
    checkOps(c, twin.casWindowGeom(c.options, msgsOf(c), c.count, c.w, c.h, c.on));
  });
}

for (const c of MARKER) {
  test(`marker paint ops match pyEfis: ${label(c)}`, () => {
    const t = c.tile;
    const g = t ? twin.casMarkerGeom(c.options, LEVEL_NUM[t.level], t.flash, c.w, c.h, c.on)
      : twin.casMarkerGeom(c.options, 0, false, c.w, c.h, c.on);
    checkOps(c, g);
  });
}

test("the twin's own parse matches the device for every bus list, malformed slots included", () => {
  // The oracle's slot lists reach the twin only through the model output
  // above; here the twin parses the raw slots itself.
  const raw = {
    malformed: ["4|0|X|BAD LEVEL|", "garbage", "2|0|NAVFAIL|NAV FAIL|", "2|2|X|BAD ACK|", "1|0|X||"],
    unsorted: ["1|0|ADV|ADVISORY ONE|", "3|1|WACK|WARNING ACKED|", "2|0|CAU|CAUTION ONE|", "3|0|WNEW|WARNING NEW|"],
    detail: ["2|0|FUELLOW|FUEL LOW|L TANK", "1|1|PITOT|PITOT HEAT|OFF"],
  };
  for (const [state, slots] of Object.entries(raw)) {
    const c = WINDOW.find((x) => x.state === state);
    assert.deepEqual(twin.casMessages(slots), msgsOf(c), state);
  }
});

test("the flash phases differ, and the window is left-aligned while the marker is centred", () => {
  const msgs = twin.casMessages(FIXTURE.schema_preview.cas_window.lines);
  const on = twin.casWindowGeom({}, msgs, msgs.length, 360, 160, true);
  const off = twin.casWindowGeom({}, msgs, msgs.length, 360, 160, false);
  assert.deepEqual(on.tiles.map((t) => t.fill), [true, true, false]);
  assert.deepEqual(off.tiles.map((t) => t.fill), [false, false, false]);
  assert.ok(on.tiles.every((t) => t.text.left && t.text.x === t.text.rect.x));
  const m = twin.casMarkerGeom({}, 2, false, 160, 40).tiles[0].text;
  assert.ok(!m.left);
  close(m.x + m.adv / 2, m.rect.x + m.rect.w / 2, "marker word centred");
});

test("quiet draws nothing, not even the background", () => {
  assert.equal(twin.casSvg(twin.casWindowGeom({}, [], 0, 360, 160)), '<svg viewBox="0 0 360 160" class="gauge-svg">\n</svg>');
  assert.equal(twin.casSvg(twin.casMarkerGeom({}, 0, false, 160, 40)), '<svg viewBox="0 0 160 40" class="gauge-svg">\n</svg>');
});

test("buildCas draws each type from its schema preview, in device px", () => {
  const build = new Function("state", "svgFromString", "mapDevicePx", `
    ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
    ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
    ${extractFunction(src, "buildCas")}
    return buildCas;
  `)({ schema: { instruments: { cas_window: { preview: FIXTURE.schema_preview.cas_window },
    cas_marker: { preview: FIXTURE.schema_preview.cas_marker } } } }, (s) => s, () => ({ w: 278, h: 196 }));
  const win = build({ type: "cas_window", options: {} });
  assert.deepEqual([...win.matchAll(/>([^<]+)<\/text>/g)].map((m) => m[1]), ["AP LINK LOST", "NAV FAIL", "CAS TEST"]);
  const c = WINDOW.find((x) => x.state === "preview" && x.w === 278 && x.t === 1000);
  assert.equal(win, twin.casSvg(twin.casWindowGeom({}, msgsOf(c), c.count, 278, 196, true)));
  const mrk = build({ type: "cas_marker", options: { lines: 2 } });
  assert.deepEqual([...mrk.matchAll(/>([^<]+)<\/text>/g)].map((m) => m[1]), ["CAUTION"]);
});
