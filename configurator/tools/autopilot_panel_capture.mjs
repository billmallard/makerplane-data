// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-5812: render the editor's autopilot_panel twin (apPanelGeom /
// apPanelSvg, extracted from the REAL public/editor.html) at device px, in
// the schema's preview state, the twin half of the side-by-side with
// tools/autopilot_panel_oracle.py --render. The preview block is the one the
// oracle read from pyEfis (test/fixtures/autopilot_panel_pyefis.json).
// FONT_DIR holds the DejaVu TTFs the editor loads from jsdelivr
// (dejavu-fonts-ttf 2.37); OPTIONS is optional JSON merged into the options,
// PREVIEW optional JSON replacing the preview block (a Pi sheet scene).
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//   FONT_DIR=/path/to/dejavu/ttf node tools/autopilot_panel_capture.mjs OUT.png LAYOUT W H [FAMILY]
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const [out, layout, W, H, family] = process.argv.slice(2);
const w = Number(W), h = Number(H);
const src = editorSource();
const CONSTS = ["AP_GROUPS", "AP_DEFAULT_BUTTONS", "AP_BOXES", "AP_COLORS", "AP_FONT_METRICS", "DEJAVU_ASCENT"];
const FUNCS = ["fontScale", "apButtons", "apColor", "apFontMetrics", "apFitToMask", "apText",
  "apPanelGeom", "apEsc", "apTextSvg", "apRectSvg", "apPanelSvg"];
const { apPanelGeom, apPanelSvg } = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { apPanelGeom, apPanelSvg };
`)();
const pv = process.env.PREVIEW ? JSON.parse(process.env.PREVIEW) : JSON.parse(readFileSync(new URL("../test/fixtures/autopilot_panel_pyefis.json", import.meta.url), "utf8")).schema_preview;
const opts = { ...(layout === "full" ? {} : { layout }), ...(family ? { font_family: family } : {}),
  ...JSON.parse(process.env.OPTIONS || "{}") };
const svg = apPanelSvg(apPanelGeom(opts, pv, w, h));

// The editor's own @font-face set.
const face = (fam, wt, file) => `@font-face { font-family: "${fam}"; font-weight: ${wt}; src: url("http://cap.test/${file}"); }`;
const html = `<!doctype html><style>
  ${face("DejaVu Sans Condensed", 400, "DejaVuSansCondensed.ttf")}
  ${face("DejaVu Sans Condensed", 700, "DejaVuSansCondensed-Bold.ttf")}
  ${face("DejaVu Sans", 400, "DejaVuSans.ttf")}
  ${face("DejaVu Sans", 700, "DejaVuSans-Bold.ttf")}
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
