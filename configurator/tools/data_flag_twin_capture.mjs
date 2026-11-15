// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5654: render the editor's DATA flag twin (dataFlagSvg, extracted from
// the REAL public/editor.html) at device px over black, the twin half of the
// side-by-side with tools/data_flag_oracle.py --render. FONT_DIR holds the
// DejaVu TTFs the editor loads from jsdelivr (dejavu-fonts-ttf 2.37).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//   FONT_DIR=/path/to/dejavu/ttf node tools/data_flag_twin_capture.mjs OUT.png W H [FONT_PERCENT]
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const [out, W, H, fp] = process.argv.slice(2);
const w = Number(W), h = Number(H);
const src = editorSource();
const { dataFlagGeom, dataFlagSvg } = new Function(`
  ${["DATA_FLAG_AMBER", "DEJAVU_ASCENT"].map((c) => extractConst(src, c)).join("\n")}
  ${["fontScale", "dataFlagGeom", "dataFlagSvg"].map((f) => extractFunction(src, f)).join("\n\n")}
  return { dataFlagGeom, dataFlagSvg };
`)();
const opts = fp == null ? {} : { font_percent: Number(fp) };
const svg = dataFlagSvg(dataFlagGeom(opts, w, h), w, h);

const html = `<!doctype html><style>
  @font-face { font-family: "DejaVu Sans Condensed"; src: url("http://cap.test/DejaVuSansCondensed.ttf"); }
  html, body { margin: 0; background: #000; }
  #f { width: ${w}px; height: ${h}px; }
  .gauge-svg { width: 100%; height: 100%; display: block; }
</style><div id="f">${svg}</div>`;

const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: w + 20, height: h + 20 }, deviceScaleFactor: 1 });
await page.route("http://cap.test/**", (route) => {
  const name = new URL(route.request().url()).pathname.slice(1);
  if (name === "") return route.fulfill({ body: html, contentType: "text/html" });
  return route.fulfill({ body: readFileSync(resolve(process.env.FONT_DIR, name)), contentType: "font/ttf" });
});
await page.goto("http://cap.test/");
await page.evaluate(() => document.fonts.ready);
await page.locator("#f").screenshot({ path: out });
await browser.close();
