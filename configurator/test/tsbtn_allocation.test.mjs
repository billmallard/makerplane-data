// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// DSP2c (AER-6676): a new button's TSBTN key skips the keys pyEfis binds to
// panel commands on every screen (pyEfis #591, AER-6630: TSBTN{id}1..11 and
// 27..33 in src/pyefis/config/databindings/default.yaml). A button on a bound
// key makes pyEfis switch that binding off, so the panel command stops working.
//
// Runs the REAL allocateTsbtn out of editor.html (test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
// Absent on a tree from before the fix; the allocator then fails the
// assertions below rather than this extraction.
let bound = "";
try { bound = extractConst(src, "TSBTN_BOUND"); } catch { bound = "const TSBTN_BOUND = new Set();"; }
const body = bound + "\n" + extractFunction(src, "allocateTsbtn");

// The bound set as pyEfis dev carries it; a change there must change both.
const PYEFIS_BOUND = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 27, 28, 29, 30, 31, 32, 33];

function makeEditor(instruments) {
  const state = { instruments };
  const allocateTsbtn = new Function("state", `${body}\nreturn allocateTsbtn;`)(state);
  return { state, allocateTsbtn };
}

const button = (dbkey) => ({ type: "button", options: { dbkey } });
const suffix = (key) => Number(/TSBTN\{id\}(\d+)/.exec(key)[1]);

test("a design's first button gets TSBTN{id}12", () => {
  const { allocateTsbtn } = makeEditor([]);
  assert.equal(allocateTsbtn(), "TSBTN{id}12");
});

test("no allocation returns a bound key while a free unbound one exists", () => {
  const { state, allocateTsbtn } = makeEditor([]);
  const got = [];
  const unbound = [];
  for (let n = 1; n <= 40; n++) if (!PYEFIS_BOUND.includes(n)) unbound.push(n);
  for (let i = 0; i < unbound.length; i++) {
    const key = allocateTsbtn();
    got.push(suffix(key));
    state.instruments.push(button(key));
  }
  assert.deepEqual(got, unbound);
  for (const n of got) assert.ok(!PYEFIS_BOUND.includes(n), `allocated bound key TSBTN{id}${n}`);
});

test("a button already on a bound key does not pull allocation onto bound keys", () => {
  // A design saved before this fix may hold TSBTN{id}1; it keeps it, and the
  // next button still lands on the first free unbound key.
  const { state, allocateTsbtn } = makeEditor([button("TSBTN{id}1"), button("TSBTN{id}12")]);
  assert.equal(allocateTsbtn(), "TSBTN{id}13");
  assert.equal(state.instruments[0].options.dbkey, "TSBTN{id}1");
});

test("the gap between the trim and dimmer keys is used before the top range", () => {
  const used = [];
  for (let n = 12; n <= 26; n++) used.push(button("TSBTN{id}" + n));
  const { allocateTsbtn } = makeEditor(used);
  assert.equal(allocateTsbtn(), "TSBTN{id}34");
});
