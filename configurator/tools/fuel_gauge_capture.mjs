// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5547 (FUEL1f): drive the REAL editor (public/editor.html, unmodified)
// for the tank picker, the orphan mark and the stock-bar migration. It loads
// a saved 1280x720 design of five vertical bars at the stock EMS2 tank-bar
// size (64 x 327 device px, small_font_percent 0.06) against an aircraft with
// L MAIN, R MAIN and L TIP (R TIP was removed), then:
//   1. twin_labels.png    the five bars, unselected: a plain "Tank 1", an
//                         untouched "Tank 3" on FUELQ3, a bar bound to L TIP,
//                         a bar bound to the removed R TIP, and a hand-named
//                         "R TIP AUX" on FUELQ4;
//   2. orphan_props.png   the page with the orphan selected: the picker in red;
//   3. migrate_hover.png  the page with "Tank 3" selected and the pointer on
//                         its "Bind to L TIP" offer: the twin previews it;
//   4. migrate_done.png   the page after the click;
//   5. saved.yaml         the PUT body after Save.
// No Worker runs: /api/* is answered here, /assets/editor/* is proxied to the
// dev site, and /aircraft_refs.js + /fuel_label.js are this checkout's.
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/fuel_gauge_capture.mjs OUTDIR

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

const bar = (column, options) => `      - type: vertical_bar_gauge
        row: 0
        column: ${column}
        span: {rows: 50, columns: 10}
        options: ${JSON.stringify({ small_font_percent: 0.06, decimal_places: 1, ...options })}
`;
const DESIGN = `main: {screenWidth: 1280, screenHeight: 720, defaultScreen: EMS}
screens:
  EMS:
    module: pyefis.screens.screenbuilder
    title: EMS
    layout: {rows: 110, columns: 200}
    instruments:
${bar(0, { dbkey: "FUELQ1", name: "Tank 1" })}${bar(12, { dbkey: "FUELQ3", name: "Tank 3" })}${bar(24, { tank: "tk_lt" })}${bar(36, { tank: "tk_rt" })}${bar(48, { dbkey: "FUELQ4", name: "R TIP AUX" })}`;
const REFS = {
  source: "aircraft.fuel.tanks",
  items: [
    { id: "tk_lm", label: "L MAIN", slot: 1, role: "main", slot_key: "FUELQ1" },
    { id: "tk_rm", label: "R MAIN", slot: 2, role: "main", slot_key: "FUELQ2" },
    { id: "tk_lt", label: "L TIP", slot: 3, role: "tip", slot_key: "FUELQ3" },
  ],
  retired: { tk_rt: "R TIP" },
};

let saved = null;
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.route("http://editor.local/**", async (route) => {
  const req = route.request();
  const p = new URL(req.url()).pathname;
  if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
  if (p === "/api/devices/1/aircraft/refs") return route.fulfill({ json: REFS });
  if (p === "/api/devices/1/preferences") return route.fulfill({ json: { preferences: {}, fixgw: {} } });
  if (p === "/api/devices/1/config" && req.method() === "PUT") {
    saved = JSON.parse(req.postData()).yaml;
    return route.fulfill({ json: { version: 2 } });
  }
  if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
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
await page.waitForFunction(() => typeof state !== "undefined" && state.instruments
  && state.instruments.length === 5 && state.aircraftRefs && state.fuelLabel);
await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
await page.evaluate(() => document.fonts.ready);
await page.evaluate(() => { state.selected = -1; renderCanvas(); renderProps(); });
await page.waitForTimeout(200);

// 1. The five bars: the union of their boxes.
const boxes = await page.locator("#canvas .inst").evaluateAll((els) => els.map((e) => {
  const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom];
}));
const clip = { x: Math.min(...boxes.map((b) => b[0])) - 4, y: Math.min(...boxes.map((b) => b[1])) - 4 };
clip.width = Math.max(...boxes.map((b) => b[2])) + 4 - clip.x;
clip.height = Math.max(...boxes.map((b) => b[3])) + 4 - clip.y;
await page.screenshot({ path: resolve(outDir, "twin_labels.png"), clip });
const names = await page.locator("#canvas .inst .g-name").allTextContents();

// 2. The orphan, selected.
await page.evaluate(() => { select(3); });
await page.waitForTimeout(200);
await page.screenshot({ path: resolve(outDir, "orphan_props.png") });
const picker = await page.locator("#props select.ref-orphan").evaluate((s) =>
  ({ value: s.value, shown: s.options[s.selectedIndex].text, options: [...s.options].map((o) => o.text) }));

// 3-4. The stock "Tank 3": hover the offer, then click it.
await page.evaluate(() => { select(1); });
await page.waitForTimeout(200);
const offer = page.locator("#props .ref-offer button");
await offer.hover();
await page.waitForTimeout(200);
await page.screenshot({ path: resolve(outDir, "migrate_hover.png") });
const hovered = { name: (await page.locator("#canvas .inst .g-name").allTextContents())[1],
  options: await page.evaluate(() => state.instruments[1].options) };
await offer.click();
await page.mouse.move(5, 5);
await page.waitForTimeout(200);
await page.screenshot({ path: resolve(outDir, "migrate_done.png") });
const after = await page.evaluate(() => state.instruments[1].options);

await page.click("#save");
await page.waitForFunction(() => /Saved/.test(document.body.innerText));
if (saved === null) throw new Error("Save sent no PUT");
writeFileSync(resolve(outDir, "saved.yaml"), saved);
console.log(JSON.stringify({ names, picker, hovered, after }, null, 1));
await browser.close();
