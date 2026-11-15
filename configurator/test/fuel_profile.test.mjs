// SPDX-License-Identifier: AGPL-3.0-or-later
//
// FUEL1a / AER-5271 (docs/fuel_provider_model.md section 2): meta.fuel
// schema 2. Runs the REAL fuel functions out of aircraft.html (brace-matched,
// not reimplemented) and the REAL src/fuel_profile.ts + aircraft_profile.ts
// the PUT calls.
//
// The page functions are loaded leniently, so this file also runs against a
// page that predates schema 2: there the first test fails on its assertions
// (tank 3 renumbers), which is the defect this row fixes.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractConst, extractFunction } from "./support/extract.mjs";
import { labelFitError } from "../public/fuel_label.js";
import { fuelCheck as serverFuelCheck, TANK_CEILING, TANK_LABEL_MAX, FUEL_ROLES }
  from "../src/fuel_profile.ts";
import { checkAircraftProfilePut } from "../src/aircraft_profile.ts";

const SRC = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "..", "public", "aircraft.html"), "utf8");
const pick = (f, name) => { try { return f(SRC, name); } catch { return ""; } };
const CONSTS = ["BARO_UNITS", "FUEL_ROLES", "TANK_CEILING", "TANK_LABEL_MAX"];
const FNS = ["normalizeProfile", "newTankId", "migrateFuel", "adoptTankValues",
  "syncTankValues", "addTank", "removeTank", "setTankSlot", "fuelCheck",
  "cylinderExpand", "buildIni"];
// buildIni reads the page's globals: the project label, the saved version,
// today's date and the profile. Stub all four so two compiles can be compared.
const page = new Function("$", "PROJECT", "savedVersion", "Date", "labelFitError",
  CONSTS.map(n => pick(extractConst, n)).join("\n") + "\n"
  + FNS.map(n => pick(extractFunction, n)).join("\n") + "\n"
  + "let profile;\n"
  + "return { " + FNS.map(n => `${n}: typeof ${n} === "function" ? ${n} : undefined`).join(", ")
  + ", ini(p) { profile = p; return buildIni(); },"
  + CONSTS.map(n => `${n}: typeof ${n} === "undefined" ? undefined : ${n}`).join(", ") + " };")(
  () => ({ textContent: "Test Project" }), "p1", 7,
  class { toISOString() { return "2026-11-09T00:00:00.000Z"; } }, labelFitError);

const clone = o => JSON.parse(JSON.stringify(o));
// The save/load path: PUT body JSON -> D1 TEXT -> GET JSON.parse -> normalize.
const load = p => page.normalizeProfile(clone(p));

// Three tanks saved by the schema-1 page: tank 1 (tip) and tank 2 (aux) feed
// tank 3, the header, which feeds the engine.
const THREE = { schema_version: 1,
  meta: { engines: 1, cylinders: 4, tanks: 3, speed_unit: "kt", baro_unit: "inHg",
          fuel: [{ role: "tip", feeds: "T3" }, { role: "aux", feeds: "T3" },
                 { role: "header", feeds: "E1" }] },
  values: { "FUELQ1.Max": 10, "FUELQ2.Max": 15, "FUELQ3.Max": 5, "FUELQ3.lowWarn": 1 } };

// Schema-neutral views, so the first test can say the same thing about a
// positional array (entry t-1 is tank t) and about schema 2.
const tankList = p => Array.isArray(p.meta.fuel) ? p.meta.fuel : p.meta.fuel.tanks;
const slotOf = (p, t) => t.slot ?? tankList(p).indexOf(t) + 1;
const feedsTank = (p, a, b) => Array.isArray(a.feeds)
  ? a.feeds.some(e => e.to === "tank" && e.id === b.id) : a.feeds === "T" + slotOf(p, b);
const feedsEngine = (a, n) => Array.isArray(a.feeds)
  ? a.feeds.some(e => e.to === "engine" && e.n === n) : a.feeds === "E" + n;

test("removing tank 2 of 3 keeps tank 3's slot, edges and values", () => {
  const p = load(THREE);
  const [t1, t2, t3] = tankList(p);
  if (page.removeTank) page.removeTank(p.meta, p.values, t2.id);
  else { tankList(p).splice(1, 1); p.meta.tanks = 2; }   // positional: all the page can do
  assert.ok(tankList(p).includes(t3), "tank 3 is still there");
  assert.equal(slotOf(p, t3), 3, "tank 3 still reads FUELQ3");
  assert.ok(feedsTank(p, t1, t3), "tank 1 still feeds tank 3, not whatever took its place");
  assert.ok(feedsEngine(t3, 1), "tank 3 still feeds the engine");
  assert.equal(p.values[`FUELQ${slotOf(p, t3)}.Max`], 5, "tank 3's capacity band is its own");
  assert.equal(p.values["FUELQ2.Max"], undefined, "the removed tank's values went with it");
  assert.deepEqual(serverFuelCheck(p.meta, p.values).errors, []);
});

test("schema 1 migrates losslessly to schema 2, with neutral labels", () => {
  const p = page.normalizeProfile(clone(THREE));
  assert.equal(p.meta.fuel.schema, 2);
  const [a, b, c] = p.meta.fuel.tanks;
  assert.deepEqual(p.meta.fuel.tanks.map(t => [t.label, t.slot, t.role, t.capacity, t.unusable]),
    [["Tank 1", 1, "tip", 10, 0], ["Tank 2", 2, "aux", 15, 0], ["Tank 3", 3, "header", 5, 0]]);
  assert.deepEqual(a.feeds, [{ to: "tank", id: c.id }]);
  assert.deepEqual(b.feeds, [{ to: "tank", id: c.id }]);
  assert.deepEqual(c.feeds, [{ to: "engine", n: 1 }]);
  assert.equal(new Set([a.id, b.id, c.id]).size, 3, "ids are distinct");
  assert.ok(p.meta.fuel.tanks.every(t => /^tk_[0-9a-f]{8}$/.test(t.id)));
  assert.equal(p.meta.tanks, 3);
  assert.deepEqual(p.values, THREE.values, "loading never rewrites values");
  // A second load is a no-op: ids are stable across saves.
  assert.deepEqual(page.normalizeProfile(clone(p)), p);
});

test("a schema-1 profile with no fuel array migrates as the old page showed it", () => {
  // The old page padded meta.fuel to the tank count with main / engine 1, and
  // showed an edge beyond the counts as "feeds: nothing".
  const p = load({ meta: { engines: 1, tanks: 2, fuel: [{ role: "main", feeds: "T7" }] }, values: {} });
  assert.deepEqual(p.meta.fuel.tanks.map(t => [t.label, t.role, t.feeds, t.capacity]),
    [["Tank 1", "main", [], null], ["Tank 2", "main", [{ to: "engine", n: 1 }], null]]);
  assert.deepEqual(load({ meta: {} }).meta.fuel, { schema: 2, tanks: [] });
});

test("a migrated profile compiles to a byte-identical aircraft.ini", () => {
  // c170b-style values, cylinder bands, and FUELQ4 left behind when the old
  // count was lowered from 4 to 3 (it still compiled; it still must).
  const old = clone(THREE);
  old.meta.cylinders = 6;
  Object.assign(old.values, { "IAS.Vs0": 45, "IAS.Vne": 139, "CHT1.highWarn": 232,
    "FUELQ1.lowWarn": 2, "FUELQ1.lowAlarm": 1, "FUELQ4.Max": 9, "FUELQ2.Min": 0 });
  const before = page.ini(clone(old));
  const migrated = page.normalizeProfile(clone(old));
  assert.equal(migrated.meta.fuel.schema, 2, "the comparison is across a real migration");
  assert.equal(page.ini(migrated), before);
  assert.match(before, /^FUELQ3\.Max = 5$/m);
  // It saves, too: the leftover FUELQ4 is a warning, not a refusal.
  const r = checkAircraftProfilePut({ profile: migrated });
  assert.equal(r.ok, true, JSON.stringify(r.errors));
  assert.ok(serverFuelCheck(migrated.meta, migrated.values).warnings
    .some(w => /^FUELQ4 values belong to no tank/.test(w)));
});

test("a FUELQn.Max of 0 migrates to no capacity, not an unsavable tank", () => {
  const p = load({ meta: { tanks: 1 }, values: { "FUELQ1.Max": 0 } });
  assert.equal(p.meta.fuel.tanks[0].capacity, null);
  assert.equal(checkAircraftProfilePut({ profile: p }).ok, true);
});

test("the tank writes FUELQn.Min/Max: Max = capacity - unusable", () => {
  const p = load(THREE), t = p.meta.fuel.tanks[0];
  t.capacity = 40; t.unusable = 1.2;
  page.syncTankValues(t, p.values);
  assert.equal(p.values["FUELQ1.Min"], 0);
  assert.equal(p.values["FUELQ1.Max"], 38.8);
  assert.equal(checkAircraftProfilePut({ profile: p }).ok, true);
  t.capacity = null; t.unusable = 0;
  page.syncTankValues(t, p.values);
  assert.equal(p.values["FUELQ1.Max"], undefined);
  assert.equal(p.values["FUELQ1.Min"], undefined);
});

test("a rewire moves the tank's values to its new slot", () => {
  const p = load(THREE), t3 = p.meta.fuel.tanks[2];
  page.setTankSlot(p.meta, p.values, t3.id, 5);
  assert.equal(t3.slot, 5);
  assert.deepEqual([p.values["FUELQ5.Max"], p.values["FUELQ5.lowWarn"], p.values["FUELQ3.Max"]], [5, 1, undefined]);
  page.setTankSlot(p.meta, p.values, t3.id, 1);   // taken: refused
  assert.equal(t3.slot, 5);
  assert.equal(checkAircraftProfilePut({ profile: p }).ok, true);
});

test("add takes the lowest free slot, a neutral label and a fresh id, up to the ceiling", () => {
  const p = load(THREE), t2 = p.meta.fuel.tanks[1];
  page.removeTank(p.meta, p.values, t2.id);
  const n = page.addTank(p.meta, p.values, page.newTankId);
  assert.deepEqual([n.slot, n.label], [2, "Tank 2"]);
  assert.notEqual(n.id, t2.id);
  while (page.addTank(p.meta, p.values, page.newTankId));
  assert.equal(p.meta.fuel.tanks.length, TANK_CEILING);
  assert.equal(p.meta.tanks, TANK_CEILING);
  assert.deepEqual(serverFuelCheck(p.meta, p.values).errors, []);
});

test("no Left, Center or Right in any default, migration or template", () => {
  const LCR = /\b(left|center|centre|right|L MAIN|R MAIN)\b/i;
  const p = load({ meta: { tanks: 8 }, values: {} });
  while (page.addTank(p.meta, p.values, page.newTankId));
  for (const t of p.meta.fuel.tanks) assert.doesNotMatch(t.label, LCR);
  const tmpl = SRC.slice(SRC.indexOf("const TEMPLATES = {"), SRC.indexOf("// ---- key instantiation"));
  assert.doesNotMatch(tmpl, LCR);
  for (const f of ["migrateFuel", "addTank"]) assert.doesNotMatch(extractFunction(SRC, f), LCR);
});

test("the page's limits and roles are the server's", () => {
  assert.deepEqual([page.TANK_CEILING, page.TANK_LABEL_MAX, page.FUEL_ROLES],
                   [TANK_CEILING, TANK_LABEL_MAX, FUEL_ROLES]);
});

// ---- validation: one table, both copies ----------------------------------------
const tk = (id, label, slot, extra = {}) => ({ id, label, role: "main", slot,
  feeds: [{ to: "engine", n: 1 }], capacity: null, unusable: 0, ...extra });
const fuelMeta = (tanks, engines = 1) => ({ engines, fuel: { schema: 2, tanks } });

const CASES = [
  // [name, meta, values, error regex or null, warning regex or null]
  ["valid two tanks", fuelMeta([tk("a", "L MAIN", 1), tk("b", "AFT", 2)]), {}, null, null],
  ["no fuel group", { engines: 1 }, {}, null, null],
  ["legacy positional array", { fuel: [{ role: "main", feeds: "E1" }] }, {}, null, null],
  ["not schema 2", { fuel: { schema: 3, tanks: [] } }, {}, /schema-2 tank list/, null],
  ["duplicate id", fuelMeta([tk("a", "A", 1), tk("a", "B", 2)]), {}, /duplicate id a/, null],
  ["bad id", fuelMeta([tk("a b", "A", 1)]), {}, /id must be/, null],
  ["duplicate slot", fuelMeta([tk("a", "A", 1), tk("b", "B", 1)]), {}, /slot 1 \(FUELQ1\) used/, null],
  ["slot out of range", fuelMeta([tk("a", "A", TANK_CEILING + 1)]), {}, /slot must be 1-8/, null],
  ["slot not an integer", fuelMeta([tk("a", "A", 1.5)]), {}, /slot must be/, null],
  ["empty label", fuelMeta([tk("a", "  ", 1)]), {}, /tank 1: label required/, null],
  ["duplicate label, any case", fuelMeta([tk("a", "Main", 1), tk("b", "MAIN ", 2)]), {}, /label used by another/, null],
  ["label too long", fuelMeta([tk("a", "X".repeat(TANK_LABEL_MAX + 1), 1)]), {}, /at most 12 characters/, null],
  // The stock-bar rule (fuel_label.js, measured off the real pyEfis bar).
  ["label wider than the stock bar", fuelMeta([tk("a", "HEADER", 1)]), {},
    /HEADER: label too wide for the stock tank bar: the device cuts "HEADER" at both edges/, null],
  ["two words wider than the stock bar", fuelMeta([tk("a", "R TIP AUX", 1)]), {},
    /label too wide for the stock tank bar: the device shows only "R TIP"/, null],
  ["the default label fits", fuelMeta([tk("a", "Tank 8", 1)]), {}, null, null],
  ["bad role", fuelMeta([tk("a", "A", 1, { role: "left" })]), {}, /role must be one of/, null],
  ["edge to a missing tank", fuelMeta([tk("a", "A", 1, { feeds: [{ to: "tank", id: "zz" }] })]), {},
    /feeds a tank that does not exist/, null],
  ["edge to a missing engine", fuelMeta([tk("a", "A", 1, { feeds: [{ to: "engine", n: 2 }] })]), {},
    /feeds engine 2, which does not exist/, null],
  ["a tank feeding itself", fuelMeta([tk("a", "A", 1, { feeds: [{ to: "tank", id: "a" }] })]), {},
    /cannot feed itself/, null],
  ["feeds not a list", fuelMeta([tk("a", "A", 1, { feeds: "E1" })]), {}, /feeds must be a list/, null],
  ["unusable equal to capacity", fuelMeta([tk("a", "A", 1, { capacity: 5, unusable: 5 })]),
    { "FUELQ1.Max": 0 }, /unusable must be less than capacity/, null],
  ["capacity 0", fuelMeta([tk("a", "A", 1, { capacity: 0 })]), {}, /capacity must be a number above 0/, null],
  ["unusable without capacity", fuelMeta([tk("a", "A", 1, { unusable: 1 })]), {}, /unusable needs a capacity/, null],
  ["Max not capacity - unusable", fuelMeta([tk("a", "A", 1, { capacity: 40, unusable: 1 })]),
    { "FUELQ1.Max": 40 }, /FUELQ1\.Max must be capacity - unusable \(39\)/, null],
  ["Max is capacity - unusable", fuelMeta([tk("a", "A", 1, { capacity: 40, unusable: 1 })]),
    { "FUELQ1.Max": 39, "FUELQ1.Min": 0 }, null, null],
  ["too many tanks", fuelMeta(Array.from({ length: 9 }, (_, i) => tk("t" + i, "T" + i, (i % 8) + 1))), {},
    /at most 8 fuel tanks/, null],
  ["an engine fed by no tank", fuelMeta([tk("a", "A", 1)], 2), {}, null, /^engine 2 is fed by no tank$/],
  ["a transfer cycle is flagged", fuelMeta([
      tk("a", "A", 1, { feeds: [{ to: "tank", id: "b" }, { to: "engine", n: 1 }] }),
      tk("b", "B", 2, { feeds: [{ to: "tank", id: "a" }] })]), {},
    null, /^tank feeds form a cycle: A -> B -> A$/],
  ["a header that feeds no engine", fuelMeta([tk("h", "HDR", 1, { role: "header", feeds: [] }),
      tk("m", "M", 2)]), {}, null, /HDR: a header tank should feed an engine/],
  ["a tip that feeds no tank", fuelMeta([tk("t", "TIP", 1, { role: "tip" })]), {}, null,
    /TIP: a tip tank should feed another tank/],
  ["values for a slot no tank holds", fuelMeta([tk("a", "A", 1)]), { "FUELQ6.lowWarn": 2 }, null,
    /^FUELQ6 values belong to no tank/],
];

for (const [name, meta, values, errRe, warnRe] of CASES) {
  test(`fuel check: ${name}`, () => {
    const server = serverFuelCheck(clone(meta), clone(values));
    assert.deepEqual(page.fuelCheck(clone(meta), clone(values)), server,
                     "the page's copy says exactly what the server says");
    if (errRe) assert.ok(server.errors.some(e => errRe.test(e)), JSON.stringify(server.errors));
    else assert.deepEqual(server.errors, []);
    if (warnRe) assert.ok(server.warnings.some(w => warnRe.test(w)), JSON.stringify(server.warnings));
    else if (!errRe) assert.deepEqual(server.warnings, []);
    // The PUT refuses exactly the errors, and saves through warnings.
    const r = checkAircraftProfilePut({ profile: { meta: clone(meta), values: clone(values) } });
    assert.equal(r.ok, !errRe, JSON.stringify(r.errors));
    if (errRe) { assert.equal(r.status, 422); assert.ok(r.errors.some(e => errRe.test(e))); }
  });
}
