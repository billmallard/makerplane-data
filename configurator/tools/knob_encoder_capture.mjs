// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// G96 (makerplane-data#19): the knob editor, driven through the REAL editor
// (public/editor.html) with real clicks. A four-instrument screen with no knob
// wiring; three instruments are made reachable one by one, the knob is bound
// to the screen, and the last ring stop is moved to the front. Each step is a
// screenshot of the canvas + properties panel, and the step's saved YAML
// (the Code pane) is printed. No Worker runs: /api/* is answered here and
// /assets/editor/* is proxied -- schema.json from ASSET_ORIGIN (default the
// dev site, schema v4), fix_keys.json from FIX_ORIGIN (default prod; the dev
// bucket has none).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/knob_encoder_capture.mjs OUTDIR

import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const FIX_ORIGIN = process.env.FIX_ORIGIN || "https://pyefis.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR");
mkdirSync(outDir, { recursive: true });

const DESIGN = `main: {screenWidth: 1024, screenHeight: 600, defaultScreen: RADIO}
screens:
  RADIO:
    module: pyefis.screens.screenbuilder
    title: RADIO
    layout: {rows: 110, columns: 200}
    instruments:
      - {type: arc_gauge, row: 5, column: 5, span: {rows: 45, columns: 60}, options: {name: RPM, dbkey: TACH1}}
      - {type: numeric_display, row: 5, column: 75, span: {rows: 20, columns: 60}, options: {dbkey: COMACTFREQ}}
      - {type: vertical_bar_gauge, row: 5, column: 145, span: {rows: 95, columns: 30}, options: {name: OILP, dbkey: OILP1}}
      - {type: button, row: 60, column: 5, span: {rows: 30, columns: 60}, options: {text: MENU, dbkey: "TSBTN{id}1"}}
`;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1640, height: 900 } });
await page.route("http://editor.local/**", async (route) => {
  const p = new URL(route.request().url()).pathname;
  if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
  if (p === "/api/devices/1/config") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
  if (p.startsWith("/api/")) return route.fulfill({ status: 404, body: "" });
  if (p.startsWith("/assets/")) {
    const origin = p.endsWith("/fix_keys.json") ? FIX_ORIGIN : ASSET_ORIGIN;
    const r = await fetch(`${origin}${p}?capture=${Date.now()}`);
    return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()),
                           contentType: r.headers.get("content-type") || "" });
  }
  const file = p === "/editor" ? resolve(PUBLIC, "editor.html") : resolve(PUBLIC, p.slice(1));
  try {
    return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] || "application/octet-stream" });
  } catch { return route.fulfill({ status: 404, body: "" }); }
});
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
await page.goto("http://editor.local/editor?device=1");
await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length === 4);
await page.evaluate(() => document.fonts.ready);

const yamlNow = () => page.evaluate(() => serializeDesign());
const shot = async (name) => {
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  await page.waitForTimeout(200);
  const canvas = await page.locator("#canvas").boundingBox();
  const props = await page.locator("#props").boundingBox();
  const x = Math.floor(canvas.x), y = Math.floor(Math.min(canvas.y, props.y));
  await page.screenshot({ path: resolve(outDir, name),
    clip: { x, y, width: Math.ceil(props.x + props.width - x), height: Math.ceil(Math.max(canvas.height, 760)) } });
  const badges = await page.evaluate(() => [...document.querySelectorAll("#canvas .inst")]
    .filter((e) => e.querySelector(".knob-badge"))
    .map((e) => [state.instruments[Number(e.dataset.i)].type, e.querySelector(".knob-badge").textContent,
                 e.querySelector(".knob-badge").classList.contains("inert") ? "inert" : "live"]));
  console.log(`${name}  badges ${JSON.stringify(badges)}`);
};

// Make instruments 0, 1, 2 reachable, in that order, through the checkbox.
for (const i of [0, 1, 2]) {
  await page.evaluate((i) => select(i), i);
  await page.locator("#props label.knob-check", { hasText: "Reachable by knob" }).locator("input").click();
}
await page.evaluate(() => select(-1));
await shot("knob_1_reachable_unbound.png");
writeFileSync(resolve(outDir, "knob_1.yaml"), await yamlNow());

// Bind the knob to the screen, then move ring stop 3 (the bar gauge) to the front.
await page.locator("#props label.knob-check", { hasText: "The knob drives this screen" }).locator("input").click();
for (const k of [2, 1]) await page.locator("ol.knob-ring li").nth(k).locator("button", { hasText: "↑" }).click();
await page.evaluate(() => select(2));
await shot("knob_2_bound_reordered.png");
writeFileSync(resolve(outDir, "knob_2.yaml"), await yamlNow());

// The selected bar gauge's own Knob section: the toggle, its ring stop and
// the raw encoder_order behind Advanced (opened).
await page.setViewportSize({ width: 1640, height: 2400 });   // the section sits at the panel's foot
const h = page.locator("#props h3", { hasText: /^Knob$/ });
await h.evaluate((e) => e.scrollIntoView({ block: "start" }));
await page.locator("#props details.knob-adv summary").click();
const hb = await h.boundingBox(), pb = await page.locator("#props").boundingBox();
await page.screenshot({ path: resolve(outDir, "knob_3_instrument_section.png"),
  clip: { x: Math.floor(pb.x), y: Math.floor(hb.y - 8), width: Math.ceil(pb.width), height: 150 } });

console.log("page errors:", JSON.stringify(errors));
await browser.close();
