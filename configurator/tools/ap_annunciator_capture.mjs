// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6286: render the editor's ap_annunciator twin (apStripGeom /
// apStripSvg, extracted from the REAL public/editor.html) at device px, the
// twin half of the side-by-sides with the Pi 5 sheet and with
// tools/ap_annunciator_oracle.py --render. With no CELLS it draws the
// schema's preview block (the one the oracle read from pyEfis,
// test/fixtures/ap_annunciator_pyefis.json) through apStripCells, as the
// editor does; CELLS is JSON in FMAModel.cells' shape for a Pi scene, ON the
// flash phase (default on). BG is an optional PNG drawn under the strip, the
// same pixels the device strip sits on. OPTIONS is optional JSON options.
// FONT_DIR holds the DejaVu TTFs the editor loads (dejavu-fonts-ttf 2.37) and
// B612-Regular.otf / B612-Bold.otf (the Pi 5's B612 1.008; the editor loads
// B612 from Google Fonts).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//   FONT_DIR=/path/to/dejavu/ttf node tools/ap_annunciator_capture.mjs OUT.png W H
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const [out, W, H] = process.argv.slice(2);
const w = Number(W), h = Number(H);
const src = editorSource();
const CONSTS = ["AP_STRIP_SLOTS", "AP_STRIP_TEMPLATES", "AP_STRIP_COLORS", "QT_BOLD_METRICS"];
const FUNCS = ["fontScale", "apColor", "apEsc", "qtBoldFont", "apStripFit", "apStripSlots",
  "apStripCells", "apStripGeom", "apStripSvg"];
const { apStripCells, apStripGeom, apStripSvg } = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { apStripCells, apStripGeom, apStripSvg };
`)();
const pv = JSON.parse(readFileSync(new URL("../test/fixtures/ap_annunciator_pyefis.json", import.meta.url), "utf8")).schema_preview;
const cells = process.env.CELLS ? JSON.parse(process.env.CELLS) : apStripCells(pv);
const svg = apStripSvg(apStripGeom(JSON.parse(process.env.OPTIONS || "{}"), cells, w, h, process.env.ON !== "0"));
const bg = process.env.BG ? `background: url("http://cap.test/bg.png") 0 0 / ${w}px ${h}px;` : "background: transparent;";

const face = (fam, wt, file) => `@font-face { font-family: "${fam}"; font-weight: ${wt}; src: url("http://cap.test/${file}"); }`;
const html = `<!doctype html><style>
  ${face("DejaVu Sans Condensed", 400, "DejaVuSansCondensed.ttf")}
  ${face("DejaVu Sans Condensed", 700, "DejaVuSansCondensed-Bold.ttf")}
  ${face("DejaVu Sans", 400, "DejaVuSans.ttf")}
  ${face("DejaVu Sans", 700, "DejaVuSans-Bold.ttf")}
  ${face("B612", 400, "B612-Regular.otf")}
  ${face("B612", 700, "B612-Bold.otf")}
  html, body { margin: 0; background: transparent; }
  #f { width: ${w}px; height: ${h}px; ${bg} }
  .gauge-svg { width: 100%; height: 100%; display: block; }
</style><div id="f">${svg}</div>`;

const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: w + 20, height: h + 20 }, deviceScaleFactor: 1 });
await page.route("http://cap.test/**", (route) => {
  const name = new URL(route.request().url()).pathname.slice(1);
  if (name === "") return route.fulfill({ body: html, contentType: "text/html" });
  if (name === "bg.png") return route.fulfill({ body: readFileSync(process.env.BG), contentType: "image/png" });
  return route.fulfill({ body: readFileSync(resolve(process.env.FONT_DIR, name)), contentType: name.endsWith(".otf") ? "font/otf" : "font/ttf" });
});
await page.goto("http://cap.test/");
await page.evaluate(() => document.fonts.ready);
await page.locator("#f").screenshot({ path: out, omitBackground: !process.env.BG });
await browser.close();
