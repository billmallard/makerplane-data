// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5084 (S3, CAP-121): drive the REAL editor (public/editor.html) for the
// token-or-custom colour control. It loads a saved design on a 1024x600 7"
// screen -- an HSI with its colours unset, a button whose colours are tokens,
// and a button with a custom hex -- then:
//   1. selects the token button and screenshots the page (inspector shows the
//      token dropdowns, the canvas the resolved colours);
//   2. selects the custom button and screenshots it ("Custom..." + picker);
//   3. selects the HSI, picks "ink.muted" from its Foreground dropdown through
//      the UI, and crops the HSI before and after;
//   4. presses Save and writes the YAML body of the PUT (the config the device
//      pulls), so the tokens can be seen going out as tokens;
//   5. writes the canvas markup of a token-free panel (canvas_notokens.html),
//      so two editor builds can be A-B'd for "nothing moves".
// No Worker runs: /api/* is answered here, /assets/editor/* is proxied to the
// dev site. EDITOR=path/to/editor.html renders another build (step 5 only is
// meaningful for a build without tokens). Usage:
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//     node tools/color_tokens_capture.mjs OUTDIR

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC = resolve(HERE, "..", "public");
const EDITOR = process.env.EDITOR ? resolve(process.env.EDITOR) : resolve(PUBLIC, "editor.html");
const ASSET_ORIGIN = process.env.ASSET_ORIGIN || "https://pyefis-dev.aerocommons.org";
const [outDir] = process.argv.slice(2);
if (!outDir) throw new Error("usage: OUTDIR");
mkdirSync(outDir, { recursive: true });

const HEAD = `main: {screenWidth: 1024, screenHeight: 600, screenDiagonalInches: 7, defaultScreen: PANEL}
screens:
  PANEL:
    module: pyefis.screens.screenbuilder
    title: PANEL
    layout: {rows: 110, columns: 200}
    instruments:
      - type: horizontal_situation_indicator
        row: 5
        column: 5
        span: {rows: 100, columns: 100}
`;
const TOKENS = HEAD + `      - type: button
        row: 10
        column: 120
        span: {rows: 25, columns: 70}
        options:
          dbkey: TSBTN{id}1
          text: TOKEN
          fg_color: state.caution
          bg_color: surface.raised
      - type: button
        row: 45
        column: 120
        span: {rows: 25, columns: 70}
        options:
          dbkey: TSBTN{id}2
          text: CUSTOM
          fg_color: "#ff4dd2"
`;
const NOTOKENS = HEAD + `      - type: button
        row: 10
        column: 120
        span: {rows: 25, columns: 70}
        options:
          dbkey: TSBTN{id}1
          text: PLAIN
          fg_color: "#ffaa00"
`;

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const browser = await chromium.launch({ executablePath: process.env.CHROME });

async function openEditor(design) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const box = { saved: null };
  await page.route("http://editor.local/**", async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === "/api/me") return route.fulfill({ json: { id: 1, email: "capture@local" } });
    if (p === "/api/devices/1/config" && req.method() === "PUT") {
      box.saved = JSON.parse(req.postData()).yaml;
      return route.fulfill({ json: { version: 2 } });
    }
    if (p === "/api/devices/1/config") return route.fulfill({ body: design, contentType: "text/yaml" });
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
  await page.waitForFunction(() => typeof state !== "undefined" && state.instruments && state.instruments.length >= 2);
  await page.evaluate(() => { const c = $("codeToggle"); if (c.checked) c.click(); });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(200);
  return { page, box };
}

const instCrop = (page, i, name) =>
  page.locator(`.inst[data-i="${i}"]`).screenshot({ path: resolve(outDir, name) });

// 5. Token-free panel markup, for an A-B against another build.
{
  const { page } = await openEditor(NOTOKENS);
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  writeFileSync(resolve(outDir, "canvas_notokens.html"),
    await page.evaluate(() => $("canvas").innerHTML));
  await page.close();
}

if (!process.env.EDITOR) {
  const { page, box } = await openEditor(TOKENS);
  // 1. The token button.
  await page.evaluate(() => select(1));
  await page.waitForTimeout(150);
  await page.screenshot({ path: resolve(outDir, "1_token_button.png") });
  // 2. The custom button.
  await page.evaluate(() => select(2));
  await page.waitForTimeout(150);
  await page.screenshot({ path: resolve(outDir, "2_custom_button.png") });
  // 3. The HSI: unset, then ink.muted picked from its Foreground dropdown.
  await page.evaluate(() => select(0));
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await instCrop(page, 0, "3a_hsi_default.png");
  await page.evaluate(() => select(0));
  const field = page.locator("#props .field", { has: page.locator("label", { hasText: /^Foreground/ }) }).first();
  await field.locator("select").selectOption("ink.muted");
  await page.waitForTimeout(150);
  await page.screenshot({ path: resolve(outDir, "3b_hsi_ink_muted_page.png") });
  await page.evaluate(() => { state.selected = -1; renderCanvas(); });
  await instCrop(page, 0, "3b_hsi_ink_muted.png");
  // 4. Save.
  await page.click("#save");
  await page.waitForFunction(() => /Saved|saved/.test($("status").textContent), null, { timeout: 5000 }).catch(() => {});
  writeFileSync(resolve(outDir, "saved.yaml"), box.saved || "(no PUT seen)\n");
  await page.close();
}
await browser.close();
