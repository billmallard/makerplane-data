// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// makerplane-data#10: a quoted scalar in the Code pane (`bg_opacity: '5'`) or a
// legacy save used to persist verbatim and reach the device as a string. The
// persist path (toScreenInstrument) now coerces string values of schema
// number/integer options with Number() and drops ones that aren't numeric;
// string-typed and undeclared options pass through untouched.
//
// Runs the REAL functions out of editor.html (see test/support/extract.mjs),
// against a schema stub whose option specs copy the live schema.json shapes.

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FUNCS = ["round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc", "fromStoredDoc"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

function makeSandbox() {
  const state = {
    schema: {
      common_options: {
        font_percent: { type: "number", default: null, min: 0, max: 1, step: 0.01 },
        font_family: { type: "enum", enum: ["DejaVu Sans Condensed", "B612"] },
      },
      instruments: {
        altimeter_tape: {
          options: {
            bg_opacity: { type: "integer", default: 100, min: 0, max: 100, step: 1 },
            font_mask: { type: "string", default: "00000" },
            dbkey: { type: "fixkey", default: "ALT" },
            bg_color: { type: "color", default: "#000000" },
            edge_fade: { type: "boolean", default: false },
          },
        },
        tab_section: { containers: [{ name: "tabs", label: "Tabs" }] },
      },
    },
    groupsById: {},
  };
  const build = new Function("state", `
    ${body}
    return { toPyefisDoc, fromStoredDoc };
  `);
  return { state, api: build(state) };
}

// Load YAML as the Code pane does, then save as the editor does.
function loadAndSave(yamlText) {
  const s = makeSandbox();
  const parsed = s.api.fromStoredDoc(jsyaml.load(yamlText));
  s.state.screens = parsed.screens;
  s.state.defaultScreen = parsed.defaultScreen;
  s.state.screen = parsed.screen;
  return s.api.toPyefisDoc();
}

const QUOTED_YAML = `
main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: altimeter_tape
        row: 0
        column: 0
        span: {rows: 50, columns: 20}
        options:
          bg_opacity: '5'
          font_percent: ' 0.45 '
          font_mask: '00000'
          dbkey: 'ALT'
          bg_color: '#112233'
          edge_fade: true
          font_family: B612
          undeclared_opt: '7'
      - type: tab_section
        row: 60
        column: 0
        span: {rows: 50, columns: 100}
        tabs:
          - label: Tab 1
            layout: {rows: 110, columns: 200}
            instruments:
              - type: altimeter_tape
                row: 0
                column: 0
                span: {rows: 50, columns: 20}
                options: {bg_opacity: '80'}
`;

test("schema-numeric quoted scalars from the Code pane persist as numbers", () => {
  const doc = loadAndSave(QUOTED_YAML);
  const opts = doc.screens.PANEL.instruments[0].options;
  assert.strictEqual(opts.bg_opacity, 5);          // integer option
  assert.strictEqual(opts.font_percent, 0.45);     // number option from common_options
  // and the saved YAML carries them unquoted
  const saved = jsyaml.dump(doc, { lineWidth: 100, noRefs: true });
  assert.match(saved, /bg_opacity: 5\n/);
  assert.doesNotMatch(saved, /bg_opacity: '5'/);
});

test("string-typed, non-numeric-typed and undeclared options are untouched", () => {
  const opts = loadAndSave(QUOTED_YAML).screens.PANEL.instruments[0].options;
  assert.strictEqual(opts.font_mask, "00000");     // string option that looks numeric
  assert.strictEqual(opts.dbkey, "ALT");
  assert.strictEqual(opts.bg_color, "#112233");
  assert.strictEqual(opts.edge_fade, true);
  assert.strictEqual(opts.font_family, "B612");
  assert.strictEqual(opts.undeclared_opt, "7");    // no schema type -> no guess
});

test("instruments nested in a tab_section are coerced too", () => {
  const tab = loadAndSave(QUOTED_YAML).screens.PANEL.instruments[1].tabs[0];
  assert.strictEqual(tab.instruments[0].options.bg_opacity, 80);
});

test("blank or non-numeric strings on numeric options are dropped, real numbers kept", () => {
  const s = makeSandbox();
  s.state.screen = { width: 1280, height: 800 };
  s.state.defaultScreen = "PANEL";
  // An inspector-edited record as a legacy design left it: values in options
  // exactly as the old text input stored them.
  s.state.screens = [{
    name: "PANEL",
    layout: { rows: 110, columns: 200 },
    instruments: [{
      type: "altimeter_tape", row: 0, column: 0, span: { rows: 50, columns: 20 },
      options: { bg_opacity: "abc", font_percent: "", font_mask: "" },
    }, {
      type: "altimeter_tape", row: 0, column: 30, span: { rows: 50, columns: 20 },
      options: { bg_opacity: 42, font_percent: 0.5 },
    }],
  }];
  const [bad, good] = s.api.toPyefisDoc().screens.PANEL.instruments;
  assert.ok(!("bg_opacity" in bad.options), "NaN must not reach the device");
  assert.ok(!("font_percent" in bad.options), "blank must not become 0");
  assert.strictEqual(bad.options.font_mask, "");   // string option keeps its blank
  assert.deepEqual(good.options, { bg_opacity: 42, font_percent: 0.5 });
});

test("a coerced design is stable across a second load/save", () => {
  const once = jsyaml.dump(loadAndSave(QUOTED_YAML), { lineWidth: 100, noRefs: true });
  const twice = jsyaml.dump(loadAndSave(once), { lineWidth: 100, noRefs: true });
  assert.equal(twice, once);
});
