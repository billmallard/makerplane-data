// SPDX-License-Identifier: AGPL-3.0-or-later
//
// ACINI1 (AER-5993): the compiled aircraft.ini rides to the device in the
// served `fixgw:` block with its sha256, and the ETag covers it. Imports the
// REAL public/aircraft_ini.js (the page's Download button runs it too) and
// src/device_prefs.ts (the module GET /device/config calls).

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import yaml from "js-yaml";
import { aircraftIniLines, buildAircraftIni, expandCylinders } from "../public/aircraft_ini.js";
import { AIRCRAFT_INI_HEADER, deviceFixgw, serveDeviceConfig } from "../src/device_prefs.ts";

const PANEL = "main:\n  defaultScreen: PFD\nscreens:\n  PFD:\n    instruments: []\n";
const sha = (s) => createHash("sha256").update(s, "utf8").digest("hex");
const prof = (values, meta = {}) => JSON.stringify({ schema_version: 2, meta, values });

test("cylinder pseudo-instances expand to the profile's count", () => {
  assert.deepEqual(expandCylinders({ "CHT1.highWarn": 400, "EGT1.Max": 1650, "OILP1.Min": 0 }, 2), {
    "CHT11.highWarn": 400, "CHT12.highWarn": 400,
    "EGT11.Max": 1650, "EGT12.Max": 1650, "OILP1.Min": 0,
  });
  assert.deepEqual(expandCylinders({ "CHT1.Max": 500 }, undefined), {});
});

test("lines: sorted, grouped by instance, only plain keys with finite numbers", () => {
  const lines = aircraftIniLines({
    meta: { cylinders: 1 },
    values: {
      "IAS.Vne": 160, "IAS.Vs": 48, "OILT1.Max": 245, "CHT1.highAlarm": 500,
      "IAS.Bad": null, "IAS.Str": "12", "IAS.Inf": Infinity,
      "IAS.Vfe = 1\nX": 5, "; comment": 1, "A.b.c": 2,
    },
  });
  assert.deepEqual(lines, [
    "CHT11.highAlarm = 500", "", "IAS.Vne = 160", "IAS.Vs = 48", "", "OILT1.Max = 245",
  ]);
});

test("file: '#' header, one trailing newline, never a blank last line", () => {
  const ini = buildAircraftIni({ values: { "IAS.Vne": 160 } }, ["a = b ; looks like a key", "two\nlines"]);
  assert.equal(ini, "# a = b ; looks like a key\n# two lines\n\nIAS.Vne = 160\n");
  for (const l of ini.split("\n")) assert.ok(l === "" || l.startsWith("#") || / = /.test(l), l);
  assert.equal(buildAircraftIni({ values: {} }, ["h"]), "# h\n");
});

test("deviceFixgw: the ini only when the profile holds a value", () => {
  assert.deepEqual(deviceFixgw(prof({})), {});
  assert.deepEqual(deviceFixgw(prof({ "IAS.Vs": "x" })), {});
  const f = deviceFixgw(prof({ "IAS.Vne": 160 }));
  assert.equal(f.aircraft_ini, AIRCRAFT_INI_HEADER.map((h) => "# " + h).join("\n") + "\n\nIAS.Vne = 160\n");
  assert.equal(f.ownship_address, undefined);
  for (const bad of [null, "", "{nope", "[]", 42]) assert.deepEqual(deviceFixgw(bad), {}, String(bad));
});

test("served: the literal block parses back to the exact file, sha matches", async () => {
  const p = prof({ "IAS.Vne": 160, "IAS.Vs0": 40, "CHT1.highWarn": 435, "FUELQ1.Max": 21.5 },
                 { cylinders: 4, registration: "N825V" });
  const s = await serveDeviceConfig(PANEL, 3, p);
  assert.ok(s.body.startsWith(PANEL), "panel text kept verbatim");
  const fx = yaml.load(s.body).fixgw;
  assert.equal(fx.ownship_address, "AB4549");
  assert.equal(fx.aircraft_ini, deviceFixgw(p).aircraft_ini);
  assert.equal(fx.aircraft_ini_sha256, sha(fx.aircraft_ini));
  assert.match(fx.aircraft_ini, /\nCHT14\.highWarn = 435\n/);
});

test("ETag: moves with a value, not with a save that changes none", async () => {
  const a = (await serveDeviceConfig(PANEL, 3, prof({ "IAS.Vne": 160 }))).etag;
  const b = (await serveDeviceConfig(PANEL, 3, prof({ "IAS.Vne": 161 }))).etag;
  const c = (await serveDeviceConfig(PANEL, 3, prof({ "IAS.Vne": 160 }, { engines: 1 }))).etag;
  assert.notEqual(a, '"v3"');
  assert.notEqual(a, b, "a value change must not 304");
  assert.equal(a, c, "a save that changes no delivered value must not re-pull");
});

test("no values: body and ETag exactly as before ACINI1", async () => {
  const s = await serveDeviceConfig(PANEL, 3, prof({}));
  assert.equal(s.body, PANEL);
  assert.equal(s.etag, '"v3"');
});
