// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6089 (ATT1b twin): dump the editor's REAL buildHSI() SVG for every
// config in tools/hsi_turn_trend_oracle.py's JSON, at that config's turn rate,
// to OUTDIR/twin_<config>.svg -- the twin half of tools/hsi_turn_trend_compare.py.
// Same extraction as test/hsi_turn_trend_twin.test.mjs; no browser needed.
//
//   node tools/hsi_turn_trend_svg.mjs ORACLE_JSON OUTDIR

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractConst, extractFunction } from "../test/support/extract.mjs";

const [oracleJson, outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: ORACLE_JSON OUTDIR");
mkdirSync(outDir, { recursive: true });
const oracle = JSON.parse(readFileSync(oracleJson, "utf8"));
const CONSTS = ["SOURCE_LABEL_MIN_CONTRAST", "ND_ADV", "ND_KERN"];
const FUNCS = ["svgFromString", "headingLabel", "_polar", "rgbaFromColor", "bgOpacity",
  "normHex", "_hexToRgb", "_rgbToHex", "_srgbToLinear", "_relativeLuminance", "contrastRatio",
  "_rgbToHsv", "_hsvToRgb", "darkenToContrast", "ndAdvance", "buildHSI"];
const SAMPLE = "const rotSample = 3;";
const src = CONSTS.map((c) => extractConst(editorSource(), c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(editorSource(), f)).join("\n\n");
if (!src.includes(SAMPLE)) throw new Error("buildHSI's preview turn rate moved");
const doc = {
  createElement() {
    const t = {};
    Object.defineProperty(t, "innerHTML", { set(v) { t._html = v; } });
    Object.defineProperty(t, "content", { get() { return { firstChild: { outerHTML: t._html } }; } });
    return t;
  },
};
for (const [name, cfg] of Object.entries(oracle.configs)) {
  const body = src.replace(SAMPLE, `const rotSample = ${cfg.rot};`);
  const { buildHSI } = new Function("document", `let _svgUid = 0; ${body}\nreturn { buildHSI };`)(doc);
  const svg = buildHSI({ options: { heading_bug_enabled: true, ...cfg.options } }).outerHTML
    .replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
  writeFileSync(resolve(outDir, `twin_${name}.svg`), svg);
  console.log(`twin_${name}.svg  rot ${cfg.rot}  ${JSON.stringify(cfg.options)}`);
}
