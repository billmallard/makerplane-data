// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6286 (AFC4): the autopilot cues the editor's tape twins draw against
// what the REAL pyEfis tapes paint for them: the IASSEL box and bug on the
// airspeed tape, the ALTSEL box's capture look and the VSSEL bug on the
// altimeter tape, the VSSEL bug on vsi_pfd. Expected values come from
// tools/ap_tape_cues_oracle.py (test/fixtures/ap_tape_cues_pyefis.json). The
// editor's tapes have their own approximate scale, so each cue's shape is
// pinned, and its place only relative to its own apex.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["AP_CUE_PREVIEW", "AP_FONT_METRICS", "DEJAVU_ASCENT"];
const FUNCS = ["apColor", "apFontMetrics", "apFitToMask", "editIconGeom", "editIconSvg", "apEsc",
  "tapeBugPath", "apCueOn", "apTapeCues", "apTapeCuesSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { apTapeCues, apTapeCuesSvg, AP_CUE_PREVIEW };
`)();

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/ap_tape_cues_pyefis.json", import.meta.url), "utf8"));

const close = (a, b, what, tol = 1e-9) =>
  assert.ok(Math.abs(a - b) <= tol, `${what}: twin ${a} vs pyEfis ${b}`);
const closeRect = (a, b, what) => { for (const k of ["x", "y", "w", "h"]) close(a[k], b[k], `${what}.${k}`); };
const rgba = ([css, a]) => [1, 3, 5].map((i) => parseInt(css.slice(i, i + 2), 16)).concat(Math.round(a * 255));
const label = (c) => `${c.type} ${c.state} ${c.w}x${c.h} ${JSON.stringify(c.options)}`;

// The device's bugs: the 7-point closed polygons standing on the label edge
// (the edit icons' paths are smaller and start elsewhere).
function deviceBugs(c) {
  return c.ops.filter((o) => o.op === "path" && o.path.length === 8 && (o.path[0][0] === c.w || o.path[0][0] === 0));
}
// What the device shows for the cue the preview draws on this tape: the IAS
// box while IAS is reported, the ALTSEL box, the VSSEL bug while VS is active.
function deviceBox(c) {
  const who = c.type === "altimeter_tape" ? "AltSelBox" : "QWidget";
  return c.ops.find((o) => o.op === "rounded_rect" && o.who === who && o.rect.x === 4.5 && o.rect.y === 4.5);
}

for (const c of FIXTURE.cases) {
  test(`cue shapes match pyEfis: ${label(c)}`, () => {
    // The preview shows IAS active, the ALTSEL box capturing and VS active;
    // the armed, plain and off states are the device's alone.
    const preview = { airspeed_tape: ["ias_active"], altimeter_tape: ["alt_capture", "vs_active"],
      vsi_pfd: ["vs_active"] }[c.type];
    if (!preview.includes(c.state)) return;
    const bugs = deviceBugs(c);
    // The bug for the preview's target. On the altimeter tape the ALTSEL bug
    // (alt_bug_enabled) is not part of this twin; the VSSEL bug is painted
    // after it, so it is the last.
    const ours = c.type === "airspeed_tape" ? bugs.slice(0, c.ias_shown ? 1 : 0)
      : c.type === "altimeter_tape" ? (c.vs_bug && c.state === "vs_active" ? bugs.slice(-1) : [])
      : bugs;
    const apex = ours.length ? ours[0].path[3][1] : 0;
    const g = twin.apTapeCues(c.type, c.options, c.w, c.h, () => apex, c.h);
    assert.equal(g.bugs.length, ours.length, "bug count");
    g.bugs.forEach((b, i) => {
      const want = ours[i].path.slice(0, 7);
      b.points.forEach(([x, y], j) => { close(x, want[j][0], `bug ${i} pt ${j} x`); close(y, want[j][1], `bug ${i} pt ${j} y`); });
      assert.deepEqual(rgba(b.color), ours[i].brush.rgba, `bug ${i} colour`);
    });
    if (c.type === "altimeter_tape" && c.state !== "alt_capture") return;   // a plain ALTSEL box
    const box = deviceBox(c);
    const gbox = g.boxes.find((b) => b.which === (c.type === "altimeter_tape" ? "altsel" : "iassel"));
    if (!box) { assert.equal(gbox, undefined, "no box"); return; }
    assert.ok(gbox, "box drawn");
    closeRect(gbox.rect, box.rect, "box rect");
    close(gbox.radius, box.rx, "box radius");
    close(gbox.strokeW, box.pen.width, "box pen width");
    assert.deepEqual(rgba(gbox.stroke), box.pen.rgba, "box border colour");
    close(gbox.fill * 255, box.brush.rgba[3], "box fill alpha");
    const who = c.type === "altimeter_tape" ? "AltSelBox" : "QWidget";
    const txt = c.ops.find((o) => o.op === "text" && o.who === who && o.rect.x === 4.5 && o.rect.y === 4.5);
    assert.equal(gbox.text, txt.text, "box text");
    assert.deepEqual(rgba(gbox.color), txt.pen.rgba, "box text colour");
    if (c.type === "altimeter_tape") close(gbox.pt, txt.point_size, "ALTSEL font point size", 1e-9 * txt.point_size);
    else assert.equal(gbox.px, txt.pixel_size, "IASSEL font pixel size");
    const icons = c.ops.filter((o) => o.op === "path" && o.who === who && o.path.length === 10);
    // The box's own edit icon: the arc's radius and stroke.
    if (gbox.icon) {
      assert.ok(icons.length, "device draws an edit icon");
      const arc = icons[icons.length - 1];
      close(gbox.icon.pw, arc.pen.width, "edit icon stroke");
    } else if (c.type === "airspeed_tape") {
      assert.equal(icons.length, 0, "no edit icon");
    }
  });
}

test("the IAS box and bug follow ias_bug; the VSSEL bug follows vs_bug", () => {
  const y = () => 200;
  assert.equal(twin.apTapeCues("airspeed_tape", {}, 70, 400, y, 370).boxes.length, 1);
  assert.equal(twin.apTapeCues("airspeed_tape", { ias_bug: false }, 70, 400, y, 370).boxes.length, 0);
  assert.equal(twin.apTapeCues("altimeter_tape", { dbkey: "VS" }, 90, 520, y).bugs.length, 1);
  assert.equal(twin.apTapeCues("altimeter_tape", { dbkey: "VS", vs_bug: false }, 90, 520, y).bugs.length, 0);
  assert.equal(twin.apTapeCues("altimeter_tape", {}, 90, 520, y).bugs.length, 0, "an ALT tape has no VSSEL bug by default");
  assert.equal(twin.apTapeCues("altimeter_tape", {}, 90, 520, y).boxes.length, 1, "an ALT tape has the ALTSEL box");
  assert.equal(twin.apTapeCues("altimeter_tape", { dbkey: "VS" }, 90, 520, y).boxes.length, 0, "a VS tape has none");
  assert.equal(twin.apTapeCues("vsi_pfd", {}, 36, 300, y).bugs.length, 1);
  assert.equal(twin.apTapeCues("vsi_pfd", { vs_bug: false }, 36, 300, y).bugs.length, 0);
  assert.equal(twin.apTapeCuesSvg(twin.apTapeCues("vsi_pfd", { vs_bug: false }, 36, 300, y)), "");
});

test("a bug past the box pegs at the box's bottom edge", () => {
  const g = twin.apTapeCues("airspeed_tape", {}, 70, 400, () => -50, 370);
  const zone = Math.max(16, Math.round(70 * 0.30)) + 9;
  close(g.bugs[0].points[3][1], zone + 1, "pegged apex");
});
