# Images: the configurator

Captured 2026-11-12. Headless Chromium (Playwright 1.62.1,
chrome-headless-shell), viewport 1440x900.

The pages are the real, unmodified `configurator/public/` pages from
makerplane-data `dev` (d1a4ea5). The account API is answered locally with an
example user ("Example builder"), an example project ("Example RV-7"), two
example devices and an example pairing code (`ABCD2345`), so no real account,
email address, token or pairing code appears. The editor's instrument catalog,
pictures and the aircraft parameter schema are fetched from the dev
configurator's `/assets/editor/`. The panel on the canvas is
`example_panel.yaml` in this folder. The Aircraft page runs in its own demo
mode (`/aircraft?demo=1`), with the Cessna 170B template loaded.

| File | What it shows | Source capture and crop box (x0, y0, x1, y1) |
|---|---|---|
| `signin.png` | The sign-in card | `signin.png`, 340, 60, 1100, 395 |
| `dashboard.png` | A project, its devices, and a pairing code | `dashboard_pair.png`, 340, 80, 1100, 725 |
| `editor.png` | The Panel Editor with the example panel and the YAML pane | `editor.png`, uncropped |
| `editor_properties.png` | Left: the HSI's colour options (Default, a token, Custom). Right: a tab section's options and tabs | `props_hsi_colors.png` and `props_tabs.png` side by side, each the inspector column (1160, 94, 1440, 900) |
| `aircraft_top.png` | The header, Configuration and Ownship identity cards, before a template | `aircraft.png`, 0, 0, 1440, 485 |
| `aircraft_speeds.png` | The Airspeeds group from the C170B template, with the arc preview | `aircraft_speeds.png`, 248, 60, 1192, 462 |
| `aircraft_error.png` | The same with Vno set to 40: the speed-order error | `aircraft_error.png`, 240, 55, 1200, 500 |

For the HSI picture, the script sets `course_color: nav.course`,
`heading_bug_enabled: true` and `heading_bug_color: "#33ccff"` on the HSI so the
three kinds of colour choice show at once.

## To regenerate

Copy `configurator/public/` from the commit above into the working folder,
put `example_panel.yaml` beside it as `design.yaml`, and run with fontconfig
able to find a font directory (see `../navdata-site/README.md`):

```bash
FONTCONFIG_FILE=fonts.conf PW=... CHROME=... node capture_configurator.mjs   # sign-in, dashboard, editor
FONTCONFIG_FILE=fonts.conf PW=... CHROME=... node capac.mjs    # aircraft page
```

The two inspector crops were taken by scrolling the inspector to the Course
pointer colour field and to the **+ Add tab** button, then clipping the inspector
column. The crops were made with Pillow.

`capture_configurator.mjs` (the sign-in page, the dashboard and the editor):

```js
import { readFileSync, mkdirSync } from "node:fs";
import { resolve, extname } from "node:path";
const { chromium } = await import(process.env.PW + "/index.mjs");
const PUBLIC = resolve("configurator/public");
const ASSET_ORIGIN = "https://pyefis-dev.aerocommons.org";
const OUT = resolve("out"); mkdirSync(OUT, { recursive: true });
const DESIGN = readFileSync("design.yaml", "utf8");
const USER = { id: 1, name: "Example builder" };
const PROJECTS = [{ id: 1, name: "Example RV-7", updated_at: "2026-11-12 14:00" }];
const DEVICES = [
  { id: 1, name: "PFD", kind: "pyefis", claimed_at: "2026-11-01 10:00", last_pull_at: new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 19).replace("T", " "), latest_version: 4, latest_config_at: "2026-11-10 09:00" },
  { id: 2, name: "MFD", kind: "pyefis", claimed_at: null, latest_version: null },
];
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
let signedIn = true;
const b = await chromium.launch({ executablePath: process.env.CHROME });
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
pg.on("dialog", d => d.dismiss());
await pg.route("http://cfg.local/**", async (route) => {
  const req = route.request(); const p = new URL(req.url()).pathname; const m = req.method();
  if (p === "/api/me") return signedIn ? route.fulfill({ json: { user: USER } }) : route.fulfill({ status: 401, json: { error: "signed out" } });
  if (p === "/api/projects" && m === "GET") return route.fulfill({ json: { projects: PROJECTS } });
  if (p === "/api/projects/1" && m === "GET") return route.fulfill({ json: { project: PROJECTS[0], devices: DEVICES } });
  if (p === "/api/devices/1/pair") return route.fulfill({ json: { claim_code: "ABCD2345", expires_in: 900 } });
  if (p === "/api/devices/1/config" && m === "GET") return route.fulfill({ body: DESIGN, contentType: "text/yaml" });
  if (p === "/api/devices/1/preferences") return route.fulfill({ json: { preferences: { units: { Pressure: "inHg" } } } });
  if (p === "/api/projects/1/aircraft" && m === "GET") return route.fulfill({ status: 404, json: { error: "no profile" } });
  if (p.startsWith("/api/")) return route.fulfill({ status: 404, json: {} });
  if (p.startsWith("/assets/")) {
    const r = await fetch(`${ASSET_ORIGIN}${p}`);
    return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()), contentType: r.headers.get("content-type") || "" });
  }
  const file = p === "/" ? "index.html" : p === "/editor" ? "editor.html" : p === "/aircraft" ? "aircraft.html" : p.slice(1);
  try { return route.fulfill({ body: readFileSync(resolve(PUBLIC, file)), contentType: TYPES[extname(file)] || "application/octet-stream" }); }
  catch { return route.fulfill({ status: 404, body: "" }); }
});
const shot = async (name, opts = {}) => { await pg.waitForTimeout(800); await pg.screenshot({ path: resolve(OUT, name), ...opts }); console.log(name); };
// signed out
signedIn = false;
await pg.goto("http://cfg.local/"); await pg.waitForLoadState("networkidle");
await shot("signin.png");
// dashboard
signedIn = true;
await pg.goto("http://cfg.local/"); await pg.waitForLoadState("networkidle");
await shot("dashboard_projects.png", { fullPage: true });
await pg.getByRole("button", { name: /Open/ }).first().click();
await pg.waitForTimeout(1200);
await shot("dashboard_project.png", { fullPage: true });
await pg.getByRole("button", { name: /^Re-pair$/ }).first().click();
await pg.waitForTimeout(800);
await shot("dashboard_pair.png", { fullPage: true });
// editor
await pg.goto("http://cfg.local/editor?device=1&project=1");
await pg.waitForFunction(() => typeof state !== "undefined" && state.screens && state.screens.length >= 1, null, { timeout: 60000 });
await pg.evaluate(() => document.fonts.ready); await pg.waitForTimeout(4000);
await shot("editor.png");
await pg.evaluate(() => { const c = document.getElementById("codeToggle"); if (c.checked) c.click(); });
await pg.waitForTimeout(1500);
await shot("editor_canvas.png");
// HSI selected, with a token and a custom colour
await pg.evaluate(() => { const i = state.instruments.findIndex(x => x.type === "horizontal_situation_indicator");
  state.instruments[i].options = { ...(state.instruments[i].options || {}), course_color: "nav.course", heading_bug_enabled: true, heading_bug_color: "#33ccff" };
  state.selected = i; renderCanvas(); renderProps(); });
await pg.waitForTimeout(1200);
await shot("editor_hsi.png");
await pg.locator("#props label", { hasText: /^Course/ }).first().scrollIntoViewIfNeeded(); await pg.waitForTimeout(400);
await pg.screenshot({ path: resolve(OUT, "props_hsi_colors.png"), clip: { x: 1160, y: 94, width: 280, height: 806 } });
// tab section selected
await pg.evaluate(() => { const i = state.instruments.findIndex(x => x.type === "tab_section"); state.selected = i; renderCanvas(); renderProps(); });
await pg.waitForTimeout(1200);
await shot("editor_tabs.png");
await pg.getByRole("button", { name: "+ Add tab" }).scrollIntoViewIfNeeded(); await pg.waitForTimeout(400);
await pg.screenshot({ path: resolve(OUT, "props_tabs.png"), clip: { x: 1160, y: 94, width: 280, height: 806 } });
await b.close();
```

`capac.mjs` (the Aircraft page):

```js
import { readFileSync } from "node:fs";
import { resolve, extname } from "node:path";
const { chromium } = await import(process.env.PW + "/index.mjs");
const PUBLIC = resolve("configurator/public"); const OUT = resolve("out");
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json" };
const b = await chromium.launch({ executablePath: process.env.CHROME });
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
pg.on("dialog", d => d.accept());
await pg.route("http://cfg.local/**", async (route) => {
  const p = new URL(route.request().url()).pathname;
  if (p.startsWith("/assets/")) { const r = await fetch(`https://pyefis-dev.aerocommons.org${p}`); return route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()), contentType: r.headers.get("content-type") || "" }); }
  if (p.startsWith("/api/")) return route.fulfill({ status: 404, json: {} });
  const file = p === "/aircraft" ? "aircraft.html" : p.slice(1);
  try { return route.fulfill({ body: readFileSync(resolve(PUBLIC, file)), contentType: TYPES[extname(file)] || "application/octet-stream" }); } catch { return route.fulfill({ status: 404, body: "" }); }
});
await pg.goto("http://cfg.local/aircraft?demo=1"); await pg.waitForLoadState("networkidle"); await pg.waitForTimeout(1500);
await pg.selectOption("#template", "c170b"); await pg.waitForTimeout(1500);
const speeds = pg.locator("text=Airspeeds").first(); await speeds.scrollIntoViewIfNeeded();
await pg.evaluate(() => window.scrollTo(0, document.querySelector('input[data-fk="IAS.Vs0"]')?.getBoundingClientRect().top + window.scrollY - 160));
await pg.waitForTimeout(800);
await pg.screenshot({ path: resolve(OUT, "aircraft_speeds.png") });
const vno = pg.locator('input[data-fk="IAS.Vno"]'); console.log("vno", await vno.count(), await vno.inputValue().catch(()=>"?"));
await vno.fill("40"); await vno.dispatchEvent("input"); await vno.dispatchEvent("change"); await pg.waitForTimeout(800);
await pg.screenshot({ path: resolve(OUT, "aircraft_error.png") });
await pg.evaluate(() => window.scrollTo(0, 0)); await pg.waitForTimeout(500);
await pg.screenshot({ path: resolve(OUT, "aircraft_top_error.png") });
await b.close();
```
