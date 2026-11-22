// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6304 (AFC5 twin): the editor half of the flight director comparison.
// Drives the REAL editor (public/editor.html) in headless Chromium with a
// saved design holding one attitude_indicator filling a 400x300 screen, the
// size pyEfis docs/aer-6052-evidence/styles.png was drawn at. Writes to OUTDIR:
//
// - preview_<style>.png: the instrument as the editor shows it (its preview
//   pose and FD_PREVIEW_ERROR), for single_cue, cross_pointer and off;
// - twin_<config>.png: for each oracle config named in CONFIGS, the editor's
//   own attitudeSVG() redrawn in place at that config's pose and command, so
//   it lines up with the widget grab of the same config;
// - options_panel.png: the selected instrument's option form, scrolled to
//   the "Flight director style" field with its list open;
// - vfr_<style>.png: a virtual_vfr (SVS) preview with that style, and the
//   fd_style values its own option form offers, printed.
//
// No Worker runs: /api/* is answered here and /assets/editor/* is proxied to
// ASSET_ORIGIN (default the dev site, whose schema.json carries fd_style).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/ai_flight_director_capture.mjs ORACLE_JSON OUTDIR

import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [oracleJson, outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: ORACLE_JSON OUTDIR");
mkdirSync(outDir, { recursive: true });
const oracle = JSON.parse(readFileSync(oracleJson, "utf8"));
const W = 400, H = 300;
const CONFIGS = ["satisfied", "climb", "turn", "both"].flatMap((c) =>
  ["single_cue", "cross_pointer"].map((s) => `${s}_${c}`));

const design = (opts, type = "atitude_indicator") => `main: {screenWidth: ${W}, screenHeight: ${H}, defaultScreen: PFD}
screens:
  PFD:
    module: pyefis.screens.screenbuilder
    title: PFD
    layout: {rows: 110, columns: 200}
    instruments:
      - type: ${type}
        row: 0
        column: 0
        span: {rows: 110, columns: 200}
        options: ${JSON.stringify(opts)}
`;

let current = "";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });

async function open(opts, type) {
  current = design(opts, type);
  const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
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
    const file = p === "/editor" ? resolve(PUBLIC, "editor.html") : resolve(PUBLIC, p.slice(1));
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
  return page;
}

// pitchDegreesShown 50: the widget's default, which styles.png was drawn at
// (the twin's ladder falls back to 30 when the option is unset).
for (const style of ["single_cue", "cross_pointer", "off"]) {
  const page = await open({ fd_style: style, pitchDegreesShown: 50 });
  await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, `preview_${style}.png`) });
  console.log(`preview_${style}.png`);
  if (style === "single_cue") {
    await page.evaluate(() => select(0));
    await page.waitForTimeout(150);
    const field = await page.evaluateHandle(() => [...document.querySelectorAll("select")]
      .find((s) => [...s.options].some((o) => o.value === "cross_pointer")));
    const values = await field.evaluate((s) => [...s.options].map((o) => o.value).join(",") + " selected=" + s.value);
    console.log(`fd_style select: ${values}`);
    await field.evaluate((s) => { s.scrollIntoView({ block: "center" }); s.size = s.options.length; });
    await page.waitForTimeout(100);
    const box = await field.evaluate((s) => {
      const r = (s.closest("label, .field, .row, div") || s).getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    });
    await page.screenshot({ path: resolve(outDir, "options_panel.png"),
      clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 60), width: box.width + 40, height: box.height + 120 } });
  }
  await page.close();
}

for (const style of ["single_cue", "cross_pointer"]) {
  const page = await open({ fd_style: style, preview_scene: "coastal" }, "virtual_vfr");
  await page.waitForFunction(() => document.querySelector("#canvas .inst canvas"), null, { timeout: 30000 });
  await page.waitForTimeout(300);
  await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, `vfr_${style}.png`) });
  await page.evaluate(() => select(0));
  const values = await page.evaluate(() => {
    const s = [...document.querySelectorAll("select")].find((e) => [...e.options].some((o) => o.value === "cross_pointer"));
    return s && [...s.options].map((o) => o.value).join(",") + " selected=" + s.value;
  });
  console.log(`vfr_${style}.png  virtual_vfr fd_style select: ${values}`);
  await page.close();
}

for (const name of CONFIGS) {
  const cfg = oracle.configs[name];
  const page = await open({ ...cfg.options, pitchDegreesShown: cfg.pitchDegreesShown });
  // attitudeSVG's roll turns the horizon clockwise (SVG rotate(roll)); the
  // widget turns it by -ROLL, so the device's ROLL goes in negated.
  await page.evaluate((c) => {
    const host = document.querySelector("#canvas .inst");
    const r = host.getBoundingClientRect();
    const svg = svgFromString(attitudeSVG(state.instruments[0], c.pitch, -c.roll, 0, true,
      r.width / r.height, c.h, { pitch: c.fd_pitch - c.pitch, roll: c.fd_roll - c.roll }));
    const old = host.querySelector("svg.gauge-svg");
    old.replaceWith(svg);
  }, cfg);
  await page.waitForTimeout(100);
  await page.locator("#canvas .inst").screenshot({ path: resolve(outDir, `twin_${name}.png`) });
  console.log(`twin_${name}.png`);
  await page.close();
}
await browser.close();
