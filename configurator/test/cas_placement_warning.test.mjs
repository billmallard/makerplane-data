// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6348 (CAS3 amendment): a screen with no `cas_window` or `cas_marker`
// gets a non-blocking warning. Runs the REAL editor function
// (test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const { screensMissingCas } = new Function(`
  ${extractConst(src, "CAS_PLACEMENT_TYPES")}
  ${extractFunction(src, "casPlacedIn")}
  ${extractFunction(src, "screensMissingCas")}
  return { screensMissingCas };
`)();

const WITH_CAS = { cas_window: {}, cas_marker: {}, button: {} };
const sc = (...types) => ({ instruments: types.map((type) => ({ type, id: type === "group" ? "g1" : undefined })) });

test("inert while the schema has neither CAS type", () => {
  assert.deepEqual(screensMissingCas([sc(), sc("button")], { button: {} }, {}), []);
  assert.deepEqual(screensMissingCas([sc()], undefined, {}), []);
});

test("lights up once the schema carries either type and the layout places one", () => {
  assert.deepEqual(screensMissingCas([sc(), sc("cas_marker")], { cas_marker: {} }, {}), [0]);
  assert.deepEqual(screensMissingCas([sc(), sc("cas_window")], { cas_window: {} }, {}), [0]);
});

test("a panel with no CAS anywhere gets no banner (Bill, AER-6553)", () => {
  assert.deepEqual(screensMissingCas([sc(), sc("button")], WITH_CAS, {}), []);
});

test("a window or a marker satisfies the screen; anything else does not", () => {
  const screens = [sc("cas_window"), sc("button"), sc("cas_marker", "button"), sc()];
  assert.deepEqual(screensMissingCas(screens, WITH_CAS, {}), [1, 3]);
});

test("a CAS instrument inside a group counts; an unknown or CAS-free group does not", () => {
  const groups = { g1: { instruments: [{ type: "cas_marker" }] } };
  assert.deepEqual(screensMissingCas([sc("group")], WITH_CAS, groups), []);
  assert.deepEqual(screensMissingCas([sc("group"), sc("cas_marker")], WITH_CAS, {}), [0]);
  assert.deepEqual(screensMissingCas([sc("group"), sc("cas_marker")], WITH_CAS, { g1: { instruments: [{ type: "button" }] } }), [0]);
});

test("one on a tab page is hidden behind the tab, so it does not count", () => {
  // On the tab page it still puts CAS in use, so the screen is flagged.
  const tabbed = { instruments: [{ type: "tab_section", tabs: [{ instruments: [{ type: "cas_marker" }] }] }] };
  assert.deepEqual(screensMissingCas([tabbed], WITH_CAS, {}), [0]);
});
