// SPDX-License-Identifier: Apache-2.0
// AER-5147: render the aircraft page's Ownship identity card in four states.
// Usage: CHROME=<chrome-headless-shell> PLAYWRIGHT_CORE=<dir> \
//          node shot.mjs <configurator dir> <out dir>
const { chromium } = await import(process.env.PLAYWRIGHT_CORE + "/index.mjs");
import { readFileSync } from "node:fs";
const [W, OUT] = process.argv.slice(2);
const html = readFileSync(W + "/public/aircraft.html", "utf8");
const schema = { schema_version: 1, generated: "stub", groups: [] };
const b = await chromium.launch({ executablePath: process.env.CHROME });
const p = await b.newPage({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
await p.route("http://local/**", r => {
  const u = new URL(r.request().url());
  if (u.pathname.startsWith("/assets/editor/aircraft_params_schema.json"))
    return r.fulfill({ contentType: "application/json", body: JSON.stringify(schema) });
  return r.fulfill({ contentType: "text/html", body: html });
});
await p.goto("http://local/aircraft.html?demo=1");
await p.waitForSelector("#registration");
const states = [["n825v", "", "1-derived"], ["N825V", "a1b2c3", "2-override"],
                ["N0123", "", "3-invalid"], ["C-GABC", "", "4-non-us"]];
for (const [reg, hex, name] of states) {
  await p.fill("#registration", reg); await p.fill("#icaoaddr", hex);
  const card = p.locator(".card", { hasText: "Ownship identity" });
  const box = await card.boundingBox();
  const err = await p.locator("#errbox").boundingBox();
  await p.screenshot({ path: `${OUT}/${name}.png`,
    clip: { x: box.x - 4, y: box.y - 4, width: box.width + 8,
            height: (err ? err.y + err.height : box.y + box.height) - box.y + 12 } });
  console.log(name, await p.textContent("#ownship-out"), "|", await p.textContent("#errbox"), "| save disabled:", await p.isDisabled("#save"));
}
await b.close();
