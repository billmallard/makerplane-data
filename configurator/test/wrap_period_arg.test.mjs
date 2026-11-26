// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6708: `change value wrap` takes an optional third field, the period
// (pyEfis #608). The shipped heading-bug buttons read "HEADBUG, -1, 360";
// without the 360, HDG - at 000 lands on 358.9. argEditor's fixkey_number
// branch used to split the arg into two fields and write two back, so editing
// the delta silently dropped the period.
//
// Runs the REAL argEditor out of editor.html (brace-matched, not
// reimplemented -- see test/support/extract.mjs) against a minimal fake DOM.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const src = editorSource();
const body = ["splitArg", "plainInput", "fixKeyInput", "normHex", "argEditor"]
  .map((f) => extractFunction(src, f)).join("\n\n");

function fakeDocument() {
  const el = (tag) => ({
    tag, className: "", value: "", placeholder: "", children: [], attrs: {}, listeners: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    append(...c) { this.children.push(...c); },
    appendChild(c) { this.children.push(c); return c; },
  });
  return { createElement: el };
}

// The catalogue entries as pyEfis's editor schema exports them.
const WRAP = { verb: "change value wrap", arg: { kind: "fixkey_number", number_label: "Delta" } };
const SET = { verb: "set value", arg: { kind: "fixkey_number", number_label: "Value" } };

function editor(meta, arg) {
  const act = { [meta.verb]: arg };
  const w = new Function("document", "commitConditions", `${body}\nreturn argEditor;`)(
    fakeDocument(), () => {})({}, act, meta.verb, meta);
  const type = (i, v) => {
    const inp = w.children[i];
    inp.value = v;
    for (const fn of inp.listeners.input) fn.call(inp);
  };
  return { act, w, type };
}

test("editing the delta of a wrap keeps its 360 period", () => {
  const { act, type } = editor(WRAP, "HEADBUG, -1, 360");
  type(1, "-2");
  assert.equal(act["change value wrap"], "HEADBUG,-2,360");
});

test("editing the key of a wrap keeps its 360 period", () => {
  const { act, type } = editor(WRAP, "HEADBUG, 1, 360");
  type(0, "HEADBUG2");
  assert.equal(act["change value wrap"], "HEADBUG2,1,360");
});

test("a wrap action offers a period input, filled from the arg", () => {
  const { w } = editor(WRAP, "HEADBUG, -1, 360");
  assert.equal(w.children.length, 3);
  assert.equal(w.children[2].value, "360");
  assert.match(w.children[2].placeholder, /period/i);
  assert.equal(w.className, "act-args three");
});

test("a new wrap button can be given a period", () => {
  const { act, type } = editor(WRAP, "");
  type(0, "HEADBUG"); type(1, "-1"); type(2, "360");
  assert.equal(act["change value wrap"], "HEADBUG,-1,360");
});

test("an empty period writes the two-field arg", () => {
  const { act, type } = editor(WRAP, "NAVSRC, 1, 4");
  type(2, "");
  assert.equal(act["change value wrap"], "NAVSRC,1");
  const fresh = editor(WRAP, "NAVSRC, 1");
  fresh.type(1, "-1");
  assert.equal(fresh.act["change value wrap"], "NAVSRC,-1");
});

test("a schema-side period_label names the input", () => {
  const meta = { verb: WRAP.verb, arg: { ...WRAP.arg, period_label: "Circle" } };
  const { w } = editor(meta, "HEADBUG, -1, 360");
  assert.equal(w.children[2].placeholder, "Circle");
});

test("set value stays two inputs, and an unknown extra field survives an edit", () => {
  const { act, w, type } = editor(SET, "HEADBUG, 90, extra");
  assert.equal(w.children.length, 2);
  type(1, "180");
  assert.equal(act["set value"], "HEADBUG,180,extra");
});
