// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6348 (ANN2): the annunciator tile editor's rules, run from the REAL
// editor functions against the schema pyEfis #551 builds (carried in the
// oracle fixture). The level picker must offer red and amber only where the
// device draws them (Bill's ANN1 ruling), and a condition's other keys are
// derived the way the Button's are.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const FUNCS = ["annIsMap", "annSpec", "annLevel", "annAlertBound", "annTileField",
  "annBindingFields", "annTileBinding", "annLevelChoices", "deriveTileConditionKeys",
  "whenKeys", "parseWhen", "deriveConditionKeys"];
const ed = new Function(`
  ${extractConst(src, "_MAGIC_KEYS")}
  const _OP_LABELS = { eq: 1, ne: 1, lt: 1, le: 1, gt: 1, ge: 1, contains: 1 };
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { annSpec, annTileBinding, annLevelChoices, deriveTileConditionKeys, deriveConditionKeys, whenKeys };
`)();

const RULE = JSON.parse(readFileSync(
  new URL("./fixtures/annunciator_pyefis.json", import.meta.url), "utf8")).schema.annunciator;

const BINDINGS = [
  {},
  { when: "OILP lt 25" },
  { flag: "annunciate" }, { flag: "old" }, { flag: "bad" }, { flag: "fail" },
  { enum: { 0: "NAV1", 2: "GPS" } },
];

test("the picker offers exactly the levels the device draws as asked", () => {
  for (const b of BINDINGS) {
    const tile = { label: "T", key: "OILP", ...b };
    const offered = ed.annLevelChoices(RULE, tile);
    for (const level of RULE.levels) {
      const drawn = ed.annSpec({ ...tile, level }, RULE).level;
      assert.equal(offered.includes(level), drawn === level, `${JSON.stringify(b)} ${level}: drawn ${drawn}`);
    }
  }
});

test("red and amber only on the annunciate flag", () => {
  const alertOn = BINDINGS.filter((b) => {
    const offered = ed.annLevelChoices(RULE, { key: "K", ...b });
    return RULE.alert_levels.every((l) => offered.includes(l));
  });
  assert.deepEqual(alertOn, [{ flag: "annunciate" }]);
  // Before a key is typed the rule still holds.
  assert.ok(RULE.alert_levels.every((l) => ed.annLevelChoices(RULE, { flag: "annunciate" }).includes(l)));
  assert.ok(!ed.annLevelChoices(RULE, { when: "X gt 1" }).some((l) => RULE.alert_levels.includes(l)));
});

test("the binding kind is the first binding field set, as the device reads it", () => {
  assert.equal(ed.annTileBinding(RULE, { key: "K" }), "");
  assert.equal(ed.annTileBinding(RULE, { key: "K", when: "K gt 1" }), "when");
  assert.equal(ed.annTileBinding(RULE, { key: "K", flag: "bad" }), "flag");
  assert.equal(ed.annTileBinding(RULE, { key: "K", enum: { 1: "A" } }), "enum");
  assert.equal(ed.annTileBinding(RULE, { key: "K", enum: {} }), "", "an empty map is no binding");
  assert.equal(ed.annTileBinding(RULE, { key: "K", when: "" }), "");
  // Two set: the device refuses the tile, and the card says so.
  assert.match(ed.annSpec({ key: "K", when: "K gt 1", flag: "bad" }, RULE).error, /more than one binding/);
});

test("a condition's other keys are derived; its own key and flags are not listed", () => {
  const t = { key: "OILP", when: "OILP lt 25 and OILT.old and not FUELP gt 3" };
  ed.deriveTileConditionKeys(t);
  // "not FUELP gt 3" is too complex for the guided parser: the raw scan reads it.
  assert.deepEqual(t.condition_keys, ["FUELP", "OILT"]);
  const own = { key: "VS", when: "VS gt 1500" };
  ed.deriveTileConditionKeys(own);
  assert.equal(own.condition_keys, undefined);
  const gone = { key: "K", flag: "annunciate", condition_keys: ["X"] };
  ed.deriveTileConditionKeys(gone);
  assert.equal(gone.condition_keys, undefined, "only a condition reads other keys");
});

test("the Button's condition_keys are unchanged by the shared key scan", () => {
  const inst = { options: { dbkey: "TSBTN{id}1", conditions: [
    { when: "CLICKED eq true and NAVSRC lt 0.5" }, { when: "TSBTN{id}1" },
    { when: "not ALT2.old" }, { when: "[SCREEN eq 'PFD'] and [MAPRANGE gt 2]" }] } };
  ed.deriveConditionKeys(inst);
  assert.deepEqual(inst.options.condition_keys, ["ALT2", "MAPRANGE", "NAVSRC"]);
  const none = { options: { conditions: [{ when: "CLICKED eq true" }], condition_keys: ["STALE"] } };
  ed.deriveConditionKeys(none);
  assert.equal(none.options.condition_keys, undefined);
});
