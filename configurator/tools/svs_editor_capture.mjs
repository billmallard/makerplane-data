// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6498 (SVSSHADE3): the virtual_vfr SVS preview as the REAL editor
// (public/editor.html) draws it, for each preview scene, with no
// svs_terrain_shading saved (the default, relief) and with clearance. Then
// the inspector of the selected instrument, to show the "Terrain shading"
// field the schema brings. No Worker runs: /api/* is answered here and
// /assets/editor/* (schema.json and the svs/<scene>.json patches) is proxied
// to ASSET_ORIGIN (default the dev site).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/svs_editor_capture.mjs OUTDIR

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

const W = 800, H = 480;
const design = (opts) => `main: {screenWidth: ${W}, screenHeight: ${H}, defaultScreen: PFD}
screens:
  PFD:
    module: pyefis.screens.screenbuilder
    title: PFD
    layout: {rows: 100, columns: 100}
    instruments:
      - {type: virtual_vfr, row: 0, column: 0, span: {rows: 100, columns: 100}, options: ${JSON.stringify(opts)}}
`;

const CASES = [];
for (const scene of ["coastal", "mountains", "approach", "final"]) {
  CASES.push([`${scene}_default`, { preview_scene: scene }]);
  CASES.push([`${scene}_clearance`, { preview_scene: scene, svs_terrain_shading: "clearance" }]);
}
let current = "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [name, opts] of CASES) {
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
  await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 1);
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  // the patch arrives asynchronously and triggers one re-render: wait for
  // the canvas, not the webp fallback
  await page.waitForSelector("#canvas .svsimg canvas", { timeout: 30000 });
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await page.waitForTimeout(300);
  await page.locator("#canvas").screenshot({ path: resolve(outDir, `editor_${name}.png`) });
  if (name === "approach_default") {
    await page.evaluate(() => { state.selected = 0; renderCanvas(); renderProps(); });
    await page.waitForTimeout(300);
    const field = await page.evaluate(() => {
      const el = [...document.querySelectorAll("#props label, #props .prop")]
        .find((e) => /Terrain shading/.test(e.textContent));
      const sel = el && (el.querySelector("select") || el.parentElement.querySelector("select"));
      return el ? { label: "Terrain shading", value: sel ? sel.value : null,
                    options: sel ? [...sel.options].map((o) => o.value) : null } : null;
    });
    console.log(`inspector field ${JSON.stringify(field)}`);
    await page.locator("#props").screenshot({ path: resolve(outDir, "editor_inspector.png") });
  }
  console.log(`editor_${name}.png`);
  await page.close();
}
await browser.close();
