// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6356: render the editor's cas_window / cas_marker twins (casWindowGeom /
// casMarkerGeom / casSvg, extracted from the REAL public/editor.html) at
// device px, the twin half of the side-by-side with tools/cas_oracle.py
// --render. Each scene names a fixture case (test/fixtures/cas_pyefis.json)
// by type, state, size, flash time and options, and the twin draws that
// case's list as the device's model read it. Writes OUT_DIR/twin_<name>.png
// on black, the screen colour the oracle's renders are composited over by
// tools/cas_compare.py. FONT_DIR holds the DejaVu TTFs (dejavu-fonts-ttf
// 2.37) and B612-Regular.otf / B612-Bold.otf.
//
//   PW=/path/to/node_modules/playwright-core CHROME=/path/to/chrome-headless-shell \
//   FONT_DIR=/path/to/fonts node tools/cas_capture.mjs OUT_DIR
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { editorSource, extractFunction, extractConst } from "../test/support/extract.mjs";

// The scenes, shared with tools/cas_compare.py.
const SCENES = JSON.parse(readFileSync(new URL("./cas_scenes.json", import.meta.url), "utf8"));

const { chromium } = await import(resolve(process.env.PW, "index.mjs"));
const outDir = process.argv[2];
const src = editorSource();
const CONSTS = ["QT_BOLD_METRICS", "CAS_TILE_LEVEL", "CAS_MARKER_TEXT", "CAS_COLORS",
  "CAS_REVERSE_TEXT"];
const FUNCS = ["fontScale", "apColor", "apEsc", "qtBoldFont", "casParseSlot", "casMessages",
  "casLines", "casWindowRows", "casFitPx", "casBackground", "casTile", "casWindowGeom",
  "casMarkerGeom", "casSvg"];
const twin = new Function(`
  ${CONSTS.map((c) => extractConst(src, c)).join("\n")}
  ${FUNCS.map((f) => extractFunction(src, f)).join("\n\n")}
  return { casWindowGeom, casMarkerGeom, casSvg };
`)();
const FIXTURE = JSON.parse(readFileSync(new URL("../test/fixtures/cas_pyefis.json", import.meta.url), "utf8"));
const LEVEL_NUM = { warning: 3, caution: 2, advisory: 1 };

const face = (fam, wt, file) => `@font-face { font-family: "${fam}"; font-weight: ${wt}; src: url("http://cap.test/${file}"); }`;
const fonts = [face("DejaVu Sans Condensed", 400, "DejaVuSansCondensed.ttf"),
  face("DejaVu Sans Condensed", 700, "DejaVuSansCondensed-Bold.ttf"),
  face("DejaVu Sans", 400, "DejaVuSans.ttf"), face("DejaVu Sans", 700, "DejaVuSans-Bold.ttf"),
  face("B612", 400, "B612-Regular.otf"), face("B612", 700, "B612-Bold.otf")].join("\n");

const browser = await chromium.launch({ executablePath: process.env.CHROME });
for (const s of SCENES) {
  const c = FIXTURE.cases.find((x) => x.type === s.type && x.state === s.state && x.w === s.w && x.h === s.h
    && x.t === s.t && JSON.stringify(x.options) === JSON.stringify(s.options));
  if (!c) throw new Error(`no fixture case for scene ${s.name}`);
  const g = s.type === "cas_window"
    ? twin.casWindowGeom(c.options, c.messages, c.count, c.w, c.h, c.on)
    : twin.casMarkerGeom(c.options, c.tile ? LEVEL_NUM[c.tile.level] : 0, c.tile ? c.tile.flash : false, c.w, c.h, c.on);
  const html = `<!doctype html><style>${fonts}
    html, body { margin: 0; background: #000; }
    #f { width: ${c.w}px; height: ${c.h}px; background: #000; }
    .gauge-svg { width: 100%; height: 100%; display: block; }
  </style><div id="f">${twin.casSvg(g)}</div>`;
  const page = await browser.newPage({ viewport: { width: c.w + 20, height: c.h + 20 }, deviceScaleFactor: 1 });
  await page.route("http://cap.test/**", (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    if (name === "") return route.fulfill({ body: html, contentType: "text/html" });
    return route.fulfill({ body: readFileSync(resolve(process.env.FONT_DIR, name)), contentType: name.endsWith(".otf") ? "font/otf" : "font/ttf" });
  });
  await page.goto("http://cap.test/");
  await page.evaluate(() => document.fonts.ready);
  await page.locator("#f").screenshot({ path: resolve(outDir, `twin_${s.name}.png`) });
  await page.close();
}
await browser.close();
