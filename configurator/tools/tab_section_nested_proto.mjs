// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// Build a standalone proto page for tab_section's buildContainerTwin() with a
// REAL nested instrument inside the active tab -- not stubbed to a no-op the
// way tools/tab_position_proto.mjs deliberately keeps it (that tool is a
// geometry/colour check only, per its own header comment). AER-1206 asks for
// evidence of the other half of Phase B: an instrument dropped into a tab
// renders inside the container at full fidelity, not a placeholder box.
//
// Same "extract by brace matching into a standalone proto page" technique as
// twin_proto.mjs/tab_position_proto.mjs: pulls buildInstrumentTwin() itself
// (unstubbed) plus the small set of helpers the value_text branch of its
// per-type dispatch needs (value_text has no separate build*() function --
// it's drawn inline in buildInstrumentTwin, see editor.html's LIVE_TEXT
// branch). The `state.schema.instruments` map used for the `meta` lookups
// buildContainerTwin/buildInstrumentTwin do (`meta.containers`,
// `meta.offscreen_renderable`, ...) is fetched from the LIVE dev schema.json
// at generation time, not hand-written -- see SCHEMA_URL below.
//
//   node tools/tab_section_nested_proto.mjs --out /tmp/proto.html
//
// Then screenshot it with any headless browser -- see
// docs/images/aer-1206/README.md for the no-root Playwright + apt-get-download
// workaround this was produced with (same recipe as docs/images/aer-665/).

import { writeFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith("--") ? next : true;
}

const SCHEMA_URL = String(arg("schema-url", "https://pyefis-dev.aerocommons.org/assets/editor/schema.json"));

const src = editorSource();
const style = src.slice(src.indexOf("<style>") + 7, src.indexOf("</style>"));

const width = Number(arg("width", 420));
const height = Number(arg("height", 300));
const out = String(arg("out", "proto.html"));

const FUNCS = [
  "clamp", "snapTo", "rootSpace", "cell", "withSpace", "rect",
  "ensureContainerSlot", "defaultActiveTab",
  "tabPosition", "tabStripGeom", "tabContentGeom",
  "buildContainerTwin", "buildInstrumentTwin",
  "formatValue", "sampleText", "rgbaFromColor", "bgOpacity", "textJustify",
];
const CONSTS = ["TAB_BAR_THICKNESS", "LIVE_TEXT", "LIVE_GAUGE", "TEXT_DEFAULTS", "_ALIGN_JUSTIFY"];
const body = CONSTS.map((c) => extractConst(src, c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(src, f)).join("\n\n");

console.error(`fetching live schema from ${SCHEMA_URL}`);
const schema = await fetch(SCHEMA_URL).then((r) => {
  if (!r.ok) throw new Error(`schema fetch failed: ${r.status}`);
  return r.json();
});
if (!schema.instruments.tab_section) throw new Error("live schema has no tab_section entry");
if (!schema.instruments.value_text) throw new Error("live schema has no value_text entry");
console.error(`live schema: generated_from=${JSON.stringify(schema.generated_from ?? null)}, schema_version=${schema.schema_version}`);

const PANELS = [
  { position: "top", label: "tab_position=top" },
  { position: "left", label: "tab_position=left" },
];

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
${PANELS.map((p, i) => `<div class="panel"><div class="cap">${p.label} -- value_text dropped into "Engine"</div><div class="host" id="host${i}"></div></div>`).join("\n")}
<script>
// Stub globals the extracted functions touch but that a bare proto page
// doesn't drive interactively (no drag, no selection, no props panel).
const state = {
  canvasW: ${width}, canvasH: ${height}, layout: { rows: 110, columns: 200 },
  nestedSel: null, snap: false,
  schema: ${JSON.stringify({ instruments: { tab_section: schema.instruments.tab_section, value_text: schema.instruments.value_text } })},
};
function renderCanvas() {}
function renderProps() {}
function startMoveNested() {}
function startResizeNested() {}
function positionEl() {}
let renderSpace = null;
${body}

${JSON.stringify(PANELS.map((p) => p.position))}.forEach((position, i) => {
  const inst = {
    column: 0, row: 0, span: { columns: 200, rows: 110 },
    tabs: [
      {
        label: "Engine", layout: { rows: 110, columns: 200 },
        // A value_text nested via the container-scoped drop-target (sec 4b):
        // tab-local coordinates, real schema options, same instrument shape
        // instrumentForDrop()/the drop handler would have produced.
        instruments: [
          { type: "value_text", column: 20, row: 30, span: { columns: 70, rows: 22 },
            options: { dbkey: "OILT", fg_color: "#7ee787" } },
        ],
      },
      { label: "Nav", layout: { rows: 110, columns: 200 }, instruments: [] },
      { label: "Radios", layout: { rows: 110, columns: 200 }, instruments: [] },
    ],
    options: { tab_position: position },
  };
  const meta = state.schema.instruments.tab_section;
  document.getElementById("host" + i).appendChild(buildContainerTwin(inst, meta, i));
});
</script>
`);
console.log(`wrote ${out}`);
