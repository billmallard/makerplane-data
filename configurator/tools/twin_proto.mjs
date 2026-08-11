// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// Build a standalone proto page for ONE instrument twin, so its rendering can
// be screenshotted headlessly and diffed against the real pyEfis widget render
// (tools/render_instrument.py output) without deploying the Worker.
//
// The point is that it extracts the REAL <style> block and the REAL build*()
// source out of public/editor.html rather than reimplementing them -- a proto
// that drifts from the editor proves nothing. This is the "extract by brace
// matching into a standalone proto page" workflow from configurator/CLAUDE.md,
// scripted so it is repeatable.
//
//   node tools/twin_proto.mjs --type airspeed_tape --width 160 --height 480 \
//        --out /tmp/proto.html [--sky]
//
// Then screenshot it with any headless browser, e.g.
//   msedge --headless --disable-gpu --screenshot=out.png --window-size=160,480 \
//          file:///tmp/proto.html

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const EDITOR = resolve(HERE, "..", "public", "editor.html");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return next && !next.startsWith("--") ? next : true;
}

/** Source text of a top-level `function name(...) { ... }`, by brace matching. */
function extractFunction(src, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`function ${name} not found in editor.html`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const src = readFileSync(EDITOR, "utf8");
const style = src.slice(src.indexOf("<style>") + 7, src.indexOf("</style>"));

const type = String(arg("type", "airspeed_tape"));
const width = Number(arg("width", 160));
const height = Number(arg("height", 480));
const sky = Boolean(arg("sky", false));
const out = String(arg("out", "proto.html"));

const FUNCS = ["buildTape", "readoutNotch"];
const body = FUNCS.map((f) => extractFunction(src, f)).join("\n\n");

writeFileSync(out, `<!doctype html>
<meta charset="utf-8">
<style>${style}</style>
<style>
  html, body { margin: 0; padding: 0; }
  body { background: ${sky ? "rgb(150,190,235)" : "#000"}; }
  #host { position: relative; width: ${width}px; height: ${height}px; }
  #host > .gauge { position: absolute; inset: 0; }
</style>
<div id="host"></div>
<script>
const SVG_NS = "http://www.w3.org/2000/svg";
// Stubs for the editor globals the extracted builders touch. Preview values
// mirror tools/render_instrument.py's _DEMO_VALUES so the proto and the widget
// render show the SAME numbers.
const state = { schema: { instruments: {
  airspeed_tape: { preview: { value: 110, tas: 118 } },
  altimeter_tape: { preview: { value: 3500 } },
} } };
function rect() { return { w: ${width}, h: ${height} }; }
function svgFromString(s) {
  const t = document.createElement("template");
  t.innerHTML = s.trim();
  return t.content.firstChild;
}
${body}
document.getElementById("host").appendChild(
  buildTape({ type: ${JSON.stringify(type)}, options: {} }));
</script>
`);
console.log(`wrote ${out} (${type}, ${width}x${height}${sky ? ", sky" : ""})`);
