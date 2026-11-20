// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-6145 (parent AER-6137, fix-gateway #200): the low CHT/EGT alarms wait
// TACHe.warmup seconds after engine start. fix-gateway describes the slot in
// aircraft_params_schema.json `aux_meta`, with `units: "s"` overriding the
// key's RPM. The Engine card must render it per engine in seconds, keep it to
// fix-gateway's bounds and to whole numbers (TACHe is an int key: fix-gateway
// refuses 12.5 and stops at startup), and carry a set value -- and nothing
// for a blank one -- into the aircraft.ini config-pull serves.
//
// The schema fixture is fix-gateway's real export, not a hand-written one:
//   git clone -b aer-6137/cht-egt-warmup https://github.com/billmallard/fix-gateway
//   (bb96d94) && cd fix-gateway/src && python -m fixgw.tools.export_param_schema \
//     -o aircraft_params_schema_warmup.json
//
// Runs the REAL render()/validate()/buildIni()/importIni() out of
// aircraft.html against a minimal DOM stub (same harness as
// aircraft_vgnd.test.mjs).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildAircraftIni } from "../public/aircraft_ini.js";
import { deviceFixgw } from "../src/device_prefs.ts";
import { extractConst, extractFunction } from "./support/extract.mjs";
import { labelFitError } from "../public/fuel_label.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(resolve(HERE, "..", "public", "aircraft.html"), "utf8");
const SCHEMA = JSON.parse(readFileSync(resolve(HERE, "fixtures", "aircraft_params_schema_warmup.json"), "utf8"));
const TACH = SCHEMA.groups.find((g) => g.id === "engine").keys.find((k) => k.key === "TACHe");
const META = TACH.aux_meta.warmup;

// ---- minimal DOM ------------------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.style = {}; this.className = ""; this._text = ""; this._html = "";
    this.listeners = {}; this.id = ""; this.value = "";
    const cls = new Set();
    this.classList = { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
                       toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)) };
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  // The Fuel card appends bare strings (text nodes) as well as elements.
  get textContent() { return this._text + this.children.map((c) => (c instanceof El ? c.textContent : String(c))).join(" "); }
  set innerHTML(v) { this._html = v; this.children = []; }
  get innerHTML() { return this._html; }
  append(...cs) { this.children.push(...cs); }
  appendChild(c) { this.children.push(c); return c; }
  prepend(c) { this.children.unshift(c); }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  fire(ev) { (this.listeners[ev] || []).forEach((fn) => fn()); }
  getContext() { return new Proxy({}, { get: () => () => {}, set: () => true }); }
  *walk() { yield this; for (const c of this.children) if (c instanceof El) yield* c.walk(); }
}
function makeDom() {
  const root = new El("body"), byId = {};
  for (const id of ["engines", "cylinders", "tanks", "speedunit", "barounit", "twinspeeds",
                    "groups", "save", "msg", "proj-label", "registration", "icaoaddr",
                    "ownship-out"]) {
    const e = new El({ groups: "div", registration: "input", icaoaddr: "input",
                       "ownship-out": "p" }[id] || "select"); e.id = id; byId[id] = e; root.append(e);
  }
  const all = () => [...root.walk()];
  return {
    root,
    createElement: (t) => new El(t),
    getElementById: (id) => byId[id] || all().find((e) => e.id === id) || null,
    querySelectorAll: (sel) => (sel === "input.err"
      ? all().filter((e) => e.tagName === "INPUT" && e.classList.contains("err")) : []),
    querySelector: (sel) => {
      const m = /^input\[data-fk="([^"]+)"\]$/.exec(sel);
      return m ? all().find((e) => e.tagName === "INPUT" && e.dataset.fk === m[1]) || null : null;
    },
  };
}

const CONSTS = ["SPEED_ORDER", "TWIN_ONLY", "DEEMPH", "KT", "SPEED_UNIT_LABEL", "BARO_UNITS",
  "N_LETTERS", "FUEL_ROLES", "TANK_CEILING", "TANK_LABEL_MAX",
  "AIRSPEED_CHAIN", "VFE_CHAIN", "BAND_CHAIN"];
const FUNCS = ["normalizeProfile", "normalizeRegistration", "nNumberAddress", "parseIcaoAddress",
  "ownshipStatus", "instances", "isSpeed", "toNative", "toDisplay", "fmt",
  "unitLabel", "auxSettings", "setEntry", "settingField", "fillCounts", "chainErrors", "render",
  "validate",
  "drawArc", "buildIni", "importIni", "newTankId", "migrateFuel",
  "adoptTankValues", "syncTankValues", "addTank", "removeTank", "setTankSlot", "fuelCheck",
  "fuelEditor"];
// Helpers this change introduced are skipped when absent, so the same test
// run against the pre-change page fails on behaviour, not on extraction.
const NEW = new Set(["SPEED_UNIT_LABEL", "unitLabel", "auxSettings", "setEntry", "settingField",
  "N_LETTERS", "normalizeRegistration", "nNumberAddress", "parseIcaoAddress", "ownshipStatus",
  "FUEL_ROLES", "TANK_CEILING", "TANK_LABEL_MAX", "newTankId", "migrateFuel", "adoptTankValues",
  "syncTankValues", "addTank", "removeTank", "setTankSlot", "fuelCheck", "fuelEditor",
  "AIRSPEED_CHAIN", "VFE_CHAIN", "BAND_CHAIN", "chainErrors"]);
const pick = (names, get) => names.map((n) => {
  try { return get(SRC, n); } catch (e) { if (NEW.has(n)) return ""; throw e; }
}).join("\n\n");
const BODY = pick(CONSTS, extractConst) + "\n" + pick(FUNCS, extractFunction);

function page(meta = {}, values = {}, schema = SCHEMA) {
  const document = makeDom();
  const $ = (id) => document.getElementById(id);
  const api = new Function("document", "$", "schema", "PROJECT", "labelFitError", "buildAircraftIni",
    `let profile = { schema_version: 1, meta: { engines:1, cylinders:4, tanks:2, speed_unit:"kt",
       baro_unit:"inHg", twin_speeds:false }, values: {} };
     let savedVersion = null;
     ${BODY}
     return { render, validate, buildIni, importIni, normalizeProfile,
              get profile() { return profile; }, set profile(p) { profile = p; } };`,
  )(document, $, schema, "1", labelFitError, buildAircraftIni);
  Object.assign(api.profile.meta, meta);
  Object.assign(api.profile.values, values);
  // The real schema carries the Fuel card, which needs the normalized meta.fuel.
  api.profile = api.normalizeProfile(api.profile);
  api.render();
  const input = (fk) => document.querySelector(`input[data-fk="${fk}"]`);
  const type = (fk, v) => { const i = input(fk); i.value = String(v); i.fire("input"); };
  return { api, document, input, type, $ };
}

const nodes = (document) => [...document.root.walk()];
const fieldOf = (document, inp) => nodes(document).find((e) => e.children.includes(inp));
const engineCard = (document) => nodes(document).find((e) => e.className === "card"
  && e.innerHTML.startsWith(`<h2>${SCHEMA.groups.find((g) => g.id === "engine").label}</h2>`));
const iniLines = (text) => text.split("\n").filter((l) => /warmup/.test(l));

test("the Engine card renders TACH1.warmup in seconds, 0-600, default 30", () => {
  const { document, input } = page({}, {}, SCHEMA);
  const inp = input("TACH1.warmup");
  assert.ok(inp, "an input for TACH1.warmup is rendered");
  assert.ok([...engineCard(document).walk()].includes(inp), "on the Engine card");
  const text = fieldOf(document, inp).textContent;
  assert.match(text, /^Low CHT\/EGT alarms: warm-up after engine start \(s\)/);
  assert.ok(!/RPM/.test(text), `labelled in seconds, not the key's RPM: ${text}`);
  assert.ok(text.includes(META.help), "help text shown verbatim from aux_meta");
  assert.match(text, /Range 0-600 s\. Blank uses the shipped default \(30 s\)\./);
  assert.equal(inp.placeholder, "30 (default)");
  assert.equal(inp.min, "0"); assert.equal(inp.max, "600");
  assert.equal(inp.step, "1", "TACHe is an int key: whole seconds");
  // Once, as a setting -- not also as a bare "warmup" cell in the band grid.
  assert.equal(nodes(document).filter((e) => e.tagName === "INPUT" && e.dataset.fk === "TACH1.warmup").length, 1);
});

test("a twin gets one warm-up field per engine", () => {
  const { input } = page({ engines: 2 }, {}, SCHEMA);
  assert.ok(input("TACH1.warmup")); assert.ok(input("TACH2.warmup"));
  assert.equal(input("TACH3.warmup"), null);
});

test("outside 0-600 s or a fraction of a second is a hard error that blocks Save", () => {
  const { api, type, input, $ } = page({}, {}, SCHEMA);
  type("TACH1.warmup", 601);
  let r = api.validate();
  assert.deepEqual(r.hard, ["Low CHT/EGT alarms: warm-up after engine start: must be 0-600 s"]);
  assert.equal($("save").disabled, true);
  assert.ok(input("TACH1.warmup").classList.contains("err"));
  type("TACH1.warmup", -1);
  assert.equal(api.validate().hard.length, 1);
  type("TACH1.warmup", 12.5);
  r = api.validate();
  assert.deepEqual(r.hard, ["Low CHT/EGT alarms: warm-up after engine start: must be a whole number of s"]);
  assert.equal($("save").disabled, true);
  for (const ok of [0, 45, 600]) {
    type("TACH1.warmup", ok);
    assert.deepEqual(api.validate().hard, [], `${ok} s is accepted`);
  }
  assert.equal($("save").disabled, false);
});

test("blank compiles nothing: not in the download, not in what config-pull serves", () => {
  const { api, type } = page({}, { "TACH1.Max": 2700 }, SCHEMA);
  type("TACH1.warmup", 45); type("TACH1.warmup", "");
  assert.equal("TACH1.warmup" in api.profile.values, false, "blank leaves the value unset");
  assert.deepEqual(iniLines(api.buildIni()), []);
  const served = deviceFixgw(JSON.parse(JSON.stringify(api.profile)));
  assert.match(served.aircraft_ini, /^TACH1\.Max = 2700$/m, "the rest of the profile still compiles");
  assert.deepEqual(iniLines(served.aircraft_ini), []);
});

test("a set value compiles to TACH1.warmup = <n>, in the download and in config-pull", () => {
  const { api, type } = page({ engines: 2 }, {}, SCHEMA);
  type("TACH1.warmup", 45); type("TACH2.warmup", 0);
  assert.deepEqual(iniLines(api.buildIni()), ["TACH1.warmup = 45", "TACH2.warmup = 0"]);
  // Save -> D1 -> GET /device/config: the stored JSON through the server's compiler.
  const stored = JSON.parse(JSON.stringify(api.normalizeProfile(api.profile)));
  assert.deepEqual(iniLines(deviceFixgw(stored).aircraft_ini), ["TACH1.warmup = 45", "TACH2.warmup = 0"]);
  assert.deepEqual(iniLines(buildAircraftIni(stored)), ["TACH1.warmup = 45", "TACH2.warmup = 0"]);
});

test("TACH1.warmup round-trips aircraft.ini import -> profile -> aircraft.ini", () => {
  const a = page({}, {}, SCHEMA);
  a.api.importIni("TACH1.warmup = 60\n");
  assert.equal(a.api.profile.values["TACH1.warmup"], 60);
  assert.equal(a.input("TACH1.warmup").value, "60", "the imported value shows in the form");
  assert.deepEqual(iniLines(a.api.buildIni()), ["TACH1.warmup = 60"]);
});

test("a schema from a fix-gateway without the slot renders no warm-up field", () => {
  const old = JSON.parse(JSON.stringify(SCHEMA));
  const k = old.groups.find((g) => g.id === "engine").keys.find((x) => x.key === "TACHe");
  k.aux = k.aux.filter((a) => a !== "warmup"); delete k.aux_meta.warmup;
  const { api, input } = page({}, {}, old);
  assert.equal(input("TACH1.warmup"), null);
  assert.deepEqual(api.validate().hard, []);
});
