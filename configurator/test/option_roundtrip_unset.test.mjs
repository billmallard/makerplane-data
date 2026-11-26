// SPDX-License-Identifier: AGPL-3.0-or-later
//
// G100 / AER-6681 (pyEfis #580): a checkbox or select toggled away and back
// to what it showed when the panel opened restores the option as it was. An
// unset option goes back to unset, not to an explicit copy of the schema
// default, because the schema default is not always what pyEfis does when the
// option is unset:
//   - a VS altimeter_tape shows Baro and Selected-altitude ticked (schema
//     default true) but pyEfis draws neither unless the option is set; an
//     explicit true from a round trip put both boxes on the VS tape.
//   - an ALT tape with show_baro unset yields to the screen's own BARO
//     readout; an explicit true does not, so a round trip gave it two.
// Runs the REAL optionField()/setOpt() out of editor.html (brace-matched,
// test/support/extract.mjs); the specs are the pyEfis registry's.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const FNS = ["roundTrip", "setOpt", "refWrites", "optionField"];

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.handlers = {};
    this.className = ""; this.textContent = ""; this.title = ""; this.value = ""; this.checked = false;
  }
  setAttribute() {}
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(ev, fn) { this.handlers[ev] = fn; }
}
const document = { createElement: (t) => new El(t) };

// The pyEfis registry specs (screenbuilder_factory.py, altimeter_tape).
const SPECS = {
  show_baro: { type: "boolean", default: true, label: "Baro setting box" },
  altsel_box: { type: "boolean", default: true, label: "Selected-altitude box" },
};

function editor() {
  const redraws = [];
  const f = new Function("document", "state", "renderCanvas",
    FNS.map((n) => extractFunction(src, n)).join("\n\n") + `\nreturn { ${FNS.join(", ")} };`)(
    document, { aircraftRefs: null }, () => redraws.push("canvas"));
  return { ...f, redraws };
}

// The control optionField builds, and a way to change it the way a click does.
function control(ed, inst, name, spec = SPECS[name]) {
  const inp = ed.optionField(inst, name, spec).children.at(-1);
  return {
    inp,
    set(v) { if (inp.type === "checkbox") inp.checked = v; else inp.value = v; inp.handlers.change(); },
  };
}

for (const name of ["show_baro", "altsel_box"]) {
  test(`VS tape: ${name} off and back on leaves the option unset`, () => {
    const ed = editor();
    const inst = { type: "altimeter_tape", options: { dbkey: "VS" } };
    const c = control(ed, inst, name);
    assert.equal(c.inp.checked, true);              // the schema default is shown
    c.set(false);
    assert.equal(inst.options[name], false);
    c.set(true);
    assert.equal(Object.hasOwn(inst.options, name), false,
      `an explicit ${name}: true would put the box on the VS tape`);
    assert.deepEqual(inst.options, { dbkey: "VS" });
  });
}

test("ALT tape: show_baro off and back on keeps yielding to the screen's BARO readout", () => {
  const ed = editor();
  const inst = { type: "altimeter_tape", options: {} };
  const c = control(ed, inst, "show_baro");
  c.set(false); c.set(true);
  assert.deepEqual(inst.options, {});
});

test("a change that sticks is still written, both ways", () => {
  const ed = editor();
  const vs = { type: "altimeter_tape", options: { dbkey: "VS" } };
  control(ed, vs, "show_baro").set(false);
  assert.equal(vs.options.show_baro, false);
  const old = { type: "altimeter_tape", options: { show_baro: false } };
  control(ed, old, "show_baro").set(true);
  assert.equal(old.options.show_baro, true);
});

test("an option the file already sets is restored, not deleted, by a round trip", () => {
  const ed = editor();
  const inst = { type: "altimeter_tape", options: { show_baro: true } };
  const c = control(ed, inst, "show_baro");
  c.set(false); c.set(true);
  assert.equal(inst.options.show_baro, true);
});

test("a select round trip also restores unset", () => {
  const ed = editor();
  const inst = { type: "x", options: {} };
  const c = control(ed, inst, "mode", { type: "enum", enum: ["a", "b"], default: "a" });
  assert.equal(c.inp.value, "a");
  c.set("b");
  assert.equal(inst.options.mode, "b");
  c.set("a");
  assert.deepEqual(inst.options, {});
});
