// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// Build a standalone proto page rendering the tab_section and button twins at
// several Font size (font_percent) values, for the side-by-side against the
// device render (AER-4374, pyEfis #356). The scenes and panel sizes match
// pyEfis docs/aer-4326-evidence/render_font_percent.py so the two strips line
// up panel for panel. Same brace-matched extraction as tab_position_proto.mjs:
// the twin code here IS editor.html's, not a copy.
//
//   node tools/font_size_proto.mjs --out /tmp/font_size.html [--scene N]
//
// --scene N renders only SCENES[N] (0 tab_section top, 1 left, 2 button), one
// strip per screenshot, as render_font_percent.py writes one file per widget.
//
// Then screenshot it headlessly; see docs/images/aer-4374/README.md.

import { writeFileSync } from "node:fs";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith("--") ? next : true;
}

const src = editorSource();
const style = src.slice(src.indexOf("<style>") + 7, src.indexOf("</style>"));
const out = String(arg("out", "font_size.html"));
const sceneArg = arg("scene", null);

const FUNCS = [
  "clamp", "rootSpace", "cell", "withSpace", "rect",
  "ensureContainerSlot", "defaultActiveTab",
  "fontScale", "tabLabelPx", "tabBarThickness",
  "tabPosition", "tabStripGeom", "tabContentGeom", "buildContainerTwin",
  "buildButton",
];
const CONSTS = ["TAB_BAR_THICKNESS", "TAB_LABEL_PX", "TAB_MIN_LABEL_PX"];
const body = CONSTS.map((c) => extractConst(src, c)).join("\n")
  + "\n\n" + FUNCS.map((f) => extractFunction(src, f)).join("\n\n");

// (type, options, tabs, panel w, panel h, values) -- render_font_percent.py's
// SCENES rows for tab_section (top, left) and button.
const TABS3 = ["Engine", "Nav", "Fuel"], TABS2 = ["Engine", "Nav"];
const SCENES = [
  { type: "tab_section", options: {}, tabs: TABS3, w: 320, h: 160, values: [null, 0.2, 150] },
  { type: "tab_section", options: { tab_position: "left" }, tabs: TABS2, w: 320, h: 160, values: [null, 0.2, 150] },
  { type: "button", options: { text: "Units" }, w: 160, h: 80, values: [null, 0.5, 0.8] },
];
const scenes = sceneArg == null ? SCENES : [SCENES[Number(sceneArg)]];

writeFileSync(out, `<!doctype html>
<meta charset="utf-8">
<style>${style}</style>
<style>
  html, body { margin: 0; padding: 0; background: #ffffff; }
  body { display: flex; flex-direction: column; gap: 12px; padding: 0; }
  .row { display: flex; gap: 8px; }
  .panel { display: flex; flex-direction: column; }
  .cap { height: 22px; line-height: 22px; text-align: center; color: #000; font: 14px "DejaVu Sans"; }
  .host { position: relative; background: #202020; overflow: hidden; }
</style>
<div id="root"></div>
<script>
const state = { canvasW: 0, canvasH: 0, layout: { rows: 10, columns: 10 }, nestedSel: null, instruments: [] };
function renderCanvas() {}
function renderProps() {}
function startMoveNested() {}
function startResizeNested() {}
function positionEl() {}
function buildInstrumentTwin() {}
let renderSpace = null;
${body}

const root = document.getElementById("root");
for (const sc of ${JSON.stringify(scenes)}) {
  const row = document.createElement("div"); row.className = "row";
  for (const v of sc.values) {
    state.canvasW = sc.w; state.canvasH = sc.h;
    const panel = document.createElement("div"); panel.className = "panel";
    panel.style.width = sc.w + "px";
    const cap = document.createElement("div"); cap.className = "cap";
    cap.textContent = v == null ? "unset (today)" : "font_percent: " + v;
    const host = document.createElement("div"); host.className = "host";
    host.style.width = sc.w + "px"; host.style.height = sc.h + "px";
    const options = { ...sc.options };
    if (v != null) options.font_percent = v;
    const inst = { column: 0, row: 0, span: { columns: 10, rows: 10 }, options };
    if (sc.type === "tab_section") {
      inst.tabs = sc.tabs.map((label) => ({ label, layout: { rows: 1, columns: 1 }, instruments: [] }));
      host.appendChild(buildContainerTwin(inst, { containers: [{ name: "tabs" }] }, 0));
    } else {
      host.appendChild(buildButton(inst));
    }
    panel.appendChild(cap); panel.appendChild(host); row.appendChild(panel);
  }
  root.appendChild(row);
}
</script>
`);
console.log(`wrote ${out}`);
