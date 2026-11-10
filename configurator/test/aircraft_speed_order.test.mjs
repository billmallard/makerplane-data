// SPDX-License-Identifier: AGPL-3.0-or-later
//
// G81 / AER-5327 (makerplane-data #179): the aircraft form's inline ordering
// check said "Vs must be <= Vno" and looked at neighbouring pairs only, while
// the server refuses Vs = Vno (Vs0 <= Vs < Vno < Vne <= Max) and checks every
// pair across blanks. Ruling (Bill, 2026-11-09): the server's strict chain is
// right. Runs the REAL chain constants and chainErrors() out of aircraft.html
// (brace-matched, loaded leniently so a page without them fails on
// assertions) against the REAL src/aircraft_profile.ts the PUT calls.
// The form-level check -- validate() puts these in "Fix before saving" and
// disables Save -- is in test/aircraft_vgnd.test.mjs, which owns the page
// harness.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractConst, extractFunction } from "./support/extract.mjs";
import { aircraftProfileErrors } from "../src/aircraft_profile.ts";

const SRC = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "..", "public", "aircraft.html"), "utf8");
const pick = (f, name) => { try { return f(SRC, name); } catch { return ""; } };
const CONSTS = ["AIRSPEED_CHAIN", "VFE_CHAIN", "BAND_CHAIN"];
const page = new Function(CONSTS.map(n => pick(extractConst, n)).join("\n") + "\n"
  + pick(extractFunction, "chainErrors") + "\n"
  + "return { chainErrors: typeof chainErrors === 'function' ? chainErrors : undefined,"
  + CONSTS.map(n => `${n}: typeof ${n} === "undefined" ? undefined : ${n}`).join(", ") + " };")();

// The page's ordering errors for a values map, the way validate() collects them.
function pageErrors(V) {
  assert.equal(typeof page.chainErrors, "function", "aircraft.html has chainErrors()");
  const errs = [...page.chainErrors(V, "IAS", page.AIRSPEED_CHAIN),
                ...page.chainErrors(V, "IAS", page.VFE_CHAIN)];
  for (const i of new Set(Object.keys(V).map(k => k.split(".")[0])))
    if (i !== "IAS") errs.push(...page.chainErrors(V, i, page.BAND_CHAIN));
  return errs;
}

const C170 = { "IAS.Vs0": 45, "IAS.Vs": 50, "IAS.Vfe": 87, "IAS.Vno": 122, "IAS.Vne": 139,
               "IAS.Max": 156 };
const CASES = [
  ["the c170b airspeeds", C170, []],
  ["Vs = Vno", { ...C170, "IAS.Vs": 100, "IAS.Vno": 100, "IAS.Vfe": 90 },
    ["IAS: Vs (100) must be < Vno (100)"]],
  ["Vs = Vno = Vne", { "IAS.Vs": 100, "IAS.Vno": 100, "IAS.Vne": 100 },
    ["IAS: Vs (100) must be < Vno (100)", "IAS: Vs (100) must be < Vne (100)",
     "IAS: Vno (100) must be < Vne (100)"]],
  ["Vno = Vne", { ...C170, "IAS.Vne": 122 }, ["IAS: Vno (122) must be < Vne (122)"]],
  ["Vs0 = Vs and Vne = Max are allowed", { ...C170, "IAS.Vs0": 50, "IAS.Max": 139 }, []],
  ["Vs = Vne across a blank Vno", { "IAS.Vs": 120, "IAS.Vne": 120 },
    ["IAS: Vs (120) must be < Vne (120)"]],
  ["Vfe above Vno", { ...C170, "IAS.Vfe": 130 }, ["IAS: Vfe (130) must be <= Vno (122)"]],
  ["Vfe = Vno is allowed", { ...C170, "IAS.Vfe": 122 }, []],
  ["lowWarn above highAlarm across blank highWarn",
    { "OILP1.Min": 0, "OILP1.lowWarn": 120, "OILP1.highAlarm": 115, "OILP1.Max": 130 },
    ["OILP1: lowWarn (120) must be <= highAlarm (115)"]],
];

for (const [name, values, want] of CASES) {
  test(`speed order: ${name}`, () => {
    const server = aircraftProfileErrors({ values });
    assert.deepEqual(server, want, "the server's rule");
    assert.deepEqual(pageErrors(values), server, "the page's copy says exactly what the server says");
  });
}

test("the page marks both fields of every failing pair", () => {
  const marked = [];
  page.chainErrors({ "IAS.Vs": 100, "IAS.Vno": 100 }, "IAS", page.AIRSPEED_CHAIN,
                   k => marked.push(k));
  assert.deepEqual(marked, ["IAS.Vs", "IAS.Vno"]);
});
