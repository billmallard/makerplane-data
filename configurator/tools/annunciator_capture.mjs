// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6348: render the editor's annunciator twin (annGeom / annSvg,
// extracted from the REAL public/editor.html) at device px, the twin half of
// the side-by-sides with tools/annunciator_oracle.py --render. The schema it
// reads is the one the oracle's pyEfis built (test/fixtures/
// annunciator_pyefis.json). OPTIONS is JSON options (default: the oracle's
// ANN1 bench block); STATES is lit, off, stale, or a JSON list per tile.
// FONT_DIR holds the DejaVu TTFs (dejavu-fonts-ttf 2.37) and B612-Bold.otf
// (Airbus B612 1.008, the bench's).
//
//   PW=/path/to/playwright CHROME=/path/to/chrome-headless-shell \
//   FONT_DIR=/path/to/fonts node tools/annunciator_capture.mjs OUT.png W H
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const [out, W, H] = process.argv.slice(2);
const w = Number(W), h = Number(H);
const src = editorSource();
const CONSTS = ["QT_BOLD_METRICS", "ANN_STALE_COLOR", "ANN_REVERSE_TEXT", "ANN_QUALITY_FLAGS"];
const FUNCS = ["apColor", "apEsc", "apRectSvg", "qtBoldFont", "annIsMap", "annSpec", "annLevel",
  "annAlertBound", "annPreviewTile", "annInt", "annFloat", "annGrid", "annRects", "annFitPx",
  "annGeom", "annSvg"];
const { annGeom, annSvg } = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { annGeom, annSvg };
`)();
const FIX = JSON.parse(readFileSync(new URL("../test/fixtures/annunciator_pyefis.json", import.meta.url), "utf8"));
const bench = FIX.cases.find((c) => c.options.tiles?.[0]?.label === "AP").options;
const o = process.env.OPTIONS ? JSON.parse(process.env.OPTIONS) : bench;
const n = (o.tiles || []).length;
const st = process.env.STATES || "lit";
const states = st.startsWith("[") ? JSON.parse(st) : Array(n).fill(st);
const svg = annSvg(annGeom(o, FIX.schema.instrument, FIX.schema.annunciator, states, w, h));

const face = (fam, wt, file) => `@font-face { font-family: "${fam}"; font-weight: ${wt}; src: url("http://cap.test/${file}"); }`;
const html = `<!doctype html><style>
  ${face("DejaVu Sans Condensed", 700, "DejaVuSansCondensed-Bold.ttf")}
  ${face("DejaVu Sans", 700, "DejaVuSans-Bold.ttf")}
  ${face("B612", 700, "B612-Bold.otf")}
  html, body { margin: 0; background: transparent; }
  #f { width: ${w}px; height: ${h}px; }
  .gauge-svg { width: 100%; height: 100%; display: block; }
</style><div id="f">${svg}</div>`;

const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: w + 20, height: h + 20 }, deviceScaleFactor: 1 });
await page.route("http://cap.test/**", (route) => {
  const name = new URL(route.request().url()).pathname.slice(1);
  if (name === "") return route.fulfill({ body: html, contentType: "text/html" });
  return route.fulfill({ body: readFileSync(resolve(process.env.FONT_DIR, name)), contentType: name.endsWith(".otf") ? "font/otf" : "font/ttf" });
});
await page.goto("http://cap.test/");
await page.evaluate(() => document.fonts.ready);
await page.locator("#f").screenshot({ path: out, omitBackground: true });
await browser.close();
