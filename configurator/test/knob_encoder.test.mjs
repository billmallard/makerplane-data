// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// G96 (makerplane-data#19): the knob editor, Phase A. The screen binding
// (encoder / encoder_button / encoder_timeout in screens.<name>), the ring
// (encoder_order, generated, never typed) and the canvas badges that number
// the ring. The ring rules are pyEfis's: EncoderController sorts the enrolled
// instruments by encoder_order with a stable sort, enrols only types with
// enc_selectable (schema `encoder.selectable`), and is inert unless both keys
// are set (screenbuilder_encoder.configure_inputs).
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["KNOB_DEFAULTS", "KNOB_ORDER_STEP"].map((c) => extractConst(src, c)).join("\n");
const FUNCS = [
  "knobCapable", "knobOrderOf", "knobRing", "setKnobReachable", "moveKnobRing",
  "knobBindingMode", "setKnobBinding", "knobKeyChoices", "knobBadges",
  "round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc",
];
const body = CONSTS + "\n" + FUNCS.map((f) => extractFunction(src, f)).join("\n\n");

// The capability block as schema v4 exports it (pyEfis #539): gauges and
// buttons are selectable, the HSI is not.
const enc = (selectable) => ({ selectable, editable: selectable, outer_ring: false, options: [] });
const ORDER = { type: "integer", default: null, label: "Knob order", step: 1 };
const SCHEMA = {
  common_options: {},
  instruments: {
    arc_gauge: { label: "Arc gauge", options: { encoder_order: ORDER }, encoder: enc(true) },
    button: { label: "Button", options: { encoder_order: ORDER }, encoder: enc(true) },
    numeric_display: { label: "Numeric display", options: { encoder_order: ORDER }, encoder: enc(true) },
    horizontal_situation_indicator: { label: "HSI", options: {}, encoder: enc(false) },
  },
};

function makeEditor(instruments, extra) {
  const sc = { name: "PANEL", layout: { rows: 110, columns: 200 }, instruments, ...(extra ? { extra } : {}) };
  const state = {
    schema: SCHEMA, groupsById: {}, screens: [sc], defaultScreen: "PANEL",
    screen: { width: 1280, height: 800, inches: null },
    fixKeys: ["ENC1", "ENC3", "ENC10", "ENC2", "BTN3", "BTN1", "ENCODER", "BTN12"].map((key) => ({ key })),
  };
  const api = new Function("state", `${body}
    return { knobRing, setKnobReachable, moveKnobRing, knobBindingMode, setKnobBinding,
             knobKeyChoices, knobBadges, toPyefisDoc };`)(state);
  return { state, sc, api };
}

const inst = (type, name, options = {}) =>
  ({ type, row: 0, column: 0, span: { rows: 10, columns: 10 }, options: { name, ...options } });
const byName = (instruments, badges) =>
  Object.fromEntries([...badges].map(([i, n]) => [instruments[i].options.name, n]));

test("acceptance: toggle three reachable, reorder, badges 1/2/3 and saved encoder_order follow", () => {
  const insts = [inst("arc_gauge", "RPM"), inst("numeric_display", "COM1"), inst("button", "MENU")];
  const { sc, api } = makeEditor(insts);
  for (const i of [0, 1, 2]) api.setKnobReachable(insts, i, true);
  assert.deepEqual(insts.map((x) => x.options.encoder_order), [10, 20, 30]);
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { RPM: 1, COM1: 2, MENU: 3 });

  api.moveKnobRing(insts, 2, 0);                 // MENU to the front of the ring
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { MENU: 1, RPM: 2, COM1: 3 });
  assert.deepEqual(insts.map((x) => x.options.encoder_order), [20, 30, 10]);

  // The saved YAML carries exactly those integers, so the device's ascending
  // sort walks MENU, RPM, COM1 -- the order the badges show.
  const saved = jsyaml.load(jsyaml.dump(api.toPyefisDoc())).screens.PANEL.instruments;
  const walk = [...saved].sort((a, b) => a.options.encoder_order - b.options.encoder_order).map((x) => x.options.name);
  assert.deepEqual(walk, ["MENU", "RPM", "COM1"]);
  assert.equal(sc.instruments, insts);
});

test("taking an instrument off the ring removes its encoder_order and renumbers nothing else", () => {
  const insts = [inst("arc_gauge", "A"), inst("arc_gauge", "B"), inst("arc_gauge", "C")];
  const { api } = makeEditor(insts);
  for (const i of [0, 1, 2]) api.setKnobReachable(insts, i, true);
  api.setKnobReachable(insts, 1, false);
  assert.equal("encoder_order" in insts[1].options, false);
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { A: 1, C: 2 });
  assert.equal(insts[2].options.encoder_order, 30);
});

test("joining the ring keeps a hand-authored panel's numbers; only a reorder renumbers", () => {
  const insts = [inst("arc_gauge", "A", { encoder_order: 600031 }), inst("arc_gauge", "B", { encoder_order: 100011 }),
                 inst("arc_gauge", "C")];
  const { api } = makeEditor(insts);
  api.setKnobReachable(insts, 2, true);
  assert.deepEqual(insts.map((x) => x.options.encoder_order), [600031, 100011, 600041]);
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { B: 1, A: 2, C: 3 });
  api.setKnobReachable(insts, 0, true);         // already on: untouched
  assert.equal(insts[0].options.encoder_order, 600031);
  api.moveKnobRing(insts, 0, 2);
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { A: 1, C: 2, B: 3 });
  assert.deepEqual(insts.map((x) => x.options.encoder_order), [10, 30, 20]);
});

test("the ring follows the device: stable ties, only selectable types, Code-pane strings", () => {
  const insts = [
    inst("arc_gauge", "T2", { encoder_order: 5 }),
    inst("horizontal_situation_indicator", "HSI", { encoder_order: 1 }),   // not enc_selectable: ignored
    inst("arc_gauge", "T1", { encoder_order: "5" }),                       // a quoted scalar still counts
    inst("arc_gauge", "BLANK", { encoder_order: "" }),                     // dropped on save: not on the ring
    { type: "group", id: "g", row: 0, column: 0, span: { rows: 1, columns: 1 } },
    inst("button", "FIRST", { encoder_order: -3 }),
  ];
  const { api } = makeEditor(insts);
  assert.deepEqual(byName(insts, api.knobBadges(insts)), { FIRST: 1, T2: 2, T1: 3 });
});

test("an out-of-range move is a no-op", () => {
  const insts = [inst("arc_gauge", "A"), inst("arc_gauge", "B")];
  const { api } = makeEditor(insts);
  api.setKnobReachable(insts, 0, true); api.setKnobReachable(insts, 1, true);
  api.moveKnobRing(insts, 0, 5);
  api.moveKnobRing(insts, -1, 0);
  assert.deepEqual(insts.map((x) => x.options.encoder_order), [10, 20]);
});

test("binding the knob emits encoder / encoder_button / encoder_timeout into screens.<name>", () => {
  const { sc, api } = makeEditor([inst("arc_gauge", "A")]);
  assert.equal(api.knobBindingMode(sc), null);
  api.setKnobBinding(sc, { encoder: "ENC3", encoder_button: "BTN3", encoder_timeout: 10000 });
  assert.equal(api.knobBindingMode(sc), "keys");
  const out = jsyaml.load(jsyaml.dump(api.toPyefisDoc())).screens.PANEL;
  assert.equal(out.encoder, "ENC3");
  assert.equal(out.encoder_button, "BTN3");
  assert.equal(out.encoder_timeout, 10000);
  assert.equal(out.module, "pyefis.screens.screenbuilder");

  api.setKnobBinding(sc, { encoder: undefined, encoder_button: undefined, encoder_timeout: undefined });
  assert.equal(sc.extra, undefined);
  const plain = api.toPyefisDoc().screens.PANEL;
  for (const k of ["encoder", "encoder_button", "encoder_timeout"]) assert.equal(k in plain, false);
});

test("keys set here serialise after an include, so they win the device's YAML-order merge", () => {
  const { sc, api } = makeEditor([], { encoder: "ENC1", include: ["HMI_ENCODER_BUTTONS"], encoder_outer: "ENC4" });
  assert.equal(api.knobBindingMode({ extra: { include: "HMI_ENCODER_BUTTONS" } }), "include");
  api.setKnobBinding(sc, { encoder: "ENC2", encoder_button: "BTN3", encoder_timeout: 10000 });
  const keys = Object.keys(api.toPyefisDoc().screens.PANEL);
  assert.ok(keys.indexOf("include") < keys.indexOf("encoder"), keys.join(","));
  assert.equal(api.toPyefisDoc().screens.PANEL.encoder_outer, "ENC4");   // keys the panel doesn't own pass through
});

test("key choices come from the FIX catalogue, numerically ordered, never invented", () => {
  const { api, state } = makeEditor([]);
  assert.deepEqual(api.knobKeyChoices("ENC", "ENC3"), ["ENC1", "ENC2", "ENC3", "ENC10"]);
  assert.deepEqual(api.knobKeyChoices("BTN", "BTN3"), ["BTN1", "BTN3", "BTN12"]);
  assert.deepEqual(api.knobKeyChoices("ENC", "ENC9"), ["ENC1", "ENC2", "ENC3", "ENC10", "ENC9"]);
  state.fixKeys = undefined;                     // no catalogue: only what the screen already names
  assert.deepEqual(api.knobKeyChoices("ENC", "ENC3"), ["ENC3"]);
});
