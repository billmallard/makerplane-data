// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5610 (CERT17): every panel carries the PFD DATA flag
// (`data_annunciation`) on its boot screen by default. pyEfis never boots to
// Data Status, so the flag is the only cue that a pack is expired or that a
// config pull changed the panel, and the only way to that screen. A new panel
// gets one, an existing panel gains one on its next save, and a builder who
// removes it is warned; the save then records main.dataFlag: false so the next
// open does not put it back.
//
// Runs the REAL functions out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FUNCS = ["round1", "expandGroups", "coerceNumericOptions", "toScreenInstrument", "toPyefisDoc",
  "fromStoredDoc", "bootScreen", "hasDataFlag", "dataFlagInstrument", "ensureDataFlag",
  "dataFlagWarning", "renderDataFlagWarning"];
const body = FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");

function makeSandbox() {
  const els = {
    "dataflag-warn": { hidden: true, classList: {
      contains: (c) => c === "hidden" && els["dataflag-warn"].hidden,
      toggle(c, on) { if (c === "hidden") els["dataflag-warn"].hidden = on; } } },
    "dataflag-msg": { textContent: "" },
  };
  const $ = (id) => els[id];
  // The editor's initial state: one empty 110 x 200 PANEL screen.
  const state = {
    schema: { instruments: {} }, groupsById: {},
    screen: { width: 1280, height: 800, inches: 10.1 },
    screens: [{ name: "PANEL", layout: { rows: 110, columns: 200 }, instruments: [] }],
    defaultScreen: "PANEL",
  };
  const api = new Function("state", "$", `
    ${body}
    return { ${FUNCS.join(", ")} };
  `)(state, $);
  return { state, api, els };
}

// What boot() does with a stored panel: load it, then add the flag unless the
// builder removed it.
function open(yamlText) {
  const sb = makeSandbox();
  const parsed = sb.api.fromStoredDoc(jsyaml.load(yamlText));
  sb.state.screens = parsed.screens;
  sb.state.defaultScreen = parsed.defaultScreen;
  sb.state.screen = parsed.screen;
  sb.state.dataFlagOff = !!parsed.dataFlagOff;
  if (!sb.state.dataFlagOff) sb.api.ensureDataFlag();
  return sb;
}
const save = (sb) => jsyaml.dump(sb.api.toPyefisDoc(), { lineWidth: 100, noRefs: true });
const flags = (doc, screen) => doc.screens[screen].instruments.filter((i) => i.type === "data_annunciation");

const STOCK = {
  type: "data_annunciation", row: 2, column: 2, span: { rows: 5, columns: 18 },
  options: { target_screen: "DataStatus" },
};

const EXISTING = `
main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: atitude_indicator
        row: 0
        column: 0
        span: {rows: 110, columns: 200}
`;

test("a new panel's first screen carries a data_annunciation at the stock spot", () => {
  const { state, api } = makeSandbox();
  assert.equal(api.ensureDataFlag(), true);
  const doc = api.toPyefisDoc();
  assert.deepEqual(flags(doc, "PANEL"), [STOCK]);
  assert.equal("dataFlag" in doc.main, false);
  assert.equal(api.dataFlagWarning(), null);
  assert.equal(state.screens[0].instruments.length, 1);
});

test("a saved existing panel without the flag gains one, drawn over the attitude indicator", () => {
  const sb = open(EXISTING);
  const doc = jsyaml.load(save(sb));
  const inst = doc.screens.PANEL.instruments;
  assert.deepEqual(inst.map((i) => i.type), ["atitude_indicator", "data_annunciation"]);
  assert.deepEqual(inst[1], STOCK);
  // ...and reopening that save neither duplicates nor moves it.
  const again = jsyaml.load(save(open(save(sb))));
  assert.deepEqual(again, doc);
});

test("a panel that already has a DATA flag is left exactly as the builder placed it", () => {
  const placed = EXISTING + `
      - type: data_annunciation
        row: 90
        column: 150
        span: {rows: 6, columns: 20}
`;
  const sb = open(placed);
  assert.equal(sb.api.ensureDataFlag(), false);
  assert.deepEqual(flags(sb.api.toPyefisDoc(), "PANEL"),
    [{ type: "data_annunciation", row: 90, column: 150, span: { rows: 6, columns: 20 } }]);
});

test("the flag goes on the boot screen, which a config pull installs, not on the first listed one", () => {
  const multi = `
main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PFD}
screens:
  EMS: {layout: {rows: 110, columns: 200}, instruments: []}
  PFD: {layout: {rows: 110, columns: 200}, instruments: []}
`;
  const doc = open(multi).api.toPyefisDoc();
  assert.equal(flags(doc, "EMS").length, 0);
  assert.deepEqual(flags(doc, "PFD"), [STOCK]);
});

test("the stock placement scales to the panel's grid", () => {
  const half = EXISTING.replace("layout: {rows: 110, columns: 200}", "layout: {rows: 55, columns: 100}");
  const [f] = flags(open(half).api.toPyefisDoc(), "PANEL");
  assert.deepEqual([f.row, f.column, f.span.rows, f.span.columns], [1, 1, 2.5, 9]);
  const odd = EXISTING.replace("layout: {rows: 110, columns: 200}", "layout: {rows: 48, columns: 64}");
  const [g] = flags(open(odd).api.toPyefisDoc(), "PANEL");
  assert.deepEqual([g.row, g.column, g.span.rows, g.span.columns], [0.9, 0.6, 2.2, 5.8]);
});

test("removing the flag shows the warning, and the save records the removal", () => {
  const sb = open(EXISTING);
  assert.equal(sb.api.renderDataFlagWarning(), false);   // no change: the stage keeps its size
  assert.equal(sb.els["dataflag-warn"].hidden, true);

  sb.state.screens[0].instruments.pop();          // the builder deletes it
  assert.equal(sb.api.renderDataFlagWarning(), true);    // appeared: the canvas is resized
  assert.equal(sb.els["dataflag-warn"].hidden, false);
  assert.equal(sb.api.renderDataFlagWarning(), false);
  assert.match(sb.els["dataflag-msg"].textContent, /boot screen PANEL has no DATA flag/);

  const saved = save(sb);
  assert.equal(jsyaml.load(saved).main.dataFlag, false);
  // Reopening respects the removal: no flag comes back, and the warning stays.
  const reopened = open(saved);
  assert.equal(flags(reopened.api.toPyefisDoc(), "PANEL").length, 0);
  assert.notEqual(reopened.api.dataFlagWarning(), null);
});

test("putting the flag back clears the warning and the removal marker", () => {
  const sb = open(EXISTING.replace("defaultScreen: PANEL}", "defaultScreen: PANEL, dataFlag: false}"));
  assert.notEqual(sb.api.dataFlagWarning(), null);
  sb.api.ensureDataFlag();
  sb.api.renderDataFlagWarning();
  assert.equal(sb.els["dataflag-warn"].hidden, true);
  const doc = sb.api.toPyefisDoc();
  assert.equal("dataFlag" in doc.main, false);
  assert.deepEqual(flags(doc, "PANEL"), [STOCK]);
});

test("making a screen without the flag the boot screen warns", () => {
  const sb = open(`
main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PFD}
screens:
  PFD: {layout: {rows: 110, columns: 200}, instruments: []}
  EMS: {layout: {rows: 110, columns: 200}, instruments: []}
`);
  assert.equal(sb.api.dataFlagWarning(), null);
  sb.state.defaultScreen = "EMS";
  assert.match(sb.api.dataFlagWarning(), /boot screen EMS has no DATA flag/);
});

test("a flag inside an element group counts; one in a tab_section tab does not", () => {
  const sb = makeSandbox();
  sb.state.groupsById.g = { grid: { rows: 10, columns: 10 },
    instruments: [{ type: "data_annunciation", row: 0, column: 0, span: { rows: 1, columns: 1 } }] };
  sb.state.screens[0].instruments.push({ type: "group", id: "g", row: 0, column: 0, span: { rows: 10, columns: 10 } });
  assert.equal(sb.api.ensureDataFlag(), false);

  const tb = makeSandbox();
  tb.state.screens[0].instruments.push({ type: "tab_section", row: 0, column: 0, span: { rows: 50, columns: 50 },
    tabs: [{ label: "A", instruments: [] },
           { label: "B", instruments: [{ type: "data_annunciation", row: 0, column: 0, span: { rows: 5, columns: 18 } }] }] });
  // A tab the pilot is not looking at is not a cue: the boot screen still gets its own.
  assert.equal(tb.api.ensureDataFlag(), true);
});

test("boot adds the flag after load unless the panel records its removal", () => {
  const boot = extractFunction(editorSource(), "boot");
  assert.match(boot, /await load\(\);\s*\n\s*if \(!state\.dataFlagOff\) ensureDataFlag\(\);/);
  assert.match(editorSource(), /id="dataflag-warn"/);
  assert.match(extractFunction(editorSource(), "renderCanvas"), /if \(renderDataFlagWarning\(\)\) sizeCanvas\(\);/);
});
