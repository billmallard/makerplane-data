// SPDX-License-Identifier: AGPL-3.0-or-later
//
// DSP1 / AER-6686: one aural host per aircraft. In a project of two or more
// devices, GET /device/config writes `nodeID` (unique) and `AuralEnabled`
// (true on one device) into the served `main:` block; a one-device project is
// served what it was served before. Imports the REAL src/device_roles.ts and
// src/device_prefs.ts (Node strips the types), the modules the Worker calls.

import { test } from "node:test";
import assert from "node:assert/strict";
import yaml from "js-yaml";
import { effectiveRoles, injectMainRoles, servedRoles } from "../src/device_roles.ts";
import { serveDeviceConfig } from "../src/device_prefs.ts";

// What the editor saves (toPyefisDoc through js-yaml dump).
const PANEL = yaml.dump({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: "PFD" },
  screens: { PFD: { module: "pyefis.screens.screenbuilder", title: "PFD",
    layout: { rows: 110, columns: 200 }, instruments: [] } },
}, { lineWidth: 100, noRefs: true });

const row = (id, aural = null, node_id = null) => ({ id, aural, node_id });

test("two automatic devices: the first plays the tones, nodes 1 and 2", () => {
  const r = effectiveRoles([row(8), row(5)]);
  assert.deepEqual(r.get(5), { nodeID: 1, AuralEnabled: true });
  assert.deepEqual(r.get(8), { nodeID: 2, AuralEnabled: false });
});

test("one explicit 'plays them' silences every automatic device", () => {
  const r = effectiveRoles([row(1), row(2, 1), row(3)]);
  assert.deepEqual([...r.values()].map((x) => x.AuralEnabled), [false, true, false]);
});

test("switching the first device off moves the tones to the next automatic one", () => {
  const r = effectiveRoles([row(1, 0), row(2), row(3)]);
  assert.deepEqual([...r.values()].map((x) => x.AuralEnabled), [false, true, false]);
});

test("automatic nodes take the smallest numbers an explicit one leaves", () => {
  const r = effectiveRoles([row(1), row(2, null, 1), row(3)]);
  assert.deepEqual([...r.values()].map((x) => x.nodeID), [2, 1, 3]);
  const nodes = [...effectiveRoles([row(1), row(2), row(3, null, 2), row(4)]).values()].map((x) => x.nodeID);
  assert.equal(new Set(nodes).size, 4);
});

test("servedRoles: a one-device project serves only what was set", () => {
  assert.deepEqual(servedRoles([row(4)], 4), {});
  assert.deepEqual(servedRoles([row(4, 0)], 4), { AuralEnabled: false });
  assert.deepEqual(servedRoles([row(4, null, 3)], 4), { nodeID: 3 });
  assert.deepEqual(servedRoles([row(4), row(6)], 6), { nodeID: 2, AuralEnabled: false });
  assert.deepEqual(servedRoles([row(4)], 9), {});
});

test("injectMainRoles writes into the existing main: block, keeping the rest", () => {
  const out = injectMainRoles(PANEL, { nodeID: 2, AuralEnabled: false });
  const doc = yaml.load(out.yaml);
  assert.deepEqual(doc.main, { nodeID: 2, AuralEnabled: false, screenWidth: 1280,
    screenHeight: 800, defaultScreen: "PFD" });
  assert.deepEqual(doc.screens, yaml.load(PANEL).screens);
  assert.deepEqual(out.roles, { nodeID: 2, AuralEnabled: false });
});

test("injectMainRoles: a key the panel sets wins; no main: gets one; flow style is left", () => {
  const own = "main:\n    nodeID: 4\n    defaultScreen: PFD\nscreens: {}\n";
  const out = injectMainRoles(own, { nodeID: 2, AuralEnabled: true });
  assert.deepEqual(yaml.load(out.yaml).main, { AuralEnabled: true, nodeID: 4, defaultScreen: "PFD" });
  assert.deepEqual(out.roles, { AuralEnabled: true });
  const none = injectMainRoles("screens: {}\n", { nodeID: 2 });
  assert.deepEqual(yaml.load(none.yaml).main, { nodeID: 2 });
  const flow = "main: {defaultScreen: PFD}\nscreens: {}\n";
  assert.deepEqual(injectMainRoles(flow, { nodeID: 2 }), { yaml: flow, roles: {} });
});

test("serveDeviceConfig: a two-device project serves the tones to one device and two nodes", async () => {
  const rows = [row(11), row(12)];
  const a = await serveDeviceConfig(PANEL, 3, null, undefined, undefined, servedRoles(rows, 11));
  const b = await serveDeviceConfig(PANEL, 3, null, undefined, undefined, servedRoles(rows, 12));
  const ma = yaml.load(a.body).main;
  const mb = yaml.load(b.body).main;
  assert.equal(ma.AuralEnabled, true);
  assert.equal(mb.AuralEnabled, false);
  assert.notEqual(ma.nodeID, mb.nodeID);
  assert.match(a.etag, /^"v3-p[0-9a-f]{12}"$/);
  assert.notEqual(a.etag, b.etag);
});

test("serveDeviceConfig: a one-device project is served exactly what it was before", async () => {
  const before = await serveDeviceConfig(PANEL, 3, null);
  const after = await serveDeviceConfig(PANEL, 3, null, undefined, undefined, servedRoles([row(11)], 11));
  assert.deepEqual(after, before);
  assert.deepEqual(before, { body: PANEL, etag: '"v3"' });
});

test("serveDeviceConfig: the roles change the ETag, a profile block still rides after them", async () => {
  const profile = JSON.stringify({ schema_version: 1, meta: { baro_unit: "hPa" }, values: {} });
  const plain = await serveDeviceConfig(PANEL, 3, profile);
  const roles = await serveDeviceConfig(PANEL, 3, profile, undefined, undefined, { nodeID: 2, AuralEnabled: false });
  assert.notEqual(plain.etag, roles.etag);
  const doc = yaml.load(roles.body);
  assert.deepEqual(doc.preferences, { units: { Pressure: "hPa" } });
  assert.equal(doc.main.nodeID, 2);
});
