// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6286 (AFC4 twin): the ap_annunciator strip and the tape autopilot cues
// as the REAL editor (public/editor.html) draws them on its canvas, from a
// saved design: the strip across the top, an airspeed tape, an ALT tape, a VS
// tape and a vsi_pfd, once per ap_position. No Worker runs: /api/* is
// answered here and /assets/editor/* is proxied to ASSET_ORIGIN (default the
// dev site, whose schema.json pyEfis's editor-assets job built from dev).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/ap_annunciator_editor_capture.mjs OUTDIR

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR");
mkdirSync(outDir, { recursive: true });

const W = 1024, H = 600;
const design = (stripOpts) => `main: {screenWidth: ${W}, screenHeight: ${H}, defaultScreen: PFD}
screens:
  PFD:
    module: pyefis.screens.screenbuilder
    title: PFD
    layout: {rows: 110, columns: 200}
    instruments:
      - {type: ap_annunciator, row: 0, column: 20, span: {rows: 9, columns: 160}, options: ${JSON.stringify(stripOpts)}}
      - {type: airspeed_tape, row: 12, column: 20, span: {rows: 84, columns: 22}}
      - {type: altimeter_tape, row: 12, column: 132, span: {rows: 84, columns: 26}}
      - {type: altimeter_tape, row: 12, column: 162, span: {rows: 84, columns: 16}, options: {dbkey: VS, maxalt: 4000, majorDiv: 500, minorDiv: 100}}
      - {type: vsi_pfd, row: 12, column: 182, span: {rows: 84, columns: 8}}
`;

const CASES = { left: {}, center: { ap_position: "center" } };
let current = "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [name, opts] of Object.entries(CASES)) {
  current = design(opts);
  const page = await browser.newPage({ viewport: { width: 1700, height: 1000 } });
  await page.route("http://editor.local/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
    if (p === "/api/devices/1/config") return route.fulfill({ body: current, contentType: "text/yaml" });
    if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
    if (p.startsWith("/assets/")) {
      const r = await fetch(`${ASSET_ORIGIN}${p}?capture=${Date.now()}`);
      return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()),
                             contentType: r.headers.get("content-type") || "" });
    }
    const file = p === "/editor" ? resolve(PUBLIC, "editor.html") : resolve(PUBLIC, p.slice(1));
    try {
      return route.fulfill({ body: readFileSync(file),
                             contentType: TYPES[extname(file)] || "application/octet-stream" });
    } catch { return route.fulfill({ status: 404, body: "" }); }
  });
  await page.goto("http://editor.local/editor?device=1");
  await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 5);
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await page.waitForTimeout(300);
  const out = `editor_${name}.png`;
  await page.locator("#canvas").screenshot({ path: resolve(outDir, out) });
  const order = await page.evaluate(() => [...document.querySelectorAll("#canvas .inst svg text[data-slot]")]
    .map((t) => [Number(t.getAttribute("x")), t.textContent]).sort((a, b) => a[0] - b[0]).map((t) => t[1]));
  const cues = await page.evaluate(() => [...document.querySelectorAll("#canvas [data-cue]")].map((e) => e.getAttribute("data-cue")));
  console.log(`${out}  strip order ${JSON.stringify(order)}  cues ${JSON.stringify(cues)}`);
  await page.close();
}
await browser.close();
