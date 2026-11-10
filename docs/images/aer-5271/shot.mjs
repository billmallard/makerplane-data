// SPDX-License-Identifier: Apache-2.0
// AER-5271 (FUEL1a): the aircraft page's Fuel card on meta.fuel schema 2.
// Loads a schema-1 profile, lets the page migrate it, removes tank 2 of 3,
// and saves; the PUT body is run through the Worker's real server check.
// Usage: CHROME=<chrome-headless-shell> PLAYWRIGHT_CORE=<dir> \
//          node shot.mjs <configurator dir> <out dir>
const { chromium } = await import(process.env.PLAYWRIGHT_CORE + "/index.mjs");
import { readFileSync } from "node:fs";
const [W, OUT] = process.argv.slice(2);
const { checkAircraftProfilePut } = await import(W + "/src/aircraft_profile.ts");
const html = readFileSync(W + "/public/aircraft.html", "utf8");
// The fuel group exactly as the live schema (v1, 2026-07-04) carries it.
const schema = { schema_version: 1, generated: "2026-07-04", groups: [
  { id: "fuel", label: "Fuel tanks", repeat: "tank", keys: [
    { key: "FUELQt", label: "Fuel Quantity Tank %t", type: "float", units: "gal",
      min: 0, max: 200, aux: ["Min", "Max", "lowWarn", "lowAlarm"],
      band_aux: ["Min", "Max", "lowWarn", "lowAlarm"], canfix_id: null }] }] };
// Saved by the schema-1 page: tanks 1 and 2 feed tank 3, which feeds the engine.
const saved = { schema_version: 1,
  meta: { engines: 1, cylinders: 4, tanks: 3, speed_unit: "kt", baro_unit: "inHg",
          engine_type: "piston", twin_speeds: false,
          fuel: [{ role: "tip", feeds: "T3" }, { role: "aux", feeds: "T3" },
                 { role: "header", feeds: "E1" }] },
  values: { "FUELQ1.Max": 10, "FUELQ2.Max": 15, "FUELQ3.Max": 5, "FUELQ3.lowWarn": 1 } };
let put = null;
const b = await chromium.launch({ executablePath: process.env.CHROME });
const p = await b.newPage({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
p.on("dialog", d => d.accept());
const json = (r, o) => r.fulfill({ contentType: "application/json", body: JSON.stringify(o) });
await p.route("http://local/**", r => {
  const u = new URL(r.request().url()), m = r.request().method();
  if (u.pathname.startsWith("/assets/editor/aircraft_params_schema.json")) return json(r, schema);
  if (u.pathname === "/api/me") return json(r, { user: { email: "test@example.com" } });
  if (u.pathname === "/api/projects/p1") return json(r, { project: { name: "Fuel test" } });
  if (u.pathname === "/api/projects/p1/aircraft" && m === "GET") return json(r, { version: 4, profile: saved });
  if (u.pathname === "/api/projects/p1/aircraft" && m === "PUT") {
    put = JSON.parse(r.request().postData()).profile;
    const c = checkAircraftProfilePut({ profile: put });
    return c.ok ? json(r, { version: 5 }) : r.fulfill({ status: c.status, contentType: "application/json",
                                                       body: JSON.stringify(c) });
  }
  return r.fulfill({ contentType: "text/html", body: html });
});
async function shot(name) {
  const card = p.locator(".card", { hasText: "Fuel system" });
  await card.screenshot({ path: `${OUT}/${name}.png` });
  const rows = await p.$$eval(".grid.tank", rs => rs.map(r => {
    const q = s => r.querySelector(s);
    const feeds = [...r.querySelectorAll(".feeds label.check")].filter(l => l.querySelector("input").checked)
      .map(l => l.textContent.trim());
    return `${q('input[data-tank-field="label"]').value} slot=${r.querySelectorAll("select")[1].selectedOptions[0].textContent}`
      + ` role=${r.querySelectorAll("select")[0].value} feeds=[${feeds}]`
      + ` cap=${q('input[data-tank-field="capacity"]').value}`;
  }));
  console.log(name, "|", rows.join(" ; "), "| errbox:", JSON.stringify(await p.textContent("#errbox")));
}
await p.goto("http://local/aircraft.html?project=p1");
await p.waitForSelector(".grid.tank");
await shot("1-migrated");
// Remove tank 2 of 3.
await p.locator(".grid.tank").nth(1).locator("button", { hasText: "Remove tank" }).click();
await shot("2-tank2-removed");
// Label the tanks, and give the header 1 gal unusable.
const label = async (i, v) => { const l = p.locator('.grid.tank input[data-tank-field="label"]').nth(i);
  await l.fill(v); await l.dispatchEvent("change"); };
await label(0, "L TIP"); await label(1, "HEADER");
await p.locator('.grid.tank input[data-tank-field="unusable"]').nth(1).fill("1");
await shot("3-labelled");
await p.click("#save");
await p.waitForFunction(() => /saved|not saved|failed/.test(document.getElementById("msg").textContent));
console.log("save:", await p.textContent("#msg"));
console.log("PUT meta.fuel:", JSON.stringify(put.meta.fuel));
console.log("PUT FUELQ values:", JSON.stringify(Object.fromEntries(Object.entries(put.values).filter(([k]) => k.startsWith("FUELQ")))));
await b.close();
