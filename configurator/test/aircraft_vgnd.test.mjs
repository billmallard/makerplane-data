// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// AER-4636 (parent AER-4619, fix-gateway #117): the engine alarms' stopped-
// engine bypass needs IAS below IAS.Vgnd, a new IAS aux slot fix-gateway
// describes in aircraft_params_schema.json `aux_meta`. The Aircraft
// Parameters form must render it from that metadata (label + help, not
// hard-coded wording), convert it like the V-speeds, reject values outside
// its bounds, warn when it is not below Vs0, and carry it import -> profile
// -> aircraft.ini.
//
// Runs the REAL render()/validate()/buildIni()/importIni() out of
// aircraft.html (brace-matched, see test/support/extract.mjs) against a
// minimal DOM stub -- no jsdom dependency.
//
// Delivery note: aircraft.ini still reaches the aircraft only by manual
// download (Phase C delivery is not built), so the round-trip proven here is
// profile <-> downloaded aircraft.ini, not config-pull.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractConst, extractFunction } from "./support/extract.mjs";

const SRC = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "..", "public", "aircraft.html"), "utf8");

// The speeds group as fix-gateway #117's export_param_schema emits it.
const VGND_META = {
  label: "Engine alarms: on-ground IAS limit", min: 10, max: 100, default: 40,
  help: "Oil pressure, oil temperature and volts alarms are silenced with the engine "
    + "stopped only while IAS is below this. Set it below the slowest IAS the aircraft "
    + "flies at (below the stall speed in landing configuration), never above it, or "
    + "the alarms can go quiet in flight.",
};
const SCHEMA = { schema_version: 1, generated: "2026-11-01", groups: [
  { id: "speeds", label: "Airspeeds", repeat: null, keys: [
    { key: "IAS", label: "Indicated Airspeed", units: "knots",
      aux: ["Min", "Max", "V1", "V2", "Vne", "Vfe", "Vmc", "Va", "Vno", "Vs", "Vs0", "Vx", "Vy", "Vgnd"],
      band_aux: ["Min", "Max"], aux_meta: { Vgnd: VGND_META } }] },
  { id: "electrical", label: "Electrical", repeat: null, keys: [
    { key: "VOLT", label: "Volts", units: "volt", aux: ["Min", "Max", "lowWarn"],
      band_aux: ["Min", "Max", "lowWarn"], aux_meta: {} }] },
]};

// ---- minimal DOM ------------------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.style = {}; this.className = ""; this._text = ""; this._html = "";
    this.listeners = {}; this.id = ""; this.value = "";
    const cls = new Set();
    this.classList = { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) };
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(" "); }
  set innerHTML(v) { this._html = v; this.children = []; }
  get innerHTML() { return this._html; }
  append(...cs) { this.children.push(...cs); }
  appendChild(c) { this.children.push(c); return c; }
  prepend(c) { this.children.unshift(c); }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  fire(ev) { (this.listeners[ev] || []).forEach((fn) => fn()); }
  getContext() { return new Proxy({}, { get: () => () => {}, set: () => true }); }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}
function makeDom() {
  const root = new El("body"), byId = {};
  for (const id of ["engines", "cylinders", "tanks", "speedunit", "barounit", "twinspeeds",
                    "groups", "save", "msg", "proj-label"]) {
    const e = new El(id === "groups" ? "div" : "select"); e.id = id; byId[id] = e; root.append(e);
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

const CONSTS = ["SPEED_ORDER", "TWIN_ONLY", "DEEMPH", "KT", "SPEED_UNIT_LABEL", "BARO_UNITS"];
const FUNCS = ["normalizeProfile", "instances", "isSpeed", "toNative", "toDisplay", "fmt",
  "unitLabel", "auxSettings", "setEntry", "settingField", "fillCounts", "render", "validate",
  "drawArc", "cylinderExpand", "buildIni", "importIni"];
// Helpers this change introduced are skipped when absent, so the same test
// run against the pre-change page fails on behaviour, not on extraction.
const NEW = new Set(["SPEED_UNIT_LABEL", "unitLabel", "auxSettings", "setEntry", "settingField"]);
const pick = (names, get) => names.map((n) => {
  try { return get(SRC, n); } catch (e) { if (NEW.has(n)) return ""; throw e; }
}).join("\n\n");
const BODY = pick(CONSTS, extractConst) + "\n" + pick(FUNCS, extractFunction);

function page(meta = {}, values = {}, schema = SCHEMA) {
  const document = makeDom();
  const $ = (id) => document.getElementById(id);
  const api = new Function("document", "$", "schema", "PROJECT",
    `let profile = { schema_version: 1, meta: { engines:1, cylinders:4, tanks:2, speed_unit:"kt",
       baro_unit:"inHg", twin_speeds:false }, values: {} };
     let savedVersion = null;
     ${BODY}
     return { render, validate, buildIni, importIni, normalizeProfile,
              get profile() { return profile; }, set profile(p) { profile = p; } };`,
  )(document, $, schema, "1");
  Object.assign(api.profile.meta, meta);
  Object.assign(api.profile.values, values);
  api.render();
  const input = (fk) => document.querySelector(`input[data-fk="${fk}"]`);
  const type = (fk, v) => { const i = input(fk); i.value = String(v); i.fire("input"); };
  return { api, document, input, type, $ };
}

test("the Airspeeds card renders Vgnd with the label and help from aux_meta", () => {
  const { document, input } = page();
  const inp = input("IAS.Vgnd");
  assert.ok(inp, "an input for IAS.Vgnd is rendered");
  const field = [...document.root.walk()].find((e) => e.children.includes(inp));
  const text = field.textContent;
  assert.match(text, /Engine alarms: on-ground IAS limit \(kt\)/);
  assert.ok(text.includes(VGND_META.help), "help text shown verbatim from aux_meta");
  assert.match(text, /Range 10-100 kt\. Blank uses the shipped default \(40 kt\)\./);
  assert.equal(inp.placeholder, "40 (default)");
  assert.equal(inp.min, "10"); assert.equal(inp.max, "100");
  // Rendered once, as a setting -- not also as a bare "Vgnd" grid cell.
  const cells = [...document.root.walk()].filter((e) => e.tagName === "INPUT" && e.dataset.fk === "IAS.Vgnd");
  assert.equal(cells.length, 1);
});

test("a key without aux_meta renders no setting field", () => {
  const { document } = page();
  const settings = [...document.root.walk()].filter((e) => e.className === "field setting");
  assert.deepEqual(settings.map((s) => s.children[1].dataset.fk), ["IAS.Vgnd"]);
});

test("the live schema before fix-gateway #117 (no Vgnd, no aux_meta) renders as before", () => {
  const old = JSON.parse(JSON.stringify(SCHEMA));
  for (const g of old.groups) for (const k of g.keys) {
    k.aux = k.aux.filter((a) => a !== "Vgnd"); delete k.aux_meta; }
  const { api, input } = page({}, { "IAS.Vs0": 35 }, old);
  assert.equal(input("IAS.Vgnd"), null);
  assert.deepEqual(api.validate(), { errs: [], hard: [] }, "no Vgnd warning without the setting");
});

test("an mph entry stores knots, and the mph bounds are the knot bounds converted", () => {
  const { api, input, type } = page({ speed_unit: "mph" });
  const inp = input("IAS.Vgnd");
  assert.equal(inp.min, "11.51"); assert.equal(inp.max, "115.08");
  assert.equal(inp.placeholder, "46.03 (default)");
  type("IAS.Vgnd", 46.03);
  assert.ok(Math.abs(api.profile.values["IAS.Vgnd"] - 40) < 0.01, `stored ${api.profile.values["IAS.Vgnd"]} kt`);
  // The converted upper bound itself is in range, not rejected by rounding.
  type("IAS.Vgnd", 115.08);
  assert.deepEqual(api.validate().hard, []);
});

test("out-of-range values are hard errors that disable Save; in range clears it", () => {
  const { api, type, input, $ } = page();
  type("IAS.Vgnd", 120);
  let r = api.validate();
  assert.equal(r.hard.length, 1); assert.match(r.hard[0], /must be 10-100 kt/);
  assert.equal($("save").disabled, true);
  assert.ok(input("IAS.Vgnd").classList.contains("err"));
  type("IAS.Vgnd", 5);
  assert.equal(api.validate().hard.length, 1);
  type("IAS.Vgnd", 35);
  r = api.validate();
  assert.deepEqual(r.hard, []); assert.equal($("save").disabled, false);
  // mph: 9 mph = 7.8 kt, below the 10 kt floor.
  const p = page({ speed_unit: "mph" });
  p.type("IAS.Vgnd", 9);
  assert.match(p.api.validate().hard[0], /must be 11.51-115.08 mph/);
});

test("Vgnd at or above Vs0 warns but still saves; blank checks the 40 kt default", () => {
  const { api, type, $ } = page({}, { "IAS.Vs0": 45 });
  assert.deepEqual(api.validate().errs, [], "default 40 kt below Vs0 45: no warning");
  type("IAS.Vgnd", 45);
  let r = api.validate();
  assert.equal(r.hard.length, 0);
  assert.ok(r.errs.some((e) => /on-ground IAS limit should be below Vs0/.test(e)), r.errs.join("|"));
  assert.equal($("save").disabled, false, "a warning does not block save");
  type("IAS.Vgnd", "");
  type("IAS.Vs0", 38);
  r = api.validate();
  assert.ok(r.errs.some((e) => /default on-ground IAS limit \(40 kt\) should be below Vs0/.test(e)),
    r.errs.join("|"));
});

test("blank Vgnd omits the line from aircraft.ini", () => {
  const { api } = page({}, { "IAS.Vs0": 45 });
  assert.ok(!/Vgnd/.test(api.buildIni()));
});

test("IAS.Vgnd round-trips import -> profile (save/load) -> aircraft.ini -> import", () => {
  const a = page();
  a.api.importIni("IAS.Vs0 = 45\nIAS.Vgnd = 35\n");
  assert.equal(a.api.profile.values["IAS.Vgnd"], 35);
  assert.equal(a.input("IAS.Vgnd").value, "35", "the imported value shows in the form");
  // Save -> D1 -> load is a JSON round trip through normalizeProfile.
  a.api.profile = a.api.normalizeProfile(JSON.parse(JSON.stringify(a.api.profile)));
  const ini = a.api.buildIni();
  assert.match(ini, /^IAS\.Vgnd = 35$/m);
  const b = page();
  b.api.importIni(ini);
  assert.equal(b.api.profile.values["IAS.Vgnd"], 35);
});
