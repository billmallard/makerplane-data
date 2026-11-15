// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5553 (HSIBRG1 twin): the editor half of the HSI bearing-pointer
// comparison. Drives the REAL editor (public/editor.html, or EDITOR=path for
// the A-B base) in headless Chromium: for each config in the oracle's JSON
// (tools/hsi_bearing_oracle.py, so both halves draw the same options) it
// loads a saved design holding one HSI filling a SIZE x SIZE screen, then
// crops the instrument to OUTDIR/<prefix>_<config>.png. No Worker runs:
// /api/* is answered here and /assets/editor/* is proxied to ASSET_ORIGIN
// (default the dev site). Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     [EDITOR=...] node tools/hsi_bearing_capture.mjs ORACLE_JSON OUTDIR [PREFIX]

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const EDITOR = process.env.EDITOR ? resolve(process.env.EDITOR) : resolve(PUBLIC, "editor.html");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [oracleJson, outDir, prefix = "twin"] = process.argv.slice(2);
if (!outDir) throw new Error("usage: ORACLE_JSON OUTDIR [PREFIX]");
mkdirSync(outDir, { recursive: true });
const oracle = JSON.parse(readFileSync(oracleJson, "utf8"));
const S = oracle.size;

const design = (opts) => `main: {screenWidth: ${S}, screenHeight: ${S}, defaultScreen: HSI}
screens:
  HSI:
    module: pyefis.screens.screenbuilder
    title: HSI
    layout: {rows: 110, columns: 200}
    instruments:
      - type: horizontal_situation_indicator
        row: 0
        column: 0
        span: {rows: 110, columns: 200}
        options: ${JSON.stringify({ heading_bug_enabled: true, ...opts })}
`;

let current = "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [name, cfg] of Object.entries(oracle.configs)) {
  current = design(cfg.options);
  const page = await browser.newPage({ viewport: { width: S + 700, height: S + 300 } });
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
    const file = p === "/editor" ? EDITOR : resolve(PUBLIC, p.slice(1));
    try {
      return route.fulfill({ body: readFileSync(file),
                             contentType: TYPES[extname(file)] || "application/octet-stream" });
    } catch { return route.fulfill({ status: 404, body: "" }); }
  });
  await page.goto("http://editor.local/editor?device=1");
  await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 1);
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await page.waitForTimeout(150);
  const out = `${prefix}_${name}.png`;
  await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, out) });
  console.log(`${out}  options ${JSON.stringify(await page.evaluate(() => state.instruments[0].options))}`);
  await page.close();
}
await browser.close();
