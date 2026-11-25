// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6540 (ANN2b): YAML typed or pasted into the Code pane is kept as
// written, but after the apply the pane's status line names each annunciator
// tile the device will draw at the fallback level (Bill's ANN1 ruling). Runs
// the REAL applyCode against the schema pyEfis #551 builds (carried in the
// oracle fixture), with the DOM reduced to the textarea and the status line.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import jsyaml from "js-yaml";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const FUNCS = ["annIsMap", "annSpec", "annLevel", "annAlertBound", "annRule", "codeMsg",
  "fromStoredDoc", "applyCode"];
const make = new Function("window", "jsyaml", "state", "$", "applyState", `
  ${extractConst(src, "ANN_TILES_OPTION")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  ${src.includes("function annDemotionNotes(") ? extractFunction(src, "annDemotionNotes") : ""}
  return applyCode;
`);

const RULE = JSON.parse(readFileSync(
  new URL("./fixtures/annunciator_pyefis.json", import.meta.url), "utf8")).schema.annunciator;

// Apply `yaml` through the Code pane: [status line, its colour, the state the
// editor took on].
function apply(yaml, rule = RULE) {
  const els = { "code-text": { value: yaml }, "code-msg": { textContent: "", style: {} } };
  let applied = null;
  const applyCode = make({ jsyaml }, jsyaml, { schema: rule ? { annunciator: rule } : {} },
    (id) => els[id], (parsed) => { applied = parsed; });
  assert.equal(applyCode(), true, "the apply is never refused");
  return { msg: els["code-msg"].textContent, color: els["code-msg"].style.color, applied };
}

// The ANN1 bench block (dev config v59, Beelink, 2026-11-25).
const bench = (level) => `
main: {screenWidth: 1280, screenHeight: 800}
screens:
  PFD:
    layout: {rows: 110, columns: 200}
    instruments:
      - type: annunciator
        row: 0
        column: 0
        span: {rows: 6, columns: 40}
        options:
          tiles:
            - label: VS HIGH
              key: VS
              when: VS gt 1500
              level: ${level}
`;

test("the bench block's warning gives exactly one note, not as an error", () => {
  const r = apply(bench("warning"));
  assert.equal(r.msg, "VS HIGH: warning drawn advisory on the device");
  assert.equal(r.color, "#8b949e", "a note, not the parse-error colour");
});

test("the same block at advisory gives none", () => {
  assert.equal(apply(bench("advisory")).msg, "");
});

test("the YAML is kept as written", () => {
  const tile = apply(bench("warning")).applied.screens[0].instruments[0].options.tiles[0];
  assert.equal(tile.level, "warning");
});

test("red and amber on the annunciate flag are drawn as asked", () => {
  const yaml = bench("x").replace(/ {12}- label: VS HIGH[\s\S]*$/, `\
            - {label: OIL, key: OILP, flag: annunciate, level: warning}
            - {label: FUEL, key: FUELQ, flag: annunciate, level: caution}
            - {label: STALE, key: ALT, flag: old, level: Caution}
`);
  assert.equal(apply(yaml).msg, "STALE: caution drawn advisory on the device");
});

test("no annunciator rule in the schema, no notes", () => {
  assert.equal(apply(bench("warning"), null).msg, "");
});
