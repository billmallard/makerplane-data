// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// LAYADV1 (AER-6539): the layout advisory as the REAL editor
// (public/editor.html) shows it, for two saved designs: a panel with the CAS
// window on its own screen (rule 1 fires on the PFD and on EMS) and a VFR
// panel with no CAS at all (rule 3's note). The advisory is opened, and the
// Code pane is on so the saved header shows beside it. No Worker runs: /api/*
// is answered here; /assets/editor/* is proxied to ASSET_ORIGIN (default the
// dev site, whose schema.json already carries cas_window and cas_marker).
//
//   PW=/path/to/playwright-core CHROME=/path/to/chrome-headless-shell \
//   node tools/layout_advisory_capture.mjs OUTDIR   # e.g. ../docs/images/aer-6539

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: ... OUTDIR");
mkdirSync(outDir, { recursive: true });

const screen = (name, instruments) => `  ${name}:
    module: pyefis.screens.screenbuilder
    title: ${name}
    layout: {rows: 110, columns: 200}
    instruments:
${instruments.map((i) => "      - " + i).join("\n")}
`;
const DESIGNS = {
  cas_off_pfd: `main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PFD}
screens:
${screen("PFD", [
    "{type: atitude_indicator, row: 0, column: 40, span: {rows: 64, columns: 120}}",
    "{type: horizontal_situation_indicator, row: 64, column: 60, span: {rows: 46, columns: 80}}",
  ])}${screen("RADIO", [
    "{type: cas_window, row: 4, column: 10, span: {rows: 50, columns: 90}}",
  ])}${screen("EMS", [
    "{type: arc_gauge, row: 4, column: 10, span: {rows: 40, columns: 60}}",
  ])}`,
  no_cas_vfr: `main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PFD}
screens:
${screen("PFD", [
    "{type: atitude_indicator, row: 0, column: 40, span: {rows: 70, columns: 120}}",
    "{type: airspeed_tape, row: 0, column: 10, span: {rows: 70, columns: 28}}",
  ])}`,
};

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [name, design] of Object.entries(DESIGNS)) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  page.on("dialog", (d) => d.accept());
  await page.route("http://editor.local/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
    if (p === "/api/devices/1/config") return route.fulfill({ body: design, contentType: "text/yaml" });
    if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
    if (p.startsWith("/assets/")) {
      const r = await fetch(`${ASSET_ORIGIN}${p}?capture=${Date.now()}`);
      return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()),
                             contentType: r.headers.get("content-type") || "" });
    }
    const file = p === "/editor" ? resolve(PUBLIC, "editor.html") : resolve(PUBLIC, p.slice(1));
    try {
      return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] || "application/octet-stream" });
    } catch { return route.fulfill({ status: 404, body: "" }); }
  });
  await page.goto("http://editor.local/editor?device=1");
  await page.waitForFunction(() => typeof state !== "undefined" && state.layoutAdvisory && state.screens.length > 0);
  await page.evaluate(() => {
    const c = $("codeToggle"); if (!c.checked) c.click();
    $("advisory").open = true; rerenderAll();
  });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);
  await page.locator(".layout").screenshot({ path: resolve(outDir, `editor_${name}.png`) });
  await page.locator("#advisory").screenshot({ path: resolve(outDir, `advisory_${name}.png`) });
  console.log(`--- ${name}\n` + await page.evaluate(() => serializeDesign()));
  await page.close();
}
await browser.close();
