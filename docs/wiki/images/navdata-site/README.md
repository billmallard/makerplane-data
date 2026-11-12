# Images: the navdata website

Captured 2026-11-12 from the live public site, https://navdata.aerocommons.org,
catalog generated 2026-11-11. Headless Chromium (Playwright 1.62.1,
chrome-headless-shell), viewport 1366x900. No account is involved and no
personal data appears.

| File | What it shows | Page |
|---|---|---|
| `navdata_home.png` | The catalog, Navigation data group | `/` |
| `inspector.png` | The pack inspector with `navaids-conus` 2611 open, its trust checks, and a query | `/inspect.html?pack=navaids-conus&cycle=2611&sha256=...&q=...` |
| `reports_index.png` | The test reports index | `/reports/index.html` |
| `report_history.png` | One pack's editions and row counts | `/reports/navaids-conus/index.html` |
| `report_header.png` | The top of one edition's report | `/reports/procedures-conus/2612/report.html` |
| `report_checks.png` | Two rows of that report's check table | the same page |
| `reports_check.png` | The first steps of Check it yourself | `/reports/check.html` |
| `schema_index.png` | The pack formats index | `/schema/index.html` |

Each picture is cropped to the page content; the boxes are listed below.

## To regenerate

Chromium needs fontconfig to find a font directory, or a page with a
`<select>` crashes the headless shell. A minimal `fonts.conf`:

```xml
<fontconfig><dir>/usr/share/fonts</dir></fontconfig>
```

Then, with `PW` the Playwright package directory and `CHROME` the
headless-shell binary:

```bash
FONTCONFIG_FILE=fonts.conf PW=... CHROME=... node cap.mjs .    # home, schema index, inspector
FONTCONFIG_FILE=fonts.conf PW=... CHROME=... node cap2.mjs     # report pages, cut above the disclaimer
```

The inspector query is
`SELECT id, type, name, freq, elev_ft, lat, lon FROM navaids WHERE id IN ('DEN','SEA','LAX','OSH') ORDER BY id`.

`cap.mjs`:

```js
const { chromium } = await import(process.env.PW + "/index.mjs");
const out = process.argv[2];
const b = await chromium.launch({ executablePath: process.env.CHROME });
const pg = await b.newPage({ viewport: { width: 1366, height: 900 } });
const site = "https://navdata.aerocommons.org";
async function shot(url, file, opts = {}) {
  await pg.goto(site + url, { waitUntil: "networkidle", timeout: 90000 });
  if (opts.after) await opts.after();
  await pg.waitForTimeout(1500);
  await pg.screenshot({ path: `${out}/${file}`, fullPage: !!opts.full, clip: opts.clip });
  console.log(file, await pg.title());
}
await shot("/", "navdata_home.png");
await shot("/schema/index.html", "schema_index.png", { full: true });
await shot("/inspect.html?pack=navaids-conus&cycle=2611&sha256=2cb9eca314387c9d93f0fe92cd33f509bfe989cac1e7331b88077ce41ad5725b&q=" + encodeURIComponent("SELECT id, type, name, freq, elev_ft, lat, lon FROM navaids WHERE id IN ('DEN','SEA','LAX','OSH') ORDER BY id"), "inspector.png", {
  full: true,
  after: async () => {
    if (await pg.locator("#qsec").isHidden()) { await pg.locator("#open").waitFor({ state: "visible" }); await pg.waitForFunction(() => !document.querySelector("#open").disabled, null, { timeout: 60000 }); await pg.locator("#open").click(); }
    await pg.locator("#qsec").waitFor({ state: "visible", timeout: 90000 });
    await pg.locator("#run").click();
    await pg.locator("#rsec").waitFor({ state: "visible", timeout: 30000 });
  } });
await b.close();
```

`cap2.mjs` (the report pages, captured to a fixed height; the crop table
below gives the final box of each picture):

```js
const { chromium } = await import(process.env.PW + "/index.mjs");
const b = await chromium.launch({ executablePath: process.env.CHROME });
const pg = await b.newPage({ viewport: { width: 1366, height: 900 } });
const site = "https://navdata.aerocommons.org";
for (const [url, file, h] of [
  ["/reports/index.html", "reports_index.png", 827],
  ["/reports/navaids-conus/index.html", "report_history.png", 1304],
  ["/reports/procedures-conus/2612/report.html", "report_procedures.png", 1250],
  ["/reports/check.html", "reports_check.png", 788],
]) {
  await pg.goto(site + url, { waitUntil: "networkidle", timeout: 90000 });
  await pg.waitForTimeout(1000);
  await pg.screenshot({ path: file, fullPage: true, clip: { x: 0, y: 0, width: 1366, height: h } });
}
await b.close();
```

| Picture | Source capture | Crop box (x0, y0, x1, y1) |
|---|---|---|
| `navdata_home.png` | `navdata_home.png` | 210, 0, 1156, 900 |
| `inspector.png` | `inspector.png` (full page) | 130, 0, 1236, 1030 |
| `reports_index.png` | `reports_index.png` | 140, 0, 1226, 815 |
| `report_history.png` | `report_history.png` | 140, 0, 1226, 575 |
| `report_header.png` | `report_procedures.png` | 140, 0, 1226, 280 |
| `report_checks.png` | `report_procedures.png` | 140, 1025, 1226, 1245 |
| `reports_check.png` | `reports_check.png` | 140, 0, 1226, 640 |
| `schema_index.png` | `schema_index.png` (full page) | 140, 0, 1226, 690 |

The crops were made with Pillow, `Image.open(src).crop((x0, y0, x1, y1))`.
