// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// LAYADV1 (AER-6539): the per-layout advisory's rule set, run from the REAL
// public/layout_advisory.js. The save and served paths are in
// layout_advisory_save.test.mjs.

import { test } from "node:test";
import assert from "node:assert/strict";
import jsyaml from "js-yaml";
import { layoutAdvisory, advisoryLines, advisoryComment, CAS_ALERTS } from "../public/layout_advisory.js";

const at = (type, row = 0, column = 0, rows = 10, columns = 10, extra = {}) =>
  ({ type, row, column, span: { rows, columns }, ...extra });
const doc = (screens) => ({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: Object.keys(screens)[0] },
  screens: Object.fromEntries(Object.entries(screens).map(([name, instruments]) =>
    [name, { module: "pyefis.screens.screenbuilder", title: name, layout: { rows: 110, columns: 200 }, instruments }])),
});
const rule = (a, n) => a.findings.filter((f) => f.rule === n);

// --- rule 1 ---------------------------------------------------------------

test("rule 1: CAS on one screen, an attitude screen without it is warned and its silent alerts named", () => {
  const a = layoutAdvisory(doc({
    PFD: [at("atitude_indicator"), at("horizontal_situation_indicator", 50)],
    RADIO: [at("cas_window")],
  }));
  const r1 = rule(a, 1);
  assert.equal(r1.length, 1);
  assert.equal(r1[0].screen, "PFD");
  assert.equal(r1[0].level, "warning");
  assert.match(r1[0].text, /carries an attitude instrument but no CAS window or CAS marker/);
  // The HSI flags HDG and NAV; the autopilot link and a silent sender have no cue there.
  assert.match(r1[0].text, /no other cue on that screen: AP LINK LOST, FUEL QTY <n> NO DATA\./);
  assert.doesNotMatch(r1[0].text, /HDG FAIL|NAV FAIL/);
});

test("rule 1: a cue instrument on the screen takes its alert off the list", () => {
  const a = layoutAdvisory(doc({
    PFD: [at("virtual_vfr"), at("autopilot_panel", 90)],
    RADIO: [at("cas_marker")],
  }));
  const text = rule(a, 1)[0].text;
  assert.match(text, /: HDG FAIL, NAV FAIL, FUEL QTY <n> NO DATA\./);
  assert.doesNotMatch(text, /AP LINK LOST/);
});

test("rule 1: a CAS marker on the attitude screen satisfies it", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), at("cas_marker", 100)] }));
  assert.deepEqual(rule(a, 1), []);
});

test("rule 1: turn coordinator and AP mode strip do not make a screen one you fly from", () => {
  const a = layoutAdvisory(doc({
    TC: [at("turn_coordinator"), at("ap_annunciator", 20)],
    PFD: [at("atitude_indicator"), at("cas_marker", 100)],
  }));
  const r1 = rule(a, 1);
  assert.equal(r1.length, 1);
  assert.equal(r1[0].screen, "TC");
  assert.match(r1[0].text, /If it is ever up in front of you in flight/);
});

test("rule 1: a non-attitude screen without CAS gets the conditional warning", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), at("cas_marker", 100)], EMS: [at("arc_gauge")] }));
  const r1 = rule(a, 1);
  assert.equal(r1.length, 1);
  assert.equal(r1[0].screen, "EMS");
  assert.match(r1[0].text, /^Screen "EMS" has no CAS window or CAS marker\. If it is ever up/);
});

test("rule 1: a CAS surface on a tab page does not count for its screen, but does put CAS in use", () => {
  const tabbed = at("tab_section", 0, 0, 50, 50, { tabs: [{ label: "A", layout: { rows: 110, columns: 200 }, instruments: [at("cas_window")] }] });
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), tabbed] }));
  assert.equal(rule(a, 1).length, 1);
  assert.equal(rule(a, 3).length, 0);
});

test("rule 1: a cue instrument on a tab page is hidden, so its alert stays on the list", () => {
  const tabbed = at("tab_section", 0, 0, 50, 50, { tabs: [{ label: "AP", instruments: [at("autopilot_panel")] }] });
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), tabbed], R: [at("cas_marker")] }));
  assert.match(rule(a, 1)[0].text, /AP LINK LOST/);
});

test("rule 1: every alert keeps a cue -> says so instead of listing", () => {
  const a = layoutAdvisory(doc({
    PFD: [at("atitude_indicator"), at("horizontal_situation_indicator"), at("autopilot_panel")],
    R: [at("cas_marker")],
  }));
  // FUEL QTY NO DATA never has a cue, so a full cover is impossible with the shipped list.
  assert.match(rule(a, 1)[0].text, /: FUEL QTY <n> NO DATA\./);
  const saved = CAS_ALERTS.slice();
  CAS_ALERTS.splice(3, 1);
  try {
    const b = layoutAdvisory(doc({
      PFD: [at("atitude_indicator"), at("horizontal_situation_indicator"), at("autopilot_panel")],
      R: [at("cas_marker")],
    }));
    assert.match(rule(b, 1)[0].text, /keeps its own cue there, but its message does not show/);
  } finally { CAS_ALERTS.splice(0, CAS_ALERTS.length, ...saved); }
});

// --- rule 2 ---------------------------------------------------------------

test("rule 2: a CAS surface past the grid edge is warned; one inside is not", () => {
  const a = layoutAdvisory(doc({
    PFD: [at("atitude_indicator"), at("cas_marker", 100, 190, 15, 15)],
    MAP: [at("cas_window", 0, 0, 20, 40)],
  }));
  const r2 = rule(a, 2);
  assert.equal(r2.length, 1);
  assert.equal(r2[0].screen, "PFD");
  assert.match(r2[0].text, /CAS marker on screen "PFD" runs past the edge of the screen \(rows 100 to 115 of 110, columns 190 to 205 of 200\)/);
});

test("rule 2: a zero-sized surface, and one on a tab page checked against the tab's own grid", () => {
  const tabbed = at("tab_section", 0, 0, 50, 50, { tabs: [{ label: "A", layout: { rows: 20, columns: 20 }, instruments: [at("cas_window", 15, 0, 10, 10)] }] });
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), at("cas_marker", 0, 0, 0, 5)], X: [tabbed] }));
  const r2 = rule(a, 2).map((f) => f.text);
  assert.equal(r2.length, 2);
  assert.match(r2[0], /CAS marker on screen "PFD" has no size/);
  assert.match(r2[1], /CAS window on screen "X" \(on a tab page\) runs past the edge of the screen \(rows 15 to 25 of 20\)/);
});

// --- rule 3 and the requirements ------------------------------------------

test("rule 3: no CAS anywhere is a note, not a warning, and no rule 1", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator")], EMS: [at("arc_gauge")] }));
  assert.deepEqual(a.findings.map((f) => [f.rule, f.level]), [[3, "note"]]);
  assert.match(a.findings[0].text, /a fuel sender that never reports has no cue at all/);
  assert.match(a.findings[0].text, /pyEfis shows CAS NOT SHOWN/);
  assert.deepEqual(a.requirements, []);
});

test("requirements: CAS placed -> field-of-view rule and one sense; lamps named when placed", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), at("cas_marker"), at("annunciator")] }));
  assert.equal(a.requirements.length, 3);
  assert.match(a.requirements[0], /must sit in your primary field of view/);
  assert.match(a.requirements[0], /within 15 degrees of your normal line of sight/);
  assert.match(a.requirements[1], /^Annunciator lamps are placed on "PFD"\./);
  assert.match(a.requirements[2], /^One sense only: pyEfis has no alert tone yet\./);
});

test("requirements: lamps with no CAS still carry the field-of-view rule", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator"), at("annunciator")] }));
  assert.equal(rule(a, 3).length, 1);
  assert.equal(a.requirements.length, 3);
});

test("inert when the schema has no CAS type, and never throws on junk", () => {
  assert.deepEqual(layoutAdvisory(doc({ PFD: [at("atitude_indicator")] }), false), { findings: [], requirements: [] });
  for (const junk of [null, undefined, 3, "x", {}, { screens: null }, { screens: { A: null } },
    { screens: { A: { instruments: [null, 7, { type: "cas_marker", span: "x" }, { tabs: [null] }] } } }]) {
    assert.doesNotThrow(() => layoutAdvisory(junk));
  }
});

// --- text and the saved header --------------------------------------------

test("advisoryLines: findings first, then requirements", () => {
  const a = layoutAdvisory(doc({ PFD: [at("atitude_indicator")], R: [at("cas_marker")] }));
  const lines = advisoryLines(a);
  assert.match(lines[0], /^WARNING: Screen "PFD"/);
  assert.match(lines[lines.length - 1], /^One sense only/);
});

test("advisoryComment: every line a comment, the YAML under it unchanged, screen names kept on one line", () => {
  const d = doc({ "P\nFD": [at("atitude_indicator")], R: [at("cas_marker")] });
  const head = advisoryComment(layoutAdvisory(d));
  for (const line of head.trimEnd().split("\n")) {
    assert.match(line, /^#/);
    assert.ok(line.length <= 100, line);
  }
  assert.match(head, /"P FD"/);
  const text = head + jsyaml.dump(d, { lineWidth: 100, noRefs: true });
  assert.deepEqual(jsyaml.load(text), d);
  assert.equal(advisoryComment({ findings: [], requirements: [] }), "");
});
