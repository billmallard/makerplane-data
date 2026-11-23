// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6348 (ANN2): the annunciator as the REAL editor (public/editor.html)
// draws it, from a saved design holding the ANN1 bench block, with the block
// selected so the tile and binding editor shows. One capture per preview
// state (lit, off, stale). No Worker runs: /api/* is answered here,
// /assets/editor/schema.json is SCHEMA (pyEfis #551's build_schema(): dev's
// asset job publishes the type only once #551 merges), and the other
// /assets/editor/* are proxied to ASSET_ORIGIN (default the dev site).
//
//   python3 -c "import json; from pyefis.editor.schema import build_schema; \
//     print(json.dumps(build_schema()))" > /tmp/schema.json   # PYTHONPATH=$PYEFIS/src
//   PW=/path/to/playwright-core CHROME=/path/to/chrome-headless-shell \
//   SCHEMA=/tmp/schema.json node tools/annunciator_editor_capture.mjs OUTDIR

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir || !process.env.SCHEMA) throw new Error("usage: SCHEMA=schema.json ... OUTDIR");
mkdirSync(outDir, { recursive: true });

// The ANN1 bench PFD block (pyEfis docs/images/ann1_annunciator/kit/edit_cfg.py)
// on the bench's 1920 x 1080 screen, beside an attitude indicator for scale.
const DESIGN = `main: {screenWidth: 1920, screenHeight: 1080, defaultScreen: PFD}
screens:
  PFD:
    module: pyefis.screens.screenbuilder
    title: PFD
    layout: {rows: 110, columns: 200}
    instruments:
      - {type: atitude_indicator, row: 0, column: 40, span: {rows: 68, columns: 120}}
      - type: annunciator
        row: 70
        column: 94
        span: {rows: 16, columns: 35}
        options:
          rows: 2
          cols: 3
          font_family: B612
          tiles:
            - {label: AP, key: APENG, level: status, flash_on_change: 5}
            - {label: YD, key: YDENG, level: status}
            - {label: ALT CAP, key: APALTCAP, level: advisory}
            - {label: NAV, key: NAVSRC, level: info, enum: {0: VLOC1, 1: VLOC2, 2: GPS}}
            - {label: ALT ALERT, key: ALTSEL, flag: annunciate, level: caution}
            - {label: VS HIGH, key: VS, when: VS gt 1500, level: warning}
`;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const st of ["lit", "off", "stale"]) {
  const page = await browser.newPage({ viewport: { width: 1700, height: 1500 } });
  page.on("dialog", (d) => d.accept());
  await page.route("http://editor.local/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
    if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
    if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
    if (p === "/assets/editor/schema.json") return route.fulfill({ body: readFileSync(process.env.SCHEMA), contentType: "application/json" });
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
  await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 2);
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  await page.evaluate((s) => { state.annPreview = s; select(1); renderCanvas(); }, st);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  await page.locator(".layout").screenshot({ path: resolve(outDir, `editor_${st}.png`) });
  await page.locator("#canvas").screenshot({ path: resolve(outDir, `canvas_${st}.png`) });
  if (st === "lit") {   // the whole tile editor: let the panel grow instead of scrolling
    await page.setViewportSize({ width: 1700, height: 4200 });
    await page.evaluate(() => { const p = $("props"); p.style.overflow = "visible"; p.style.height = "auto"; });
    await page.locator("#props").screenshot({ path: resolve(outDir, "tiles_panel.png") });
  }
  const yaml = await page.evaluate(() => serializeDesign());
  if (st === "lit") console.log(yaml);
  await page.close();
}
await browser.close();
