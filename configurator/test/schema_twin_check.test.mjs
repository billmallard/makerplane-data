// SPDX-License-Identifier: AGPL-3.0-or-later
//
// INS4 (AER-6587): tools/schema_twin_check.mjs, the check pyEfis CI runs
// against the schema.json it has just built, finds each kind of drift the
// twins can have from the spec, and nothing on a schema they agree with.
//
// The fixture is the dev schema.json the editor loaded on 2026-11-25
// (https://pyefis-dev.aerocommons.org/assets/editor/schema.json, sha256
// ffb0011fa7498eb15bc4167e43bc736f8a88c8059ec45a297cfb2d090455b832),
// unmodified. Every seeded change below is applied to a copy of it.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { PLACEHOLDER_ONLY, checkSchemaTwins, twinTypes } from "../tools/schema_twin_check.mjs";
import { editorSource, extractFunction } from "./support/extract.mjs";

const RAW = readFileSync(new URL("./fixtures/schema_pyefis_dev.json", import.meta.url), "utf8");
const fresh = () => JSON.parse(RAW);
const SRC = editorSource();
const TOOL = fileURLToPath(new URL("../tools/schema_twin_check.mjs", import.meta.url));
const SHA = "0123456789abcdef0123456789abcdef01234567";

// The one gap the dev schema still has (INS4's pyEfis half closes it).
const settled = (s) => {
  s.pyefis_commit = SHA;
  return s;
};
const only = (r, re) => {
  assert.ok(r.errors.length >= 1, "expected an error");
  for (const e of r.errors) assert.match(e, re);
};

test("the dev schema: no errors, and the one warning is the missing stamp", () => {
  const r = checkSchemaTwins(fresh(), SRC);
  assert.deepEqual(r.errors, []);
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0], /^pyefis_commit:/);
  assert.deepEqual(r.notes, []);
});

test("with the stamp, nothing is left to report", () => {
  assert.deepEqual(checkSchemaTwins(settled(fresh()), SRC), { errors: [], warnings: [], notes: [] });
});

// --- a seeded change to a spec enum or default is an error ----------------

test("fd_style: a new style, a dropped one, or a new default", () => {
  for (const edit of [
    (f) => f.enum.push("flight_path"),
    (f) => f.enum.splice(1, 1),
    (f) => { f.default = "cross_pointer"; },
  ]) {
    const s = fresh(); edit(s.instruments.atitude_indicator.options.fd_style);
    only(checkSchemaTwins(s, SRC), /^atitude_indicator\.fd_style /);
  }
});

test("static_text alignment: a new value, or a default the unset preview does not match", () => {
  let s = fresh(); s.instruments.static_text.options.alignment.enum.push("AlignJustify");
  only(checkSchemaTwins(s, SRC), /^static_text\.alignment enum/);
  s = fresh(); s.instruments.static_text.options.alignment.default = "AlignCenter";
  only(checkSchemaTwins(s, SRC), /^static_text\.alignment default "AlignCenter" previews center/);
});

test("a colour default: autopilot panel, AP strip, value_text", () => {
  for (const [type, opt, re] of [
    ["autopilot_panel", "active_color", /^AP_COLORS\.active_color /],
    ["ap_annunciator", "warning_color", /^AP_STRIP_COLORS\.warning_color /],
    ["value_text", "prefix_color", /^VALUE_TEXT_DEFAULTS\.prefix_color /],
  ]) {
    const s = fresh(); s.instruments[type].options[opt].default = "#123456";
    only(checkSchemaTwins(s, SRC), re);
  }
  // Case is not a colour change.
  const s = fresh(); s.instruments.autopilot_panel.options.active_color.default = "#00FF00";
  assert.deepEqual(checkSchemaTwins(s, SRC).errors, []);
});

test("a new colour option the twin does not know, and a value_text default that moves", () => {
  let s = fresh();
  s.instruments.autopilot_panel.options.glow_color = { type: "color", default: "#ff00ff" };
  only(checkSchemaTwins(s, SRC), /^AP_COLORS\.glow_color: missing/);
  s = fresh(); s.instruments.value_text.options.border_opacity.default = 80;
  only(checkSchemaTwins(s, SRC), /^VALUE_TEXT_DEFAULTS\.border_opacity /);
});

test("a per-type font default the text twin disagrees with is an error", () => {
  const s = settled(fresh());
  s.instruments.value_text.options.font_family = { ...s.common_options.font_family, default: "Roboto" };
  only(checkSchemaTwins(s, SRC), /^TEXT_DEFAULTS\.value_text\.font "DejaVu Sans Condensed" != value_text\.font_family default "Roboto"/);
});

test("a common font default the text twins do not use, with no per-type one, is a warning for each", () => {
  const s = settled(fresh());
  s.common_options.font_family.default = "Roboto";
  const r = checkSchemaTwins(s, SRC);
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.warnings.map((w) => w.split(":")[0]).sort(),
    ["heading_display", "numeric_display", "static_text", "value_text"]);
  for (const w of r.warnings) assert.match(w, /the twin's font is "DejaVu Sans Condensed".*the common one is "Roboto"/);
});

// --- twin coverage --------------------------------------------------------

test("the twin dispatch covers every type but the placeholder-only list", () => {
  const I = fresh().instruments;
  const twins = twinTypes(SRC, { instruments: I });
  assert.deepEqual(Object.keys(I).filter((t) => !twins.has(t)).sort(), [...PLACEHOLDER_ONLY].sort());
});

test("a new type with no twin, not on the list, is an error; a dropped one is only a note", () => {
  let s = fresh();
  s.instruments.engine_monitor = { label: "Engine Monitor", options: {} };
  only(checkSchemaTwins(s, SRC), /^engine_monitor: no twin and not on PLACEHOLDER_ONLY/);
  s = fresh(); delete s.instruments.weston; delete s.instruments.moving_map;
  const r = checkSchemaTwins(s, SRC);
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.notes, ["weston: on PLACEHOLDER_ONLY, but the schema no longer declares it",
    "moving_map: the editor has a twin for it, but the schema no longer declares it"]);
});

test("a placeholder-only type that gains a twin must come off the list", () => {
  const fn = extractFunction(SRC, "buildInstrumentTwin");
  const src = SRC.replace(fn, fn.replace('} else if (inst.type === "moving_map") {',
    '} else if (inst.type === "weston") {\n      el.appendChild(buildWeston(inst));\n    } else if (inst.type === "moving_map") {'));
  assert.notEqual(src, SRC);
  only(checkSchemaTwins(fresh(), src), /^weston: has a twin now/);
});

test("an older schema (qa, main) without the newer types is notes, never errors", () => {
  const s = fresh();
  for (const t of ["autopilot_panel", "ap_annunciator"]) delete s.instruments[t];
  for (const t of ["atitude_indicator", "virtual_vfr"]) delete s.instruments[t].options.fd_style;
  const r = checkSchemaTwins(s, SRC);
  assert.deepEqual(r.errors, []);
  assert.ok(r.notes.some((n) => /^fd_style: no type declares it/.test(n)));
  assert.ok(r.notes.some((n) => /^autopilot_panel: not declared; AP_COLORS not checked/.test(n)));
});

// --- version, provenance, references -------------------------------------

test("schema_version: newer than the editor knows, or missing, is an error", () => {
  let s = fresh(); s.schema_version = 5;
  only(checkSchemaTwins(s, SRC), /^schema_version 5 is newer than the editor's SCHEMA_VERSION_MAX 4/);
  s = fresh(); delete s.schema_version;
  only(checkSchemaTwins(s, SRC), /^schema_version: missing/);
});

test("an aircraft_ref the resolver cannot resolve, or one declared two ways, is an error", () => {
  const tank = () => ({ ...fresh().instruments.arc_gauge.options.tank });
  let s = fresh(); s.instruments.checklist.options.list = { ...tank(), source: "aircraft.checklists" };
  only(checkSchemaTwins(s, SRC), /^checklist\.list: aircraft_ref source "aircraft\.checklists"/);
  s = fresh(); s.instruments.vertical_bar_gauge.options.tank.resolves = { dbkey: "slot_key" };
  only(checkSchemaTwins(s, SRC), /^vertical_bar_gauge\.tank: an aircraft_ref declared differently/);
  s = fresh(); s.instruments.arc_gauge.options.tank.multi = true;
  only(checkSchemaTwins(s, SRC), /^arc_gauge\.tank: a multi aircraft_ref/);
  s = fresh(); s.instruments.numeric_display.options.tank = tank();
  assert.deepEqual(checkSchemaTwins(s, SRC).errors, [], "a new type with the tank prop is fine");
});

// --- the command line pyEfis CI runs --------------------------------------

test("CLI: exit 0 on the dev schema, 1 under --strict, 1 on a seeded change, 2 with no path", () => {
  const dir = mkdtempSync(join(tmpdir(), "ins4-"));
  const write = (name, doc) => { const p = join(dir, name); writeFileSync(p, JSON.stringify(doc)); return p; };
  const run = (...a) => spawnSync(process.execPath, [TOOL, ...a], { encoding: "utf8" });
  const dev = write("dev.json", fresh());
  assert.equal(run(dev).status, 0);
  assert.equal(run(dev, "--strict").status, 1);
  assert.equal(run(write("settled.json", settled(fresh())), "--strict").status, 0);
  const bad = fresh(); bad.instruments.atitude_indicator.options.fd_style.enum.push("flight_path");
  const r = run(write("bad.json", bad));
  assert.equal(r.status, 1);
  assert.match(r.stdout, /^error: atitude_indicator\.fd_style enum/m);
  assert.equal(run().status, 2);
});
