// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// Build a standalone proto page for tab_section's buildContainerTwin() across
// all four tab_position values, so its rendering can be screenshotted
// headlessly (AER-665). Same "extract by brace matching into a standalone
// proto page" workflow as twin_proto.mjs, extended to a container twin: the
// container helpers (rect/cell/withSpace/ensureContainerSlot/defaultActiveTab)
// come along too, since buildContainerTwin() depends on them, and the child
// instruments are stubbed to no-ops so this stays a geometry/colour check,
// not a full recursive render.
//
//   node tools/tab_position_proto.mjs --out /tmp/proto.html
//
// Then screenshot it with any headless browser, e.g. see
// docs/images/aer-665/README.md for the no-root Playwright + apt-get-download
// workaround this was built against.

import { writeFileSync } from "node:fs";
import { editorSource, extractFunction } from "../test/support/extract.mjs";

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith("--") ? next : true;
}

const src = editorSource();
const style = src.slice(src.indexOf("<style>") + 7, src.indexOf("</style>"));

const width = Number(arg("width", 260));
const height = Number(arg("height", 200));
const out = String(arg("out", "proto.html"));

const FUNCS = [
  "clamp", "rootSpace", "cell", "withSpace", "rect",
  "ensureContainerSlot", "defaultActiveTab",
  "tabPosition", "tabStripGeom", "tabContentGeom", "buildContainerTwin",
];
const body = FUNCS.map((f) => extractFunction(src, f)).join("\n\n");
const tabBarConst = src.slice(
  src.indexOf("const TAB_BAR_THICKNESS"),
  src.indexOf(";", src.indexOf("const TAB_BAR_THICKNESS")) + 1,
);

const POSITIONS = ["top", "bottom", "left", "right"];

writeFileSync(out, `<!doctype html>
<meta charset="utf-8">
<style>${style}</style>
<style>
  html, body { margin: 0; padding: 0; background: #0d1117; }
  body { display: flex; flex-wrap: wrap; gap: 16px; padding: 16px; }
  .panel { display: flex; flex-direction: column; gap: 4px; }
  .panel .cap { color: #8b949e; font: 12px sans-serif; }
  .host { position: relative; width: ${width}px; height: ${height}px; border: 1px solid #30363d; background: #010409; }
</style>
${POSITIONS.map((p, i) => `<div class="panel"><div class="cap">tab_position=${p}${i % 2 ? " (fg/bg override)" : " (default colours)"}</div><div class="host" id="host${i}"></div></div>`).join("\n")}
<script>
// Stub globals the extracted functions touch but that a bare proto page
// doesn't need for a geometry/colour check (no interaction, no recursion
// into child instrument rendering).
const state = { canvasW: ${width}, canvasH: ${height}, layout: { rows: 110, columns: 200 }, nestedSel: null };
function renderCanvas() {}
function renderProps() {}
function startMoveNested() {}
function startResizeNested() {}
function positionEl() {}
function buildInstrumentTwin() {}
${tabBarConst}
let renderSpace = null;
${body}

${JSON.stringify(POSITIONS)}.forEach((position, i) => {
  const inst = {
    column: 0, row: 0, span: { columns: 200, rows: 110 },
    tabs: [
      { label: "Engine", layout: { rows: 110, columns: 200 }, instruments: [] },
      { label: "Nav", layout: { rows: 110, columns: 200 }, instruments: [] },
      { label: "Radios", layout: { rows: 110, columns: 200 }, instruments: [] },
    ],
    options: { tab_position: position },
  };
  if (i % 2) { inst.options.fg_color = "#ffcc00"; inst.options.bg_color = "#1f6feb"; }
  const meta = { containers: [{ name: "tabs" }] };
  document.getElementById("host" + i).appendChild(buildContainerTwin(inst, meta, i));
});
</script>
`);
console.log(`wrote ${out}`);
