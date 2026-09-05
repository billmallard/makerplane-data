// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-345 acceptance criterion: an instrument placed into a tab_section's
// active tab via the canvas drop-target, and one hand-authored as YAML and
// pasted into the Code pane, must save to byte-identical output. Phase A
// (makerplane-data#40) already proved the save/load recursion works --
// verified directly on issue #44 by tracing fromStoredDoc()/toScreenInstrument()
// on origin/dev -- this test pins that proof down as a permanent regression
// guard now that Phase B's canvas changes (drop-target + nested twin) live
// nearby. It deliberately does NOT touch renderCanvas()/buildContainerTwin();
// those need a browser and aren't what's at risk here -- the wire format is.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs / tools/twin_proto.mjs),
// so this fails the moment Phase B code accidentally changes
// toScreenInstrument()/fromStoredDoc()/expandGroups()'s behaviour.

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FUNCS = ["round1", "expandGroups", "toScreenInstrument", "toPyefisDoc", "fromStoredDoc"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

// A fresh sandbox per scenario -- `state` is a free variable the extracted
// functions close over (the same trick editor.html itself relies on, and
// tools/twin_proto.mjs's proto page uses to stub `state`).
function makeSandbox() {
  const state = {
    schema: { instruments: { tab_section: { containers: [{ name: "tabs", label: "Tabs" }] } } },
    groupsById: {},
  };
  const build = new Function("state", `
    ${body}
    return { toPyefisDoc, fromStoredDoc };
  `);
  return { state, api: build(state) };
}

const HAND_AUTHORED_YAML = `
main:
  screenWidth: 1280
  screenHeight: 800
  defaultScreen: PANEL
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: tab_section
        row: 10
        column: 10
        span: {rows: 60, columns: 100}
        tabs:
          - label: Tab 1
            layout: {rows: 110, columns: 200}
            instruments:
              - type: moving_map
                row: 20
                column: 30
                span: {rows: 40, columns: 60}
`;

test("a nested instrument placed via the drop-target round-trips byte-identically with hand-authored YAML", () => {
  // Path A: what the container-scoped drop-target produces (editor.html's
  // canvas drop handler + instrumentForDrop()) -- a plain instrument record
  // pushed straight into tabSection.tabs[activeTab].instruments, in the
  // tab's own coordinate space (AER-345 sec 4b), plus the editor-only
  // `activeTab` UI-state field that must never reach the saved doc.
  const a = makeSandbox();
  a.state.screens = [{
    name: "PANEL",
    layout: { rows: 110, columns: 200 },
    instruments: [{
      type: "tab_section",
      row: 10, column: 10, span: { rows: 60, columns: 100 },
      options: {},
      activeTab: 0,
      tabs: [{
        label: "Tab 1",
        layout: { rows: 110, columns: 200 },
        instruments: [{
          type: "moving_map",
          row: 20, column: 30, span: { rows: 40, columns: 60 },
          options: {},
        }],
      }],
    }],
  }];
  a.state.defaultScreen = "PANEL";
  a.state.screen = { width: 1280, height: 800 };
  const yamlA = jsyaml.dump(a.api.toPyefisDoc(), { lineWidth: 100, noRefs: true });

  // Path B: the same content hand-authored as YAML, pasted into the Code
  // pane (fromStoredDoc()) and immediately resaved.
  const b = makeSandbox();
  const parsed = b.api.fromStoredDoc(jsyaml.load(HAND_AUTHORED_YAML));
  b.state.screens = parsed.screens;
  b.state.defaultScreen = parsed.defaultScreen;
  b.state.screen = parsed.screen;
  const yamlB = jsyaml.dump(b.api.toPyefisDoc(), { lineWidth: 100, noRefs: true });

  assert.equal(yamlA, yamlB);
  assert.ok(!yamlA.includes("activeTab"), "editor-only UI state must never reach the saved doc");
});
