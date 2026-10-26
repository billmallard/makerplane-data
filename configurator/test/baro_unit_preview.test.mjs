// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #108 / AER-3917: a pressure numeric_display (the BARO box) previews the
// altimeter setting in the aircraft profile's unit, converted and formatted
// as pyEfis does. Runs the REAL sampleText()/baroSample() out of editor.html
// (brace-matched -- see test/support/extract.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const SRC = editorSource();
const body = ["BARO_STD_INHG", "INHG_TO_HPA"].map((c) => extractConst(SRC, c)).join("\n")
  + "\n" + ["formatValue", "baroSample", "sampleText"].map((f) => extractFunction(SRC, f)).join("\n");
const make = (baroUnit) =>
  new Function("state", body + "\nreturn { sampleText };")({ baroUnit });

const baro = (opts) => ({ type: "numeric_display", options: { dbkey: "BARO", pressure: true, ...opts } });

test("inHg: 29.92 at the pyEfis default of 1 decimal, or the configured decimals", () => {
  const { sampleText } = make("inHg");
  assert.equal(sampleText(baro({})), "29.9");
  assert.equal(sampleText(baro({ decimal_places: 2 })), "29.92");
});

test("hPa: the same value through pyEfis's funcPressHpa", () => {
  const { sampleText } = make("hPa");
  assert.equal(sampleText(baro({ decimal_places: 0 })), "1013");
  assert.equal(sampleText(baro({})), "1013.2");     // 29.92 * 33.8639 = 1013.21
});

test("a numeric_display without pressure: true is unchanged", () => {
  assert.equal(make("hPa").sampleText({ type: "numeric_display", options: { dbkey: "OILP1" } }), "123");
});
