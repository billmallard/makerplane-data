// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// LAYADV1 (AER-6539): the advisory travels with the layout. The save path
// runs the REAL editor functions out of editor.html (test/support/extract.mjs);
// the served path runs the REAL src/aircraft_refs.ts and src/device_prefs.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import * as adv from "../public/layout_advisory.js";
import { layoutAdvisory, advisoryComment } from "../public/layout_advisory.js";
import { resolveAircraftRefs } from "../src/aircraft_refs.ts";
import { serveDeviceConfig } from "../src/device_prefs.ts";
import { editorSource, extractFunction } from "./support/extract.mjs";

const at = (type, row = 0, column = 0, rows = 10, columns = 10, extra = {}) =>
  ({ type, row, column, span: { rows, columns }, ...extra });
const doc = (screens) => ({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: Object.keys(screens)[0] },
  screens: Object.fromEntries(Object.entries(screens).map(([name, instruments]) =>
    [name, { module: "pyefis.screens.screenbuilder", title: name, layout: { rows: 110, columns: 200 }, instruments }])),
});

// --- the editor's save path -----------------------------------------------

const FUNCS = ["round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc",
  "currentAdvisory", "serializeDesign", "fromStoredDoc"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");
function editor(schemaTypes) {
  const state = {
    schema: { instruments: Object.fromEntries(schemaTypes.map((t) => [t, {}])) },
    groupsById: {}, layoutAdvisory: adv, defaultScreen: "PFD",
    screen: { width: 1280, height: 800, inches: null },
    screens: [
      { name: "PFD", layout: { rows: 110, columns: 200 }, instruments: [at("atitude_indicator")] },
      { name: "RADIO", layout: { rows: 110, columns: 200 }, instruments: [at("cas_window")] },
    ],
  };
  const api = new Function("state", "window", "jsyaml", `${body}\nreturn { serializeDesign, toPyefisDoc, fromStoredDoc };`)(state, { jsyaml }, jsyaml);
  return { state, api };
}

test("save: the YAML opens with the advisory and parses to the same panel", () => {
  const { api } = editor(["atitude_indicator", "cas_window", "cas_marker"]);
  const yaml = api.serializeDesign();
  assert.match(yaml, /^# Layout advisory, written by the configurator/);
  assert.match(yaml, /WARNING: Screen "PFD" carries an attitude instrument/);
  assert.deepEqual(jsyaml.load(yaml), api.toPyefisDoc());
  // Load it back and save again: the same bytes, header and all.
  const { state, api: api2 } = editor(["atitude_indicator", "cas_window", "cas_marker"]);
  const parsed = api2.fromStoredDoc(jsyaml.load(yaml));
  Object.assign(state, { screens: parsed.screens, defaultScreen: parsed.defaultScreen, screen: parsed.screen });
  assert.equal(api2.serializeDesign(), yaml);
});

test("save: no header before the schema carries a CAS type, or without the module", () => {
  const a = editor(["atitude_indicator"]);
  assert.match(a.api.serializeDesign(), /^main:/);
  const b = editor(["atitude_indicator", "cas_window"]);
  b.state.layoutAdvisory = null;
  assert.match(b.api.serializeDesign(), /^main:/);
});

// --- the served path ------------------------------------------------------

const PROFILE = JSON.stringify({ schema_version: 1, values: {}, meta: { fuel: { schema: 2, tanks: [
  { id: "tk_l", label: "L MAIN", role: "main", slot: 1, feeds: [], capacity: 20, unusable: 0 }] } } });

test("served: a bound tank reference re-dumps the panel and keeps the advisory header", async () => {
  const d = doc({ PFD: [at("atitude_indicator"), at("vertical_bar_gauge", 0, 0, 10, 10, { options: { tank: "tk_l" } })], R: [at("cas_marker")] });
  const stored = advisoryComment(layoutAdvisory(d)) + jsyaml.dump(d, { lineWidth: 100, noRefs: true });
  const r = resolveAircraftRefs(stored, PROFILE);
  assert.equal(r.bindings.length, 1, "the reference must bind, or this test proves nothing");
  assert.ok(r.yaml.startsWith(advisoryComment(layoutAdvisory(d))));
  assert.equal(jsyaml.load(r.yaml).screens.PFD.instruments[1].options.dbkey, "FUELQ1");
  const served = await serveDeviceConfig(stored, 3, PROFILE);
  assert.match(served.body, /^# Layout advisory/);
});

test("served: a stored panel with no header is served exactly as before", () => {
  const d = doc({ PFD: [at("vertical_bar_gauge", 0, 0, 10, 10, { options: { tank: "tk_l" } })] });
  const stored = jsyaml.dump(d, { lineWidth: 100, noRefs: true });
  const r = resolveAircraftRefs(stored, PROFILE);
  assert.equal(r.bindings.length, 1);
  assert.match(r.yaml, /^main:/);
});
