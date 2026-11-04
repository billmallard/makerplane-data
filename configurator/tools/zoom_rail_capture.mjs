// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4758 (G23): drive the REAL editor (public/editor.html, unmodified) end
// to end for the zoom rail. It loads a saved design with a full-screen
// moving_map, zoom_rail unset, on a 1024x600 7" screen, then:
//   1. crops the map element: the rail is off by default;
//   2. clicks the inspector's "Zoom rail" checkbox, the field the dev
//      schema.json carries from the pyEfis registry, and screenshots the page;
//   3. crops the map element again: the rail is drawn;
//   4. presses Save and writes the YAML body of the PUT, which is the config
//      the device pulls.
// No Worker runs: /api/* is answered here, and /assets/editor/* is proxied to
// the dev site, so the schema is the one the dev editor serves. Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/zoom_rail_capture.mjs OUTDIR

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR");
mkdirSync(outDir, { recursive: true });

const DESIGN = `main: {screenWidth: 1024, screenHeight: 600, screenDiagonalInches: 7, defaultScreen: MAP}
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
`;

let saved = null;
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.route("http://editor.local/**", async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const p = url.pathname;
  if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
  if (p === "/api/devices/1/config" && req.method() === "PUT") {
    saved = JSON.parse(req.postData()).yaml;
    return route.fulfill({ json: { version: 2 } });
  }
  if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
  if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
  if (p.startsWith("/assets/")) {
    // Bust the edge cache: a schema regenerated minutes ago must not be
    // answered by the copy cached before it.
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
const mapCrop = async (name) => {
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await page.waitForTimeout(100);
  await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, name) });
};

await mapCrop("twin_off.png");

await page.evaluate(() => { state.selected = 0; renderCanvas(); renderProps(); });
const box = page.locator("#props label", { hasText: /^Zoom rail$/ }).locator("xpath=..").locator("input[type=checkbox]");
if (await box.count() !== 1) throw new Error("no Zoom rail checkbox in the inspector: is zoom_rail in the dev schema.json?");
await box.scrollIntoViewIfNeeded();
await box.click();
await page.waitForTimeout(300);
await page.screenshot({ path: resolve(outDir, "editor_on.png") });

await mapCrop("twin_on.png");

await page.click("#save");
await page.waitForFunction(() => /Saved/.test(document.body.innerText));
if (saved === null) throw new Error("Save sent no PUT");
writeFileSync(resolve(outDir, "saved.yaml"), saved);
console.log(`options: ${JSON.stringify(await page.evaluate(() => state.instruments[0].options))}`);
await browser.close();
