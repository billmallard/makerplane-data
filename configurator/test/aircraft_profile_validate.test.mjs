// SPDX-License-Identifier: AGPL-3.0-or-later
//
// CERT11 / AER-5060 (AER-571 item 1): the server refuses an aircraft profile
// that breaks the ordering and unit rules of docs/canfix_configurator.md
// section 6. Before this the browser only warned and the server checked
// "is an object, under 256 KB", so a profile with Vne below Vno saved and
// compiled into a downloadable aircraft.ini. Imports the REAL
// src/aircraft_profile.ts (Node strips the types), the function
// PUT /api/projects/:id/aircraft calls. One test per rule.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkAircraftProfilePut } from "../src/aircraft_profile.ts";

// A profile every rule accepts: the c170b template airspeeds plus the O-320
// engine bands (OILP highAlarm == Max, so equality at the edges is legal).
const GOOD = {
  "IAS.Vs0": 45, "IAS.Vs": 50, "IAS.Vfe": 87, "IAS.Vno": 122, "IAS.Vne": 139, "IAS.Max": 156,
  "OILP1.Min": 0, "OILP1.Max": 115, "OILP1.lowAlarm": 25, "OILP1.lowWarn": 55,
  "OILP1.highWarn": 95, "OILP1.highAlarm": 115,
  "OAT.Min": -60, "OAT.Max": 60, "OAT.lowAlarm": -30, "OAT.lowWarn": -25,
  "OAT.highWarn": 28, "OAT.highAlarm": 35,
  "FUELQ1.Max": 21, "FUELQ1.lowWarn": 5, "FUELQ1.lowAlarm": 2.5,
};

const put = (values, meta = {}) => checkAircraftProfilePut({
  profile: { schema_version: 1,
             meta: { engines: 1, cylinders: 4, tanks: 2, speed_unit: "kt",
                     baro_unit: "inHg", ...meta },
             values },
});
const withValues = (patch) => put({ ...GOOD, ...patch });

function rejected(r, re) {
  assert.equal(r.ok, false, "expected the profile to be refused");
  assert.equal(r.status, 422);
  assert.ok(r.errors.some(e => re.test(e)), `no error matching ${re}: ${JSON.stringify(r.errors)}`);
}

test("a consistent profile saves, and blank (absent) values are allowed", () => {
  assert.equal(put(GOOD).ok, true);
  assert.equal(put({}).ok, true);
  assert.equal(put({ "IAS.Vs": 50, "IAS.Vne": 139 }).ok, true);
});

test("every starter template in aircraft.html saves, alone and with c170b", () => {
  // The REAL TEMPLATES object, brace-matched out of the page, so a template
  // the server would now refuse fails here rather than on a builder's Save.
  const src = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
    "..", "public", "aircraft.html"), "utf8");
  const start = src.indexOf("{", src.indexOf("const TEMPLATES = {"));
  let end = start;
  for (let depth = 0; end < src.length; end++) {
    if (src[end] === "{") depth++;
    else if (src[end] === "}" && --depth === 0) break;
  }
  const TEMPLATES = new Function(`return ${src.slice(start, end + 1)};`)();
  assert.ok(Object.keys(TEMPLATES).length >= 4);
  for (const [id, t] of Object.entries(TEMPLATES)) {
    const alone = put(t.values);
    assert.equal(alone.ok, true, `${id}: ${JSON.stringify(alone.errors)}`);
    const merged = put({ ...TEMPLATES.c170b.values, ...t.values });
    assert.equal(merged.ok, true, `c170b+${id}: ${JSON.stringify(merged.errors)}`);
  }
});

test("the existing shape and size checks still apply", () => {
  assert.deepEqual(checkAircraftProfilePut({}),
                   { ok: false, status: 400, error: "profile object required" });
  const big = checkAircraftProfilePut({ profile: { pad: "x".repeat(256 * 1024) } });
  assert.equal(big.status, 413);
});

test("Vs0 <= Vs", () => {
  rejected(withValues({ "IAS.Vs0": 51 }), /Vs0.*Vs\b/);
  assert.equal(withValues({ "IAS.Vs0": 50 }).ok, true);
});

test("Vs < Vno (strict)", () => {
  rejected(withValues({ "IAS.Vs": 122, "IAS.Vs0": 45 }), /Vs.*Vno/);
});

test("Vno < Vne (strict)", () => {
  rejected(withValues({ "IAS.Vne": 122 }), /Vno.*Vne/);
  rejected(withValues({ "IAS.Vne": 100 }), /Vno.*Vne/);
});

test("Vne <= Max", () => {
  rejected(withValues({ "IAS.Max": 138 }), /Vne.*Max/);
  assert.equal(withValues({ "IAS.Max": 139 }).ok, true);
});

test("the airspeed order holds across a blank in the middle", () => {
  // Vno and Vne left unset: Vs at Max is still out of order.
  rejected(put({ "IAS.Vs": 160, "IAS.Max": 156 }), /Vs.*Max/);
});

test("Vfe within [Vs0, Vno]", () => {
  rejected(withValues({ "IAS.Vfe": 40 }), /Vfe/);
  rejected(withValues({ "IAS.Vfe": 130 }), /Vfe/);
  assert.equal(withValues({ "IAS.Vfe": 122 }).ok, true);
});

test("Min <= lowAlarm (alarms inside Min/Max)", () => {
  rejected(withValues({ "OILP1.Min": 30 }), /OILP1.*Min.*lowAlarm/);
});

test("lowAlarm <= lowWarn (warns inside alarms)", () => {
  rejected(withValues({ "OILP1.lowAlarm": 60 }), /OILP1.*lowAlarm.*lowWarn/);
  rejected(withValues({ "FUELQ1.lowAlarm": 6 }), /FUELQ1.*lowAlarm.*lowWarn/);
});

test("lowWarn <= highWarn", () => {
  rejected(withValues({ "OAT.lowWarn": 30, "OAT.lowAlarm": -30 }), /OAT.*lowWarn.*highWarn/);
});

test("highWarn <= highAlarm (warns inside alarms)", () => {
  rejected(withValues({ "OILP1.highWarn": 116, "OILP1.highAlarm": 110 }), /OILP1.*highWarn.*highAlarm/);
});

test("highAlarm <= Max (alarms inside Min/Max)", () => {
  rejected(withValues({ "OILP1.highAlarm": 120 }), /OILP1.*highAlarm.*Max/);
});

test("the band order holds across blanks", () => {
  // No alarms or warns between them: Min above Max is still caught.
  rejected(put({ "VOLT.Min": 16, "VOLT.Max": 3 }), /VOLT.*Min.*Max/);
});

test("units: the airspeed entry unit is kt, mph or kph", () => {
  for (const u of ["kt", "mph", "kph"]) assert.equal(put(GOOD, { speed_unit: u }).ok, true);
  rejected(put(GOOD, { speed_unit: "m/s" }), /speed_unit/);
});

test("units: the altimeter-setting unit is inHg or hPa", () => {
  assert.equal(put(GOOD, { baro_unit: "hPa" }).ok, true);
  rejected(put(GOOD, { baro_unit: "mmHg" }), /baro_unit/);
});

test("every stored value is a finite number in native units", () => {
  rejected(withValues({ "IAS.Vne": "139" }), /IAS\.Vne.*number/);
  rejected(withValues({ "OAT.Max": true }), /OAT\.Max.*number/);
  rejected(checkAircraftProfilePut({ profile: { values: [139] } }), /values.*object/);
});
