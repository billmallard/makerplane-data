// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5556: screen-level keys the editor has no UI for -- pyEfis `include:`
// (HMI_ENCODER_BUTTONS carries the knob's encoder/encoder_button keys) and the
// raw `encoder*` keys -- must survive a load -> save round-trip. Before the fix
// fromStoredDoc() kept only layout + instruments, so a Code-pane `include:`
// vanished on the next save and a managed panel could never wire the knob.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FUNCS = ["round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc", "fromStoredDoc"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

function makeSandbox() {
  const state = { schema: { instruments: {} }, groupsById: {} };
  const build = new Function("state", `
    ${body}
    return { toPyefisDoc, fromStoredDoc };
  `);
  return { state, api: build(state) };
}

function roundTrip(yamlText) {
  const { state, api } = makeSandbox();
  const parsed = api.fromStoredDoc(jsyaml.load(yamlText));
  state.screens = parsed.screens;
  state.defaultScreen = parsed.defaultScreen;
  state.screen = parsed.screen;
  return api.toPyefisDoc();
}

const PANEL = `
main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: flight_plan
        row: 1
        column: 2
        span: {rows: 105, columns: 196}
        options:
          procedures_db_path: /data/makerplane-data/procedures/current/procedures.sqlite
          encoder_order: 1
    include:
      - HMI_ENCODER_BUTTONS
    encoder_timeout: 5000
`;

test("screen-level include and encoder keys survive load -> save", () => {
  const sc = roundTrip(PANEL).screens.PANEL;
  assert.deepEqual(sc.include, ["HMI_ENCODER_BUTTONS"]);
  assert.equal(sc.encoder_timeout, 5000);
});

test("unknown instrument options still pass through (procedures_db_path, encoder_order)", () => {
  const opts = roundTrip(PANEL).screens.PANEL.instruments[0].options;
  assert.equal(opts.procedures_db_path, "/data/makerplane-data/procedures/current/procedures.sqlite");
  assert.equal(opts.encoder_order, 1);
});

test("a stored screen cannot override the keys the editor owns", () => {
  const sc = roundTrip(PANEL.replace("title: PANEL", "title: SOMETHING_ELSE")
                            .replace("module: pyefis.screens.screenbuilder", "module: other")).screens.PANEL;
  assert.equal(sc.module, "pyefis.screens.screenbuilder");
  assert.equal(sc.title, "PANEL");
});

test("a screen with no extra keys serialises exactly as before", () => {
  const plain = PANEL.replace(/    include:\n      - HMI_ENCODER_BUTTONS\n    encoder_timeout: 5000\n/, "");
  const sc = roundTrip(plain).screens.PANEL;
  assert.deepEqual(Object.keys(sc), ["module", "title", "layout", "instruments"]);
});
