// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5092 (S2 twin, CAP-121): crop the REAL editor's preview tile of a
// heading_display with its colours unset, plus the Foreground field the
// inspector shows for it. No Worker runs: /api/* is answered here and
// /assets/editor/* is proxied to ASSET_ORIGIN, so the schema.json the tile
// reads its default from is chosen by that origin. EDITOR=path/to/editor.html
// renders another build (the A-B base). Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     [EDITOR=...] [ASSET_ORIGIN=https://pyefis-dev.aerocommons.org] \
//     node tools/heading_display_capture.mjs OUTDIR PREFIX

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const EDITOR = process.env.EDITOR ? resolve(process.env.EDITOR) : resolve(PUBLIC, "editor.html");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir, prefix = "tile"] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR [PREFIX]");
mkdirSync(outDir, { recursive: true });

const DESIGN = `main: {screenWidth: 1024, screenHeight: 600, screenDiagonalInches: 7, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: heading_display
        row: 10
        column: 10
        span: {rows: 12, columns: 32}
        options: {}
`;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME,
  args: process.env.CHROME_ARGS ? process.env.CHROME_ARGS.split(" ") : [] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on("crash", () => console.error("page crashed"));
await page.route("http://editor.local/**", async (route) => {
  const p = new URL(route.request().url()).pathname;
  if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
  if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
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
await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length >= 1);
await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
await page.evaluate(() => document.fonts.ready);
await page.evaluate(() => { state.selected = -1; renderCanvas(); });
await page.waitForTimeout(200);
const tile = page.locator('.inst[data-i="0"]');
await tile.screenshot({ path: resolve(outDir, `${prefix}_tile.png`) });
const color = await page.evaluate(() => {
  const t = document.querySelector('.inst[data-i="0"] .inst-text');
  return { color: getComputedStyle(t).color, border: getComputedStyle(t).borderTopColor,
           schemaDefault: state.schema.instruments.heading_display.options.fg_color.default };
});
console.log(prefix, JSON.stringify(color));
await page.evaluate(() => select(0));
await page.waitForTimeout(150);
await page.locator("#props .field", { has: page.locator("label", { hasText: /^Foreground/ }) }).first()
  .screenshot({ path: resolve(outDir, `${prefix}_field.png`) });
await browser.close();
