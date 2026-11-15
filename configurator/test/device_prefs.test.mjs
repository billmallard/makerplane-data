// SPDX-License-Identifier: AGPL-3.0-or-later
//
// #108 / AER-3917: the altimeter-setting unit chosen in the aircraft profile
// rides to the device as a top-level `preferences:` block on the served panel
// YAML, and the ETag covers it. Imports the REAL src/device_prefs.ts (Node
// strips the types), the same module GET /device/config calls.

import { test } from "node:test";
import assert from "node:assert/strict";
import yaml from "js-yaml";
import { deviceFixgw, devicePreferences, serveDeviceConfig } from "../src/device_prefs.ts";

const PANEL = [
  "main:",
  "  defaultScreen: PFD",
  "screens:",
  "  PFD:",
  "    instruments:",
  "    - type: numeric_display",
  "      options: {dbkey: BARO, pressure: true, decimal_places: 2}",
  "    - type: static_text",
  "      options: {text: 1e3}",   // YAML 1.1 string, 1.2 float: must survive as text
  "",
].join("\n");

const profile = (meta) => JSON.stringify({ schema_version: 1, meta, values: {} });

test("devicePreferences: baro_unit -> units.Pressure", () => {
  assert.deepEqual(devicePreferences(profile({ baro_unit: "hPa" })), { units: { Pressure: "hPa" } });
  assert.deepEqual(devicePreferences(profile({ baro_unit: "inHg" })), { units: { Pressure: "inHg" } });
  assert.deepEqual(devicePreferences({ meta: { baro_unit: "hPa" } }), { units: { Pressure: "hPa" } });
});

test("devicePreferences never throws; unknown / absent -> {}", () => {
  for (const p of [null, undefined, "", "{not json", "[]", 42, profile({}),
                   profile({ speed_unit: "kt" }), profile({ baro_unit: "mb" }),
                   profile({ baro_unit: "HPA" }), profile({ baro_unit: 1013 }),
                   JSON.stringify({ meta: "x" })]) {
    assert.deepEqual(devicePreferences(p), {}, `input ${String(p)}`);
  }
});

test("no preferences: body byte-identical, ETag unchanged from before #108", async () => {
  for (const p of [null, profile({ speed_unit: "kt" })]) {
    const s = await serveDeviceConfig(PANEL, 7, p);
    assert.equal(s.body, PANEL);
    assert.equal(s.etag, '"v7"');
  }
});

test("hPa: panel text kept verbatim, preferences block appended and parseable", async () => {
  const s = await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa" }));
  assert.ok(s.body.startsWith(PANEL), "panel YAML must reach the device byte-for-byte");
  const doc = yaml.load(s.body);
  assert.deepEqual(doc.preferences, { units: { Pressure: "hPa" } });
  assert.deepEqual(Object.keys(doc.screens), ["PFD"]);
  assert.match(s.etag, /^"v7-p[0-9a-f]{12}"$/);
});

test("appending works when the stored blob has no trailing newline", async () => {
  const s = await serveDeviceConfig(PANEL.trimEnd(), 3, profile({ baro_unit: "hPa" }));
  assert.equal(yaml.load(s.body).preferences.units.Pressure, "hPa");
  assert.equal(yaml.load(s.body).main.defaultScreen, "PFD");
});

test("ETag: changes with the unit, not with unrelated profile edits", async () => {
  const hpa = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa" }))).etag;
  const hpa2 = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa", speed_unit: "mph", engines: 2 }))).etag;
  const inhg = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "inHg" }))).etag;
  const v8 = (await serveDeviceConfig(PANEL, 8, profile({ baro_unit: "hPa" }))).etag;
  assert.equal(hpa, hpa2, "a Vne/speed-unit edit must not make devices reinstall + restart");
  assert.notEqual(hpa, inhg, "a unit change must not 304 forever");
  assert.notEqual(hpa, v8, "a new panel version must not 304");
  assert.notEqual(hpa, '"v7"');
});

test("a blob carrying its own top-level preferences: key is served unchanged", async () => {
  const own = PANEL + "preferences:\n  units:\n    Pressure: inHg\n";
  const s = await serveDeviceConfig(own, 7, profile({ baro_unit: "hPa" }));
  assert.equal(s.body, own);
  assert.equal(s.etag, '"v7"');
});

// P3f / AER-5458: the ownship address rides as a top-level `fixgw:` block.

test("deviceFixgw: N825V -> AB4549; the hex entry wins; blank -> {}", () => {
  assert.deepEqual(deviceFixgw(profile({ registration: "N825V" })), { ownship_address: "AB4549" });
  assert.deepEqual(deviceFixgw({ meta: { registration: "n825v" } }), { ownship_address: "AB4549" });
  assert.deepEqual(deviceFixgw(profile({ registration: "N825V", icao_address: "0xc0ffee" })),
                   { ownship_address: "C0FFEE" });
  assert.deepEqual(deviceFixgw(profile({ registration: "C-GABC", icao_address: "C01234" })),
                   { ownship_address: "C01234" });
  for (const p of [null, "", "{not json", profile({}), profile({ registration: "" }),
                   profile({ registration: "C-GABC" }), profile({ icao_address: "000000" }),
                   profile({ baro_unit: "hPa" })]) {
    assert.deepEqual(deviceFixgw(p), {}, `input ${String(p)}`);
  }
});

test("N825V: panel kept verbatim, fixgw block appended with the address quoted", async () => {
  const s = await serveDeviceConfig(PANEL, 7, profile({ registration: "N825V" }));
  assert.ok(s.body.startsWith(PANEL));
  assert.match(s.body, /^fixgw:\n  ownship_address: "AB4549"\n$/m);
  const doc = yaml.load(s.body);
  assert.deepEqual(doc.fixgw, { ownship_address: "AB4549" });
  assert.equal(doc.preferences, undefined);
  assert.match(s.etag, /^"v7-p[0-9a-f]{12}"$/);
});

test("an all-digit address stays a string (quoted)", async () => {
  const s = await serveDeviceConfig(PANEL, 7, profile({ icao_address: "123456" }));
  assert.equal(yaml.load(s.body).fixgw.ownship_address, "123456");
});

test("both blocks: each parses, panel untouched", async () => {
  const s = await serveDeviceConfig(PANEL.trimEnd(), 7,
    profile({ baro_unit: "hPa", registration: "N825V" }));
  const doc = yaml.load(s.body);
  assert.deepEqual(doc.preferences, { units: { Pressure: "hPa" } });
  assert.deepEqual(doc.fixgw, { ownship_address: "AB4549" });
  assert.equal(doc.main.defaultScreen, "PFD");
});

test("ETag: a unit-only profile keeps its #108 ETag; the address moves it", async () => {
  const unitOnly = profile({ baro_unit: "hPa" });
  const before = (await serveDeviceConfig(PANEL, 7, unitOnly)).etag;
  // The #108 hash: over JSON.stringify of the preferences block alone.
  const buf = await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(JSON.stringify({ units: { Pressure: "hPa" } })));
  const h = Array.from(new Uint8Array(buf).slice(0, 6), (b) => b.toString(16).padStart(2, "0")).join("");
  assert.equal(before, `"v7-p${h}"`, "no device without an address may re-pull for this change");
  const a = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa", registration: "N825V" }))).etag;
  const b = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa", registration: "N825W" }))).etag;
  const c = (await serveDeviceConfig(PANEL, 7, profile({ baro_unit: "hPa", registration: "N825V", engines: 2 }))).etag;
  assert.notEqual(a, before);
  assert.notEqual(a, b, "an address change must not 304");
  assert.equal(a, c, "an unrelated profile edit must not re-pull");
});

test("blank registration: body and ETag exactly as without the field", async () => {
  const s = await serveDeviceConfig(PANEL, 7, profile({ registration: "" }));
  assert.equal(s.body, PANEL);
  assert.equal(s.etag, '"v7"');
});

test("a blob carrying its own top-level fixgw: key gets no second one", async () => {
  const own = PANEL + "fixgw:\n  ownship_address: \"A00001\"\n";
  const s = await serveDeviceConfig(own, 7, profile({ registration: "N825V" }));
  assert.equal(s.body, own);
  assert.equal(s.etag, '"v7"');
  const both = await serveDeviceConfig(own, 7, profile({ registration: "N825V", baro_unit: "hPa" }));
  assert.equal(yaml.load(both.body).fixgw.ownship_address, "A00001");
  assert.equal(yaml.load(both.body).preferences.units.Pressure, "hPa");
});
