// SPDX-License-Identifier: AGPL-3.0-or-later
//
// AER-5499 (FUEL1 F3): a gauge bound to a tank on the aircraft profile is
// served with that tank's slot key and label, a gauge whose tank is gone is
// served unbound and keyless, and the ETag moves whenever a resolved binding
// does. Imports the REAL public/aircraft_refs.js and src/device_prefs.ts --
// the modules GET /device/config, the download and the editor preview call.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import yaml from "js-yaml";
import {
  AIRCRAFT_REFS, itemsBySource, refItems, resolveDoc, resolveOptions, retiredLabels,
} from "../public/aircraft_refs.js";
import { resolveAircraftRefs } from "../src/aircraft_refs.ts";
import { serveDeviceConfig } from "../src/device_prefs.ts";
import { editorSource, extractFunction } from "./support/extract.mjs";

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/aircraft_refs_pyefis.json", import.meta.url), "utf8"));

const tank = (id, label, slot, extra = {}) =>
  ({ id, label, role: "main", slot, feeds: [], capacity: 20, unusable: 0, ...extra });
const profile = (tanks, meta = {}) =>
  JSON.stringify({ schema_version: 1, meta: { ...meta, fuel: { schema: 2, tanks } }, values: {} });

const THREE = [tank("tk_lm", "L MAIN", 1), tank("tk_rm", "R MAIN", 2), tank("tk_lt", "L TIP", 3)];

// What the editor saves: js-yaml dump of the native document.
const panel = (instruments) => yaml.dump({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: "EMS" },
  screens: { EMS: { module: "pyefis.screens.screenbuilder", title: "EMS",
    layout: { rows: 110, columns: 200 }, instruments } },
}, { lineWidth: 100, noRefs: true });
const bar = (options, type = "vertical_bar_gauge") =>
  ({ type, row: 0, column: 0, span: { rows: 20, columns: 10 }, options });

const served = (text) => yaml.load(text).screens.EMS.instruments;

// --- the reference table is pyEfis's -------------------------------------

test("AIRCRAFT_REFS matches every aircraft_ref field the pyEfis schema declares", () => {
  const fromSchema = {};
  for (const f of FIXTURE.fields) {
    const r = (fromSchema[f.option] ||= { source: f.source, resolves: f.resolves, types: [] });
    assert.equal(r.source, f.source, `${f.option}: one source across types`);
    assert.deepEqual(r.resolves, f.resolves, `${f.option}: one resolves across types`);
    assert.equal(f.multi, false, "multi references are not resolved yet");
    r.types.push(f.type);
  }
  for (const r of Object.values(fromSchema)) r.types.sort();
  const ours = JSON.parse(JSON.stringify(AIRCRAFT_REFS));
  for (const r of Object.values(ours)) r.types.sort();
  assert.deepEqual(ours, fromSchema);
});

// --- items --------------------------------------------------------------

test("refItems: schema-2 tanks with their slot keys; nothing from schema 1, junk or no profile", () => {
  assert.deepEqual(refItems(profile(THREE), "aircraft.fuel.tanks").map((i) => [i.id, i.label, i.slot_key]),
    [["tk_lm", "L MAIN", "FUELQ1"], ["tk_rm", "R MAIN", "FUELQ2"], ["tk_lt", "L TIP", "FUELQ3"]]);
  const schema1 = JSON.stringify({ meta: { fuel: [{ role: "main", feeds: "E1" }] } });
  for (const p of [null, "", "{not json", schema1, profile([{ id: "x", label: "", slot: 1 }]),
    profile([{ id: "x", label: "A", slot: 0 }]), profile([{ label: "A", slot: 1 }])])
    assert.deepEqual(refItems(p, "aircraft.fuel.tanks"), [], String(p));
  assert.deepEqual(refItems(profile(THREE), "aircraft.checklists"), []);
});

test("retiredLabels: the newest label of each id the current profile no longer has", () => {
  const v1 = profile([tank("tk_a", "AUX OLD", 4), tank("tk_lm", "L MAIN", 1)]);
  const v2 = profile([tank("tk_a", "AUX", 4), tank("tk_lm", "L MAIN", 1)]);
  const v3 = profile([tank("tk_lm", "L MAIN", 1)]);
  assert.deepEqual(retiredLabels([v3, v2, v1], "aircraft.fuel.tanks", v3), { tk_a: "AUX" });
});

// --- one instrument -----------------------------------------------------

test("a resolved tank writes dbkey FUELQ<slot> and name <label>, and the reference goes", () => {
  const r = resolveOptions("vertical_bar_gauge", { tank: "tk_lt", decimal_places: 1, unbound: true },
    itemsBySource(profile(THREE)));
  assert.deepEqual(r.options, { dbkey: "FUELQ3", name: "L TIP", decimal_places: 1 });
  assert.deepEqual(r.bindings, [{ option: "tank", id: "tk_lt", dbkey: "FUELQ3", name: "L TIP" }]);
});

test("an orphan writes unbound: true with its last label and no dbkey", () => {
  const items = itemsBySource(profile(THREE));
  const r = resolveOptions("arc_gauge", { tank: "tk_gone", dbkey: "FUELQ4", name: "STALE" },
    items, { tk_gone: "R TIP" });
  assert.deepEqual(r.options, { name: "R TIP", unbound: true });
  // No history for the id: the panel's own name stays, still no key.
  assert.deepEqual(resolveOptions("arc_gauge", { tank: "tk_gone", dbkey: "FUELQ4", name: "STALE" }, items).options,
    { name: "STALE", unbound: true });
  assert.deepEqual(resolveOptions("arc_gauge", { tank: "tk_gone" }, items).options, { unbound: true });
});

test("an empty reference is no reference; a tank option on an undeclared type is left alone", () => {
  const items = itemsBySource(profile(THREE));
  assert.deepEqual(resolveOptions("vertical_bar_gauge", { tank: "", dbkey: "FUELQ1" }, items).options,
    { dbkey: "FUELQ1" });
  const opts = { tank: "tk_lm", text: "x" };
  assert.equal(resolveOptions("static_text", opts, items).options, opts);
});

// --- the served YAML ------------------------------------------------------

test("resolveAircraftRefs: the device YAML carries the slot key and label, never the reference", () => {
  const r = resolveAircraftRefs(panel([bar({ tank: "tk_lt", name: "Tank 3" })]), profile(THREE));
  assert.deepEqual(served(r.yaml)[0].options, { dbkey: "FUELQ3", name: "L TIP" });
  assert.doesNotMatch(r.yaml, /tank:|tk_lt/);
});

test("a removed tank never emits a key", () => {
  // The panel was built against three tanks; L TIP (slot 3) is then removed.
  // The stored gauge still carries the dbkey an older editor might have kept.
  const text = panel([bar({ tank: "tk_lm" }), bar({ tank: "tk_lt", dbkey: "FUELQ3", name: "L TIP" })]);
  const without = profile(THREE.filter((t) => t.id !== "tk_lt"));
  for (const retired of [{}, { tk_lt: "L TIP" }]) {
    const out = served(resolveAircraftRefs(text, without, retired).yaml);
    assert.deepEqual(out[0].options, { dbkey: "FUELQ1", name: "L MAIN" });
    assert.equal("dbkey" in out[1].options, false);
    assert.equal(out[1].options.unbound, true);
    assert.equal(out[1].options.name, "L TIP");
  }
  // No profile at all, or a profile with no fuel group: every gauge unbound.
  for (const p of [null, JSON.stringify({ meta: {} })]) {
    const out = served(resolveAircraftRefs(text, p).yaml);
    for (const g of out) assert.equal("dbkey" in g.options, false, String(p));
    for (const g of out) assert.equal(g.options.unbound, true);
  }
});

test("a reference inside a tab_section page resolves, and each gauge is its own binding", () => {
  const tabs = { type: "tab_section", row: 0, column: 0, span: { rows: 50, columns: 50 },
    tabs: [{ label: "FUEL", layout: { rows: 110, columns: 200 },
      instruments: [bar({ tank: "tk_rm" }), bar({ tank: "tk_rm" }, "horizontal_bar_gauge")] }] };
  const r = resolveAircraftRefs(panel([tabs]), profile(THREE));
  const page = served(r.yaml)[0].tabs[0].instruments;
  assert.deepEqual(page.map((g) => g.options), [{ dbkey: "FUELQ2", name: "R MAIN" }, { dbkey: "FUELQ2", name: "R MAIN" }]);
  assert.deepEqual(r.bindings.map((b) => b.at), ["EMS/0/tabs/0/0", "EMS/0/tabs/0/1"]);
});

test("a panel without references is served as the same text, unparsed", () => {
  const text = "main: {defaultScreen: PFD}\nscreens:\n  PFD:\n    instruments:\n"
    + "    - type: static_text\n      options: {text: 1e3}\n";
  assert.equal(resolveAircraftRefs(text, profile(THREE)).yaml, text);
  // Mentioning `tank:` but not on a gauge, or unparseable: unchanged too.
  const other = text.replace("text: 1e3", "text: 1e3, tank: tk_lm");
  assert.equal(resolveAircraftRefs(other, profile(THREE)).yaml, other);
  assert.equal(resolveAircraftRefs("tank: [", profile(THREE)).yaml, "tank: [");
});

test("resolveDoc never mutates the parsed panel", () => {
  const doc = yaml.load(panel([bar({ tank: "tk_lm" })]));
  const before = JSON.stringify(doc);
  resolveDoc(doc, itemsBySource(profile(THREE)), {});
  assert.equal(JSON.stringify(doc), before);
});

// --- the ETag -------------------------------------------------------------

const TEXT = panel([bar({ tank: "tk_lt" }), bar({ dbkey: "FUELQT", name: "TOTAL" })]);

test("a rename changes the ETag, and the served name", async () => {
  const a = await serveDeviceConfig(TEXT, 4, profile(THREE));
  const renamed = THREE.map((t) => (t.id === "tk_lt" ? { ...t, label: "LEFT TIP" } : t));
  const b = await serveDeviceConfig(TEXT, 4, profile(renamed));
  assert.notEqual(a.etag, b.etag);
  assert.match(a.etag, /^"v4-p[0-9a-f]{12}"$/);
  assert.equal(served(b.body)[0].options.name, "LEFT TIP");
});

test("a rewire, or the tank's removal, changes the ETag; an unrelated edit does not", async () => {
  const base = await serveDeviceConfig(TEXT, 4, profile(THREE));
  const rewired = THREE.map((t) => (t.id === "tk_lt" ? { ...t, slot: 5 } : t));
  assert.notEqual((await serveDeviceConfig(TEXT, 4, profile(rewired))).etag, base.etag);
  assert.notEqual((await serveDeviceConfig(TEXT, 4, profile(THREE.slice(0, 2)))).etag, base.etag);
  // Another tank's label, a capacity, an unrelated profile value: same ETag.
  const other = THREE.map((t) => (t.id === "tk_rm" ? { ...t, label: "RIGHT", capacity: 30 } : t));
  assert.equal((await serveDeviceConfig(TEXT, 4, profile(other, { cruise_rpm: 2400 }))).etag, base.etag);
  // And the config version still moves it.
  assert.notEqual((await serveDeviceConfig(TEXT, 5, profile(THREE))).etag, base.etag);
});

test("an orphan's last label is in the ETag; the history loads only for an orphan", async () => {
  let calls = 0;
  const load = (labels) => async () => { calls++; return labels; };
  await serveDeviceConfig(TEXT, 4, profile(THREE), load({}));
  assert.equal(calls, 0);
  const gone = profile(THREE.slice(0, 2));
  const a = await serveDeviceConfig(TEXT, 4, gone, load({ tk_lt: "L TIP" }));
  const b = await serveDeviceConfig(TEXT, 4, gone, load({ tk_lt: "LEFT TIP" }));
  assert.equal(calls, 2);
  assert.notEqual(a.etag, b.etag);
  assert.deepEqual(served(a.body)[0].options, { name: "L TIP", unbound: true });
});

test("no reference: the body and ETag are exactly what the device was served before F3", async () => {
  const text = panel([bar({ dbkey: "FUELQ1", name: "Tank 1" })]);
  assert.deepEqual(await serveDeviceConfig(text, 7, profile(THREE)), { body: text, etag: '"v7"' });
  const withBaro = await serveDeviceConfig(text, 7, profile(THREE, { baro_unit: "hPa" }));
  assert.equal(withBaro.etag, (await serveDeviceConfig(text, 7, JSON.stringify({ meta: { baro_unit: "hPa" } }))).etag);
});

test("references and the preferences block ride together", async () => {
  const r = await serveDeviceConfig(TEXT, 4, profile(THREE, { baro_unit: "hPa" }));
  const doc = yaml.load(r.body);
  assert.deepEqual(doc.preferences, { units: { Pressure: "hPa" } });
  assert.equal(doc.screens.EMS.instruments[0].options.dbkey, "FUELQ3");
});

// --- the editor preview runs the same resolver ----------------------------

test("editor.html previews a gauge through the same resolveOptions the Worker serves", () => {
  const src = editorSource();
  assert.match(src, /await import\("\/aircraft_refs\.js"\)/);
  const items = itemsBySource(profile(THREE));
  const state = { aircraftRefs: { mod: { resolveOptions }, items, retired: { tk_x: "R TIP" } } };
  const refResolved = new Function("state", `${extractFunction(src, "refResolved")}; return refResolved;`)(state);
  const inst = bar({ tank: "tk_lt", decimal_places: 1 });
  const worker = served(resolveAircraftRefs(panel([inst]), profile(THREE)).yaml)[0].options;
  assert.deepEqual(refResolved(inst).options, worker);
  assert.deepEqual(refResolved(bar({ tank: "tk_x" })).options, { name: "R TIP", unbound: true });
  const plain = bar({ dbkey: "OILP1" });
  assert.equal(refResolved(plain), plain);
  // In the twin, before the theme pass.
  assert.match(extractFunction(src, "buildInstrumentTwin"), /themedInstrument\(refResolved\(inst\)\)/);
});
