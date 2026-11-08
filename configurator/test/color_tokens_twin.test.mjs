// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5084 (S3, CAP-121): the editor's colour-token twin against the REAL
// pyEfis token layer (S1, screens/color_tokens.py). The expected values are
// not hand-written: tools/color_tokens_oracle.py imports pyEfis and dumps its
// vocabulary, each colour prop's token, and what apply_theme_colors() does to
// a table of options x preferences into test/fixtures/color_tokens_pyefis.json
// (regenerate with the command in that script's header). This test runs the
// REAL editor functions (test/support/extract.mjs) against it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "./support/extract.mjs";

const src = editorSource();
const CONSTS = ["COLOR_TOKENS", "COLOR_TOKEN_INDEX", "COLOR_TOKEN_IMMUTABLE",
  "COLOR_PROP_TOKENS", "PANEL_THEMES"];
const FUNCS = ["activeThemePreferences", "isColorToken", "isImmutableToken",
  "colorTokenDefault", "themeColors", "resolveColorToken", "applyThemeColors",
  "themedInstrument"];
const twin = new Function("state", `
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { COLOR_TOKENS, COLOR_TOKEN_IMMUTABLE, COLOR_PROP_TOKENS, PANEL_THEMES,
           themeColors, applyThemeColors, themedInstrument };
`)({ theme: "builtin" });

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/color_tokens_pyefis.json", import.meta.url), "utf8"));

test("token vocabulary is pyEfis's, in order", () => {
  assert.deepEqual(
    twin.COLOR_TOKENS,
    FIXTURE.tokens.map((t) => [t.name, t.value, t.description]));
});

test("theme-immutable groups are pyEfis's", () => {
  assert.deepEqual(twin.COLOR_TOKEN_IMMUTABLE, FIXTURE.immutable_groups);
});

test("every colour prop reads the token its pyEfis Prop declares", () => {
  assert.deepEqual(twin.COLOR_PROP_TOKENS, FIXTURE.prop_tokens);
});

for (const [name, prefs] of Object.entries(FIXTURE.preferences)) {
  test(`theme_colors() matches pyEfis: ${name}`, () => {
    assert.deepEqual(twin.themeColors(prefs), FIXTURE.theme_colors[name]);
  });
}

for (const c of FIXTURE.cases) {
  test(`apply_theme_colors() matches pyEfis: ${c.prefs} ${c.type} ${JSON.stringify(c.options)}`, () => {
    const before = JSON.stringify(c.options);
    const out = twin.applyThemeColors(c.type, c.options, FIXTURE.preferences[c.prefs]);
    assert.deepEqual(out, c.out);
    // Same key order too: pyEfis puts theme-filled props first.
    assert.deepEqual(Object.keys(out), Object.keys(c.out));
    assert.equal(JSON.stringify(c.options), before, "input options untouched");
  });
}

test("the built-in panel theme is pyEfis's shipped preferences (no colors: block)", () => {
  // If pyEfis ships a colour theme, this fails on the next fixture refresh: the
  // picker would then be offering less than the device has.
  assert.deepEqual(FIXTURE.shipped_theme_colors, {});
  const builtin = twin.PANEL_THEMES.find((t) => t.id === "builtin");
  assert.equal(builtin.preferences, null);
  assert.deepEqual(twin.themeColors(builtin.preferences), FIXTURE.shipped_theme_colors);
});

test("a panel with no tokens is drawn from the very same record", () => {
  for (const inst of [
    { type: "horizontal_situation_indicator", options: {} },
    { type: "button", options: { fg_color: "#ff0000", bg_color: "red" } },
    { type: "value_text" },
  ]) assert.equal(twin.themedInstrument(inst), inst);
});

test("a token is resolved for drawing but saved as the token", () => {
  const inst = { type: "button", row: 3, options: { fg_color: "ink.primary" } };
  const drawn = twin.themedInstrument(inst);
  assert.notEqual(drawn, inst);
  assert.equal(drawn.options.fg_color, "#ffffff");
  assert.equal(drawn.row, 3);
  assert.equal(inst.options.fg_color, "ink.primary", "the record keeps the token");
});
