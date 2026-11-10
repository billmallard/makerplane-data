// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5272 (G11b twin): the editor half of the ND mode screenshot comparison.
// Drives the REAL editor (public/editor.html, unmodified) in headless
// Chromium: for each screen size and nd_mode it loads a saved design holding
// one full-screen moving_map at 10 NM, then crops the map element to
// OUTDIR/twin_<mode>_<w>x<h>.png. No Worker runs: /api/* is answered here and
// /assets/editor/* is proxied to ASSET_ORIGIN (default the dev site), so the
// schema is the one the dev editor serves. Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/nd_mode_capture.mjs OUTDIR [WxH ...]
//
// tools/range_rings_compare.py composes each crop with the device frame from
// tools/nd_mode_oracle.py --render.

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir, ...sizeArgs] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR [WxH ...]");
mkdirSync(outDir, { recursive: true });
const SIZES = (sizeArgs.length ? sizeArgs : ["800x480", "650x1000"]).map((s) => s.split("x").map(Number));

const design = (w, h, mode) => `main: {screenWidth: ${w}, screenHeight: ${h}, defaultScreen: MAP}
screens:
  MAP:
    module: pyefis.screens.screenbuilder
    title: MAP
    layout: {rows: 110, columns: 200}
    instruments:
      - type: moving_map
        row: 0
        column: 0
        span: {rows: 110, columns: 200}
        options:
          range_nm: 10
          nd_mode: ${mode}
`;

let current = "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [w, h] of SIZES) {
  for (const mode of ["expanded", "ctr"]) {
    current = design(w, h, mode);
    // A viewport that shows the panel near 1:1, so the crop is not resampled much.
    const page = await browser.newPage({ viewport: { width: w + 700, height: h + 300 } });
    await page.route("http://editor.local/**", async (route) => {
      const req = route.request();
      const p = new URL(req.url()).pathname;
      if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
      if (p === "/api/devices/1/config") return route.fulfill({ body: current, contentType: "text/yaml" });
      if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
      if (p.startsWith("/assets/")) {
        const r = await fetch(`${ASSET_ORIGIN}${p}?capture=${Date.now()}`);
        return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()),
                               contentType: r.headers.get("content-type") || "" });
      }
      const file = p === "/editor" ? "editor.html" : p.slice(1);
      try {
        return route.fulfill({ body: readFileSync(resolve(PUBLIC, file)),
                               contentType: TYPES[extname(file)] || "application/octet-stream" });
      } catch { return route.fulfill({ status: 404, body: "" }); }
    });
    await page.goto("http://editor.local/editor?device=1");
    await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 1);
    await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => { state.selected = -1; renderCanvas(); });
    await page.waitForTimeout(150);
    const name = `twin_${mode}_${w}x${h}.png`;
    await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, name) });
    const opts = await page.evaluate(() => state.instruments[0].options);
    console.log(`${name}  options ${JSON.stringify(opts)}`);
    await page.close();
  }
}
await browser.close();
