// SPDX-License-Identifier: AGPL-3.0-or-later
//
// FUEL1f / AER-5547 (docs/fuel_provider_model.md section 5): making a
// profile active that removes a tank a published panel is bound to warns
// and lists the panels; it never refuses. Runs the REAL orphanedBy
// (src/aircraft_refs.ts, which POST /api/projects/:id/aircraft/orphans
// calls per device) and the REAL page functions out of aircraft.html.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";
import { orphanedBy } from "../src/aircraft_refs.ts";
import { extractFunction } from "./support/extract.mjs";

const PAGE = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "..", "public", "aircraft.html"), "utf8");

const tank = (id, label, slot) =>
  ({ id, label, role: "main", slot, feeds: [], capacity: 20, unusable: 0 });
const profile = (tanks) =>
  JSON.stringify({ schema_version: 1, meta: { fuel: { schema: 2, tanks } }, values: {} });
const THREE = [tank("tk_lm", "L MAIN", 1), tank("tk_rm", "R MAIN", 2), tank("tk_lt", "L TIP", 3)];
const bar = (options) =>
  ({ type: "vertical_bar_gauge", row: 0, column: 0, span: { rows: 20, columns: 10 }, options });
const panel = (instruments) => yaml.dump({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: "EMS" },
  screens: { EMS: { module: "pyefis.screens.screenbuilder", title: "EMS",
    layout: { rows: 110, columns: 200 }, instruments } },
}, { lineWidth: 100, noRefs: true });

const PANEL = panel([bar({ tank: "tk_lm" }), bar({ tank: "tk_lt" }), bar({ dbkey: "FUELQ2", name: "Tank 2" })]);

test("removing a bound tank orphans exactly the gauges bound to it, under their last label", () => {
  const without = profile(THREE.filter((t) => t.id !== "tk_lt"));
  assert.deepEqual(orphanedBy(PANEL, profile(THREE), without),
    [{ at: "EMS/1", option: "tank", id: "tk_lt", label: "L TIP" }]);
});

test("a rename, a reslot, or removing an unbound tank orphans nothing", () => {
  const renamed = profile([tank("tk_lm", "LEFT MN", 1), THREE[1], tank("tk_lt", "L TIP", 5)]);
  assert.deepEqual(orphanedBy(PANEL, profile(THREE), renamed), []);
  // R MAIN (slot 2) is read by a plain FUELQ2 bar, not a reference.
  assert.deepEqual(orphanedBy(PANEL, profile(THREE), profile(THREE.filter((t) => t.id !== "tk_rm"))), []);
});

test("a gauge already orphaned by the active profile is not reported again", () => {
  const without = profile(THREE.filter((t) => t.id !== "tk_lt"));
  assert.deepEqual(orphanedBy(PANEL, without, without), []);
  assert.deepEqual(orphanedBy(PANEL, without, profile([])), [
    { at: "EMS/0", option: "tank", id: "tk_lm", label: "L MAIN" }]);
});

test("no active profile, or a panel with no references, orphans nothing", () => {
  assert.deepEqual(orphanedBy(PANEL, null, profile([])), []);
  assert.deepEqual(orphanedBy(panel([bar({ dbkey: "FUELQ1" })]), profile(THREE), profile([])), []);
});

test("the page lists every orphaning panel and asks before saving; no orphans saves silently", async () => {
  const confirms = [], posts = [];
  const make = (panels, ok = true) => new Function("PROJECT", "fetch", "confirm",
    extractFunction(PAGE, "orphanWarning") + "\nasync " + extractFunction(PAGE, "confirmOrphans")
    + "\nreturn confirmOrphans;")("7",
    async (url, init) => { posts.push([url, JSON.parse(init.body)]); return { ok, json: async () => ({ panels }) }; },
    (text) => { confirms.push(text); return false; });
  const p = { meta: {} };
  assert.equal(await make([])({ profile: p }), true);
  assert.deepEqual(posts[0], ["/api/projects/7/aircraft/orphans", { profile: p }]);
  assert.equal(confirms.length, 0);
  const panels = [
    { device: "Left EFIS", version: 4, gauges: [{ label: "L TIP" }, { label: null }] },
    { device: "EMS", version: 2, gauges: [{ label: "L TIP" }] }];
  assert.equal(await make(panels)({ profile: p }), false, "declining the warning stops the save");
  assert.match(confirms[0], /- Left EFIS \(panel v4\): "L TIP", a gauge\n- EMS \(panel v2\): "L TIP"/);
  assert.match(confirms[0], /failed \(xxx\) under its last label/);
  // The check failing is not a reason to refuse the save.
  assert.equal(await make(panels, false)({ profile: p }), true);
});
