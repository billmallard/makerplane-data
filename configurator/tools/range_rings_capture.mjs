// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4461: screenshot the REAL editor (public/editor.html, unmodified) with a
// full-screen moving_map selected, so the property panel's range-ring fields
// and the live preview are captured together. No Worker runs: /api/* is
// answered here (a signed-in user, device 1 holding the design below) and
// /assets/editor/* is proxied to the dev site, so the schema is the one the
// dev editor serves. Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/range_rings_capture.mjs default|configured OUT.png [OUT_MAP.png]

import { readFileSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [scene, out, outMap] = process.argv.slice(2);

const RING_OPTS = {
  default: {},
  configured: {
    range_ring_fractions: "0.25,0.5,0.75,1",
    range_ring_label_angle: 135,
    range_ring_color: "#00ffcc",
    range_ring_opacity: 80,
    range_ring_width: 1.5,
    range_ring_label_size: 18,
  },
}[scene];
if (!RING_OPTS || !out) throw new Error("usage: default|configured OUT.png [OUT_MAP.png]");

const opts = Object.entries({ range_nm: 10, ...RING_OPTS })
  .map(([k, v]) => `          ${k}: ${typeof v === "string" ? JSON.stringify(v) : v}`).join("\n");
const DESIGN = `main: {screenWidth: 1024, screenHeight: 600, defaultScreen: MAP}
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
${opts}
`;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.route("http://editor.local/**", async (route) => {
  const url = new URL(route.request().url());
  const p = url.pathname;
  if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
  if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
  if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
  if (p.startsWith("/assets/")) {
    const r = await fetch(ASSET_ORIGIN + p);
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
// Code pane off so the canvas gets the full stage height.
await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
await page.evaluate(() => { state.selected = 0; renderCanvas(); renderProps(); });
await page.evaluate(() => document.fonts.ready);
// Bring the range-ring fields into view in the property panel.
await page.evaluate(() => {
  const lab = [...document.querySelectorAll("#props label")].find((l) => l.textContent.startsWith("Range ring fractions"));
  if (lab) lab.scrollIntoView({ block: "start" });
});
await page.waitForTimeout(300);
await page.screenshot({ path: out });
// OUT_MAP: the map element alone, deselected, for an overlay against the
// pyEfis widget render (scale it to the device's 1024x600 first).
if (outMap) {
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await page.waitForTimeout(100);
  await page.locator("#canvas .inst").screenshot({ path: outMap });
}
await browser.close();
