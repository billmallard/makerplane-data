// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6498: render the real renderSVS() out of an editor.html (the SVS block
// via test/support/extract.mjs svsSource, not a copy) for each preview scene
// patch, in each terrain_shading mode, as PNG. Usage:
//   node render_svs.mjs <editor.html> <patch dir> <out dir> [W H [pitchDegreesShown]]
// <patch dir> holds <scene>.json as served at /assets/editor/svs/.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { createCanvas } from "canvas";
import { svsSource } from "../../../test/support/extract.mjs";

const [editor, patchDir, outDir, w = "800", h = "480", fov] = process.argv.slice(2);
const base = fov ? { pitchDegreesShown: Number(fov) } : {};
const renderSVS = new Function(`${svsSource(readFileSync(editor, "utf8"))}\nreturn renderSVS;`)();
mkdirSync(outDir, { recursive: true });
for (const scene of ["coastal", "mountains", "approach", "final"]) {
  const patch = JSON.parse(readFileSync(resolve(patchDir, `${scene}.json`), "utf8"));
  for (const mode of ["default", "relief", "clearance"]) {
    const cv = createCanvas(Number(w), Number(h));
    renderSVS(cv, patch, mode === "default" ? base : { ...base, svs_terrain_shading: mode });
    writeFileSync(resolve(outDir, `${scene}_${mode}.png`), cv.toBuffer("image/png"));
  }
}
