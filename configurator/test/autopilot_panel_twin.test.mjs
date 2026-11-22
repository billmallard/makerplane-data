// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5812 (AFC3): the autopilot_panel twin against what the REAL pyEfis
// AutopilotPanel paints. The expected values are not hand-written: they are
// dumped by tools/autopilot_panel_oracle.py into
// test/fixtures/autopilot_panel_pyefis.json (regenerate with the command in
// that script's header; AP_FONT_METRICS comes from the same fixture through
// tools/autopilot_panel_metrics.py). This test runs the REAL editor functions
// against them (see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["AP_GROUPS", "AP_DEFAULT_BUTTONS", "AP_BOXES", "AP_COLORS", "AP_FONT_METRICS",
  "DEJAVU_ASCENT"];
const FUNCS = ["fontScale", "apButtons", "apColor", "apFontMetrics", "apFitToMask", "apText", "editIconGeom", "editIconSvg",
  "apPanelGeom", "apEsc", "apTextSvg", "apRectSvg", "apPanelSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { apButtons, apFontMetrics, apFitToMask, apPanelGeom, apPanelSvg, AP_FONT_METRICS };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/autopilot_panel_pyefis.json", import.meta.url), "utf8"));

const close = (a, b, what, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what, tol) => { for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`, tol); };
// The twin's [css "#rrggbb", alpha 0..1] as Qt's 0..255 rgba.
const rgba = ([css, a]) => [1, 3, 5].map((i) => parseInt(css.slice(i, i + 2), 16)).concat(Math.round(a * 255));
const PX = 96 / 72;

test("the twin's font table is Qt's metrics, value for value", () => {
  const M = FIXTURE.metrics, cols = M.columns;
  for (const [fam, rows] of Object.entries(M.families)) {
    for (const r of rows) {
      const m = twin.apFontMetrics(fam, r[0]);
      assert.equal(m.ascent, r[cols.indexOf("ascent")], `${fam} ${r[0]}px ascent`);
      assert.equal(m.descent, r[cols.indexOf("descent")], `${fam} ${r[0]}px descent`);
      assert.equal(m.height, r[cols.indexOf("height")], `${fam} ${r[0]}px height`);
      for (const mask of M.masks) assert.equal(m.adv[mask], r[cols.indexOf(mask)], `${fam} ${r[0]}px "${mask}"`);
    }
  }
});

test("apFitToMask is helpers.fit_to_mask", () => {
  // Below 3 px Qt's point-size path reports a different line height than its
  // pixel-size path (2 px against 3 at 2 px), which the table cannot carry.
  // No panel draws text that small; those fits are left out, and counted.
  const cases = FIXTURE.fit_cases.filter((c) => c[4] * PX >= 3);
  assert.ok(cases.length >= 1050, `${cases.length} of ${FIXTURE.fit_cases.length}`);
  for (const [fam, mask, w, h, pt] of cases) {
    close(twin.apFitToMask(w, h, mask, fam), pt, `fit ${fam} "${mask}" ${w}x${h}`, 1e-9 * pt);
  }
});

test("buttons parses like parse_buttons", () => {
  assert.deepEqual(twin.apButtons("ap, hdg ,bogus,ALT"), ["HDG", "AP", "ALT"]);
  assert.deepEqual(twin.apButtons(""), []);
  assert.deepEqual(twin.apButtons(["ALT", "AP", "TRK"]), ["TRK", "AP", "ALT"]);
});

test("the fixture walks every exposed option and both layouts", () => {
  const seen = new Set(FIXTURE.cases.flatMap((c) => Object.keys(c.options)));
  for (const k of ["layout", "buttons", "touch_edit", "font_percent", "font_family", "active_color",
    "armed_color", "caution_color", "key_color", "level_color", "text_color", "muted_color",
    "line_color", "target_color", "bg_color"]) assert.ok(seen.has(k), k);
  const counts = new Set(FIXTURE.cases.map((c) => c.buttons.length));
  assert.ok(counts.has(0) && counts.has(3) && counts.has(12), [...counts].join(","));
});

function textOp(t, op, what, alignTol) {
  assert.equal(t.text, op.text, `${what} text`);
  closeRect(t.rect, op.rect, `${what} rect`, 1e-9);
  assert.equal(op.flags & 0x84, 0x84, `${what} AlignCenter`);
  assert.deepEqual(rgba(t.color), op.pen.rgba, `${what} colour`);
  assert.equal(t.family, op.family, `${what} family`);
  assert.equal(t.bold, op.bold, `${what} bold`);
  const px = op.pixel_size > 0 ? op.pixel_size : op.point_size * PX;
  close(t.px, px, `${what} px`, 1e-9 * px);
  close(t.baseline, op.laid_out.y + op.ascent, `${what} baseline`);
  // Centred: Qt's laid-out box centre vs the twin's anchor. Qt snaps the box
  // to whole pixels, so this is the one place a half pixel is allowed.
  close(t.x, op.laid_out.x + op.laid_out.w / 2, `${what} centre x`, alignTol);
}

for (const c of FIXTURE.cases) {
  test(`matches pyEfis AutopilotPanel: ${JSON.stringify(c.options)} @ ${c.w}x${c.h} ${JSON.stringify(c.preview)}`, () => {
    const g = twin.apPanelGeom(c.options, c.preview, c.w, c.h);
    const ops = (who) => c.ops.filter((o) => o.who === who);

    // The panel: its ground, then the message line (or nothing).
    const panel = ops("panel");
    assert.equal(panel[0].op, "fill_rect");
    assert.deepEqual(rgba(g.bg), panel[0].rgba, "background");
    if (g.message) {
      assert.equal(panel.length, 2);
      textOp(g.message, panel[1], "message", 0.5);
      closeRect(g.message.rect, c.message_rect, "message rect", 1e-9);
    } else {
      assert.equal(panel.length, 1, "no message drawn");
    }

    // The keys: which, in what order, where.
    assert.deepEqual(g.keys.map((k) => k.name), c.buttons, "buttons shown");
    for (const k of g.keys) {
      closeRect(k.at, c.button_rects[k.name], `${k.name} geometry`, 0);
      const kops = ops(k.name);
      const [face, ...rest] = kops;
      assert.equal(face.op, "rounded_rect");
      closeRect(k.key, face.rect, `${k.name} key rect`, 1e-9);
      close(k.radius, face.rx, `${k.name} radius`);
      close(k.strokeW, face.pen.width, `${k.name} border width`);
      assert.deepEqual(rgba(k.border), face.pen.rgba, `${k.name} border`);
      assert.deepEqual(rgba(k.face), face.brush.rgba, `${k.name} face`);
      const bar = rest.filter((o) => o.op === "rounded_rect");
      if (!k.bar) assert.equal(bar.length, 0, `${k.name} no bar`);
      else {
        assert.equal(bar.length, 1, `${k.name} one bar`);
        closeRect(k.bar.rect, bar[0].rect, `${k.name} bar rect`, 1e-9);
        close(k.bar.r, bar[0].rx, `${k.name} bar radius`);
        if (k.bar.fill) {
          assert.equal(bar[0].pen.style, "NoPen");
          assert.deepEqual(rgba(k.bar.fill), bar[0].brush.rgba, `${k.name} bar fill`);
        } else {
          assert.equal(bar[0].brush.style, "NoBrush");
          assert.deepEqual(rgba(k.bar.line), bar[0].pen.rgba, `${k.name} bar outline`);
          close(k.bar.lw, bar[0].pen.width, `${k.name} bar outline width`);
        }
      }
      const label = rest.filter((o) => o.op === "text");
      assert.equal(label.length, 1);
      textOp(k.label, label[0], `${k.name} label`, 0.5);
    }

    // The value boxes.
    for (const b of g.boxes) {
      closeRect(b.at, c.box_rects[b.which], `${b.which} geometry`, 0);
      const bops = ops(b.which);
      const texts = bops.filter((o) => o.op === "text");
      textOp(b.label, texts[0], `${b.which} label`, 0.5);
      textOp(b.value, texts[1], `${b.which} value`, 0.5);
      const [panelOp] = bops.filter((o) => o.op === "rounded_rect");
      closeRect(b.panel.rect, panelOp.rect, `${b.which} readout`, 1e-9);
      close(b.panel.radius, panelOp.rx, `${b.which} readout radius`);
      close(b.panel.strokeW, panelOp.pen.width, `${b.which} readout border width`);
      assert.deepEqual(rgba(b.panel.stroke), panelOp.pen.rgba, `${b.which} readout border`);
      assert.deepEqual(panelOp.brush.rgba, [0, 0, 0, 255], "opaque black readout");
      const disc = bops.filter((o) => o.op === "ellipse");
      const paths = bops.filter((o) => o.op === "path");
      if (!b.icon) {
        assert.equal(disc.length + paths.length, 0, `${b.which} no edit icon`);
        continue;
      }
      // The at-rest icon: disc, arc, head, all in the icon's own frame.
      assert.equal(disc.length, 1);
      close(b.icon.x, disc[0].dx, `${b.which} icon x`);
      close(b.icon.y, disc[0].dy, `${b.which} icon y`);
      closeRect({ x: 0, y: 0, w: b.icon.s, h: b.icon.s }, disc[0].rect, `${b.which} disc`, 1e-9);
      assert.deepEqual(disc[0].brush.rgba, [0, 0, 0, 160]);
      const [arc, head] = paths;
      close(b.icon.pw, arc.pen.width, `${b.which} arc width`);
      assert.deepEqual(arc.pen.rgba, [0, 255, 255, 110]);
      assert.equal(arc.pen.cap, "FlatCap");
      const c0 = b.icon.s / 2, first = arc.path[0], last = arc.path[arc.path.length - 1];
      close(c0 + b.icon.r, first[1], "arc start x"); close(c0, first[2], "arc start y");
      // The arc's end is Qt's Bezier end point, on the circle to 1e-9.
      close(c0, last[1], "arc end x", 1e-9); close(c0 + b.icon.r, last[2], "arc end y", 1e-9);
      // Every Bezier point of a quarter arc lies within 0.0273 r of the circle.
      assert.deepEqual(head.brush.rgba, [0, 255, 255, 110]);
      const ex = c0, ey = c0 + b.icon.r, hh = b.icon.head;
      const want = [[ex + hh, ey], [ex - hh * 0.15, ey - hh * 0.75], [ex - hh * 0.15, ey + hh * 0.75]];
      want.forEach(([x, y], i) => { close(x, head.path[i][1], `head ${i} x`); close(y, head.path[i][2], `head ${i} y`); });
    }

    // The markup carries each key once, in order, with its state.
    const svg = twin.apPanelSvg(g);
    const shown = [...svg.matchAll(/data-key="([A-Z]+)" data-state="([a-z]+)"/g)];
    assert.deepEqual(shown.map((m) => m[1]), c.buttons);
    assert.equal((svg.match(/class="ap-box"/g) || []).length, 4);
  });
}
