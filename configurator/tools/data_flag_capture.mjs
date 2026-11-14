// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5610: drive the REAL editor (public/editor.html) headlessly through the
// default DATA flag cases and record what each one saves. /api is answered
// with a stored panel (or a 404 for a new device), /assets/* is proxied to the
// live configurator, and js-yaml comes from node_modules. Per case it writes a
// screenshot and the YAML the Save button PUT.
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/data_flag_capture.mjs OUTDIR [ASSET_ORIGIN]
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const JSYAML = resolve(HERE, "..", "node_modules", "js-yaml", "dist", "js-yaml.min.js");
const OUT = resolve(process.argv[2] || "out");
const ORIGIN = process.argv[3] || "https://pyefis.aerocommons.org";
mkdirSync(OUT, { recursive: true });

// An existing panel saved before AER-5610: tapes either side of an attitude
// indicator, no DATA flag.
const EXISTING = `main: {screenWidth: 1280, screenHeight: 800, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - {type: airspeed_tape, row: 6, column: 4, span: {rows: 98, columns: 24}}
      - {type: atitude_indicator, row: 0, column: 30, span: {rows: 110, columns: 140}}
      - {type: altimeter_tape, row: 6, column: 172, span: {rows: 98, columns: 24}}
`;

const selectFlagAndDelete = async (page) => {
  const n = await page.$$eval(".inst", (els) => els.length);
  await page.locator(".inst").nth(n - 1).click({ position: { x: 5, y: 5 } });   // the flag is drawn last
  await page.click("#del");
  await page.waitForTimeout(300);
};
const CASES = [
  ["new-panel", null, null],
  ["existing-panel", EXISTING, null],
  ["removed", EXISTING, selectFlagAndDelete],
  ["restored", EXISTING, async (page) => { await selectFlagAndDelete(page); await page.click("#dataflag-add"); }],
];

const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const [name, stored, act] of CASES) {
  const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
  const puts = [];
  page.on("dialog", (d) => d.accept());
  page.on("pageerror", (e) => console.log(name, "page error:", e.message));
  await page.route("**/*", async (route) => {
    const req = route.request(), u = new URL(req.url());
    if (u.hostname === "cdn.jsdelivr.net") return route.fulfill({ body: readFileSync(JSYAML), contentType: "text/javascript" });
    if (u.hostname !== "cfg.test") return route.abort();
    if (u.pathname.startsWith("/assets/")) {
      const r = await fetch(ORIGIN + u.pathname);
      return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()),
        contentType: r.headers.get("content-type") || "" });
    }
    if (u.pathname === "/api/me") return route.fulfill({ json: { id: 1 } });
    if (u.pathname === "/api/devices/1/config") {
      if (req.method() === "PUT") { puts.push(JSON.parse(req.postData()).yaml); return route.fulfill({ json: { version: 7 } }); }
      return stored ? route.fulfill({ body: stored, contentType: "text/yaml" }) : route.fulfill({ status: 404, body: "" });
    }
    if (u.pathname.startsWith("/api/")) return route.fulfill({ status: 404, json: {} });
    const p = u.pathname === "/editor" ? "/editor.html" : u.pathname;
    try { return route.fulfill({ body: readFileSync(PUBLIC + p), contentType: p.endsWith(".html") ? "text/html" : "text/javascript" }); }
    catch { return route.fulfill({ status: 404, body: "" }); }
  });
  await page.goto("http://cfg.test/editor?device=1");
  await page.waitForTimeout(2500);
  if (act) await act(page);
  await page.waitForTimeout(800);
  await page.click("#save");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  writeFileSync(`${OUT}/${name}.saved.yaml`, puts[0] || "");
  const banner = await page.$eval("#dataflag-warn", (e) => !e.classList.contains("hidden"));
  console.log(`${name}: warning ${banner ? "shown" : "hidden"}`);
  await page.close();
}
await browser.close();
