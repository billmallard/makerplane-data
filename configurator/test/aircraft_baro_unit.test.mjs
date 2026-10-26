// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #108 / AER-3917: the "Altimeter setting units" aircraft setting saves and
// loads with the profile, and what loads is what the device is served. Runs
// the REAL normalizeProfile() out of aircraft.html (brace-matched, not
// reimplemented) and the REAL src/device_prefs.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractConst, extractFunction } from "./support/extract.mjs";
import { devicePreferences } from "../src/device_prefs.ts";

const SRC = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "..", "public", "aircraft.html"), "utf8");
const { normalizeProfile } = new Function(
  extractConst(SRC, "BARO_UNITS") + "\n" + extractFunction(SRC, "normalizeProfile")
  + "\nreturn { normalizeProfile };")();

// The save/load path: PUT body JSON -> D1 TEXT -> GET JSON.parse -> normalize.
const roundTrip = (profile) =>
  normalizeProfile(JSON.parse(JSON.stringify(JSON.parse(JSON.stringify({ profile })).profile)));

test("the select offers inHg (default) and hPa / mb", () => {
  const sel = SRC.match(/<select id="barounit">([\s\S]*?)<\/select>/);
  assert.ok(sel, "barounit select present in the Configuration card");
  const opts = [...sel[1].matchAll(/<option value="([^"]+)">([^<]+)</g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(opts, [["inHg", "inHg (default)"], ["hPa", "hPa / mb"]]);
});

test("hPa survives save -> load and is what the device is served", () => {
  const p = { schema_version: 1, meta: { engines: 1, speed_unit: "kt", baro_unit: "hPa" },
              values: { "IAS.Vne": 160 } };
  const back = roundTrip(p);
  assert.equal(back.meta.baro_unit, "hPa");
  assert.equal(back.meta.speed_unit, "kt");
  assert.equal(back.values["IAS.Vne"], 160);
  assert.deepEqual(devicePreferences(JSON.stringify(back)), { units: { Pressure: "hPa" } });
});

test("a profile saved before #108 loads as inHg, and its next save is explicit", () => {
  const old = { schema_version: 1, meta: { engines: 2, speed_unit: "mph" }, values: {} };
  const back = roundTrip(old);
  assert.equal(back.meta.baro_unit, "inHg");
  assert.equal(back.meta.speed_unit, "mph", "other meta keys untouched");
  assert.deepEqual(devicePreferences(JSON.stringify(back)), { units: { Pressure: "inHg" } });
});

test("a junk value loads as inHg rather than reaching the device", () => {
  assert.equal(normalizeProfile({ meta: { baro_unit: "mb" } }).meta.baro_unit, "inHg");
  assert.equal(normalizeProfile(null).meta.baro_unit, "inHg");
});
