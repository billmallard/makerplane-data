// SPDX-License-Identifier: AGPL-3.0-or-later
//
// FUEL1f / AER-5547 (docs/fuel_provider_model.md sections 5-7): the editor's
// tank picker, the orphan mark and failed look, the stock-bar migration
// offer and the bar name line. Runs the REAL functions out of editor.html
// (brace-matched, test/support/extract.mjs) against the REAL
// public/aircraft_refs.js and public/fuel_label.js, with the failed look and
// the name font pinned to what tools/fuel_gauge_oracle.py read off the real
// pyEfis widgets (test/fixtures/fuel_gauge_pyefis.json).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as refs from "../public/aircraft_refs.js";
import * as fuelLabel from "../public/fuel_label.js";
import { editorSource, extractConst, extractFunction } from "./support/extract.mjs";

const FIXTURE = JSON.parse(readFileSync(
  new URL("./fixtures/fuel_gauge_pyefis.json", import.meta.url), "utf8"));
const src = editorSource();
const FNS = ["sampleGaugeValue", "barNameLine", "buildBarGauge", "_polar", "_arcCW", "buildArcGauge",
  "refResolved", "tankBinding", "migratedOptions", "profileBandsOnDevice", "markTankBinding",
  "refWrites", "refItemText", "aircraftRefField", "applyTankBinding", "unbindTank",
  "migrationTarget", "migrationOffer"];

// Just enough DOM for these builders.
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.style = {}; this.children = []; this.handlers = {};
    this.classes = new Set(); this.textContent = ""; this.title = ""; this.value = ""; this.disabled = false;
    const cl = this.classes;
    this.classList = { add: (...c) => c.forEach((x) => cl.add(x)), contains: (c) => cl.has(c),
      remove: (c) => cl.delete(c), toggle: (c, on) => (on ? cl.add(c) : cl.delete(c)) };
  }
  set className(v) { this.classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classes.add(c)); }
  get className() { return [...this.classes].join(" "); }
  appendChild(c) { this.children.push(c); return c; }
  append(...c) { this.children.push(...c); }
  addEventListener(ev, fn) { this.handlers[ev] = fn; }
  fire(ev) { this.handlers[ev](); }
  getContext() {
    // The browser measures in the real font; here, the widget's own advances.
    const ctx = { font: "", measureText: (t) => {
      const px = /^(\d+)px/.exec(ctx.font)[1];
      return { width: [...t].reduce((s, ch) => s + FIXTURE.advances[px][ch], 0) };
    } };
    return ctx;
  }
  find(pred) {
    if (pred(this)) return this;
    for (const c of this.children) { const r = c instanceof El && c.find(pred); if (r) return r; }
    return null;
  }
}
const document = { createElement: (t) => new El(t) };

const tank = (id, label, slot) => ({ id, label, role: "main", slot, feeds: [], capacity: 20, unusable: 0 });
const profile = (tanks) => JSON.stringify({ schema_version: 1, meta: { fuel: { schema: 2, tanks } }, values: {} });
const THREE = [tank("tk_lm", "L MAIN", 1), tank("tk_rm", "R MAIN", 2), tank("tk_lt", "L TIP", 3)];
const SCHEMA = { instruments: Object.fromEntries(
  ["vertical_bar_gauge", "horizontal_bar_gauge", "arc_gauge"].map((t) =>
    [t, { options: { tank: { type: "aircraft_ref", source: "aircraft.fuel.tanks" } } }])) };

function editor({ dev = { w: 65, h: 327 }, canvas = dev, tanks = THREE, retired = {} } = {}) {
  const state = {
    schema: SCHEMA, fuelLabel, migrationPreview: null,
    aircraftRefs: { mod: refs, items: refs.itemsBySource(profile(tanks)), retired },
  };
  const calls = [];
  const f = new Function("document", "state", "rect", "mapDevicePx", "svgFromString",
    "renderCanvas", "renderProps",
    extractConst(src, "GAUGE_FILL") + "\n" + extractConst(src, "GAUGE_FAIL") + "\nlet _nameCtx = null;\n"
    + FNS.map((n) => extractFunction(src, n)).join("\n\n")
    + `\nreturn { GAUGE_FAIL, ${FNS.join(", ")} };`)(
    document, state, () => ({ x: 0, y: 0, ...canvas }), () => dev, (s) => s,
    () => calls.push("canvas"), () => calls.push("props"));
  return { ...f, state, calls };
}
const gauge = (options, type = "vertical_bar_gauge") =>
  ({ type, row: 0, column: 0, span: { rows: 30, columns: 10 }, options });
const hex = ([r, g, b]) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");

// --- the failed look ------------------------------------------------------

test("GAUGE_FAIL is what setUnbound() leaves on every gauge type", () => {
  const { GAUGE_FAIL } = editor();
  for (const f of FIXTURE.fail) {
    assert.equal(f.unbound && f.fail, true, f.type);
    assert.equal(GAUGE_FAIL.value, f.value_text, `${f.type} value text`);
    assert.equal(f.value, 0, `${f.type} value held at 0`);
    assert.deepEqual(Object.values(f.bands), [null, null, null, null], `${f.type} draws no bands`);
    assert.equal(GAUGE_FAIL.safe, hex(f.colors.safeColor), `${f.type} bar`);
    assert.equal(GAUGE_FAIL.text, hex(f.colors.textColor), `${f.type} text`);
    assert.equal(GAUGE_FAIL.text, hex(f.colors.valueColor), `${f.type} value`);
    assert.equal(GAUGE_FAIL.pen, hex(f.colors.penColor), `${f.type} pointer`);
  }
});

test("an unbound bar draws the failed look; a bound one does not", () => {
  const e = editor({ dev: { w: 80, h: 300 } });
  const w = e.buildBarGauge(gauge({ name: "L TIP", unbound: true }), true);
  const [name, track, val] = w.children;
  assert.ok(w.classList.contains("fail"));
  assert.equal(w.style.color, e.GAUGE_FAIL.text);
  assert.equal(track.style.background, e.GAUGE_FAIL.safe, "one colour, no bands");
  assert.equal(track.children[0].style.background, e.GAUGE_FAIL.pen);
  assert.equal(track.children[0].style.bottom, "0%", "held at 0");
  assert.equal(val.textContent, "xxx");
  assert.equal(name.textContent, "L TIP", "keeps its last label");
  const ok = e.buildBarGauge(gauge({ name: "L TIP", dbkey: "FUELQ3" }), true);
  assert.ok(!ok.classList.contains("fail"));
  assert.notEqual(ok.children[2].textContent, "xxx");
});

test("an unbound arc is one grey sweep with the needle at rest and xxx", () => {
  const e = editor();
  const svg = e.buildArcGauge(gauge({ name: "L TIP", unbound: true }, "arc_gauge"));
  const strokes = [...svg.matchAll(/stroke="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(strokes)], [e.GAUGE_FAIL.safe]);
  assert.match(svg, new RegExp(`fill="${e.GAUGE_FAIL.pen}"/>`));
  assert.match(svg, />xxx<\/text>/);
  assert.match(svg, new RegExp(`fill="${e.GAUGE_FAIL.text}"[^>]*>L TIP<`));
  assert.doesNotMatch(e.buildArcGauge(gauge({ name: "L TIP" }, "arc_gauge")), />xxx</);
});

// --- the name line --------------------------------------------------------

for (const l of FIXTURE.labels.filter((l) => !l.stock && l.type !== "arc_gauge")) {
  test(`the twin's ${l.type} name at ${l.w}x${l.h} is the widget's font and first line`, () => {
    const e = editor({ dev: { w: l.w, h: l.h } });
    const vertical = l.type === "vertical_bar_gauge";
    for (const c of l.cases) {
      const name = e.buildBarGauge(gauge({ name: c.label }, l.type), vertical).children[0];
      assert.equal(name.style.fontSize, l.name_px + "px", `${c.label}: name font`);
      const want = fuelLabel.nameLine(c.label, l.name_rect[2], (t) =>
        [...t].reduce((s, ch) => s + FIXTURE.advances[String(l.name_px)][ch], 0)).text;
      assert.equal(name.textContent, want, c.label);
    }
  });
}

test("on the stock bar a two-word label too wide shows its first line, one word stays whole", () => {
  // The stock EMS2 bar at 1280x720 (small_font_percent 0.06, from the oracle).
  const s = FIXTURE.stock.find((b) => b.w === 65);
  const e = editor({ dev: { w: s.w, h: s.h } });
  const name = (label) => e.buildBarGauge(gauge({ name: label, small_font_percent: s.small_font_percent }), true).children[0];
  assert.equal(name("R TIP AUX").style.fontSize, s.name_px + "px");
  assert.equal(name("R TIP AUX").textContent, "R TIP");
  assert.equal(name("HEADER").textContent, "HEADER");
  assert.equal(name("L MAIN").textContent, "L MAIN");
});

test("the name scales with the canvas, from the device size", () => {
  const e = editor({ dev: { w: 80, h: 300 }, canvas: { w: 40, h: 150 } });
  assert.equal(e.buildBarGauge(gauge({ name: "AUX" }), true).children[0].style.fontSize, "12px");
});

// --- the picker -----------------------------------------------------------

test("the picker lists each tank with its slot, and binding replaces the plain key and name", () => {
  const e = editor();
  const inst = gauge({ dbkey: "FUELQ3", name: "Tank 3", decimal_places: 1 });
  const field = e.aircraftRefField(inst, "tank", { source: "aircraft.fuel.tanks" }, undefined);
  const sel = field.children[0];
  assert.deepEqual(sel.children.map((o) => o.textContent),
    ["Not bound (plain FIX key)", "L MAIN - FUELQ1", "R MAIN - FUELQ2", "L TIP - FUELQ3"]);
  assert.equal(sel.value, "");
  sel.value = "tk_lt"; sel.fire("change");
  assert.deepEqual(inst.options, { decimal_places: 1, tank: "tk_lt" });
  assert.deepEqual(e.refResolved(inst).options, { decimal_places: 1, dbkey: "FUELQ3", name: "L TIP" });
  assert.deepEqual(e.calls, ["canvas", "props"]);
  // Unbinding keeps what the device was receiving.
  sel.value = ""; sel.fire("change");
  assert.deepEqual(inst.options, { decimal_places: 1, dbkey: "FUELQ3", name: "L TIP" });
});

test("a removed tank stays selected in red under its last label, and the gauge is marked", () => {
  const e = editor({ tanks: THREE.slice(0, 2), retired: { tk_lt: "L TIP" } });
  const inst = gauge({ tank: "tk_lt" });
  const field = e.aircraftRefField(inst, "tank", { source: "aircraft.fuel.tanks" }, "tk_lt");
  const sel = field.children[0];
  assert.equal(sel.value, "tk_lt");
  assert.equal(sel.children.at(-1).textContent, "L TIP - removed");
  assert.ok(sel.classList.contains("ref-orphan"));
  assert.match(field.children[1].textContent, /failed \(xxx\) under its last label/);
  assert.deepEqual(e.tankBinding(inst), { id: "tk_lt", item: null, orphan: true, label: "L TIP" });
  const el = new El("div");
  e.markTankBinding(el, inst);
  assert.ok(el.classList.contains("orphan"));
  assert.match(el.title, /"L TIP": its tank is no longer on the aircraft page/);
  assert.deepEqual(e.refResolved(inst).options, { name: "L TIP", unbound: true }, "the twin draws it failed");
});

test("no tanks on the aircraft page: the picker says so and is disabled", () => {
  const e = editor({ tanks: [] });
  const sel = e.aircraftRefField(gauge({}), "tank", { source: "aircraft.fuel.tanks" }, undefined).children[0];
  assert.equal(sel.children[0].textContent, "No tanks on the aircraft page");
  assert.equal(sel.disabled, true);
});

test("a bound gauge's dbkey and name are written by the binding, not typed", () => {
  const e = editor();
  assert.equal(e.refWrites(gauge({ tank: "tk_lt" }), "dbkey"), "tank");
  assert.equal(e.refWrites(gauge({ tank: "tk_lt" }), "name"), "tank");
  assert.equal(e.refWrites(gauge({ tank: "tk_lt" }), "decimal_places"), null);
  assert.equal(e.refWrites(gauge({ dbkey: "FUELQ3" }), "dbkey"), null);
});

// --- profile bands ----------------------------------------------------------

test("a bound gauge keeps sample bands and says its bands come from the device", () => {
  const e = editor();
  assert.equal(e.profileBandsOnDevice(), false, "aircraft.ini is not delivered yet (Phase C)");
  const el = new El("div"); el.classList.add("sel");
  e.markTankBinding(el, gauge({ tank: "tk_lt" }));
  assert.ok(!el.classList.contains("orphan"));
  assert.equal(el.children[0].textContent, "bands from the device's fix-gateway");
  const unselected = new El("div");
  e.markTankBinding(unselected, gauge({ tank: "tk_lt" }));
  assert.equal(unselected.children.length, 0, "only on the selected gauge, never over a twin");
  const plain = new El("div"); plain.classList.add("sel");
  e.markTankBinding(plain, gauge({ dbkey: "FUELQ3", name: "Tank 3" }));
  assert.equal(plain.children.length, 0, "a plain key gets no note");
  const field = e.aircraftRefField(gauge({ tank: "tk_lt" }), "tank", { source: "aircraft.fuel.tanks" }, "tk_lt");
  assert.match(field.children[1].textContent, /from its own fix-gateway until it receives the aircraft's aircraft\.ini/);
});

// --- the stock-bar migration ------------------------------------------------

test("the migration is offered only for an untouched stock bar whose slot the aircraft has", () => {
  const e = editor();
  const offered = (o, type) => (e.migrationTarget(gauge(o, type)) || {}).label || null;
  assert.equal(offered({ dbkey: "FUELQ3", name: "Tank 3" }), "L TIP");
  assert.equal(offered({ dbkey: "FUELQ1", name: "Tank 1" }, "horizontal_bar_gauge"), "L MAIN");
  assert.equal(offered({ dbkey: "FUELQ3", name: "Fuel 3" }), null, "a hand-edited name");
  assert.equal(offered({ dbkey: "FUELQ3", name: "tank 3" }), null, "a hand-edited name");
  assert.equal(offered({ dbkey: "FUELQ3", name: "Tank 2" }), null, "name and key disagree");
  assert.equal(offered({ dbkey: "FUELQ4", name: "Tank 4" }), null, "no tank in slot 4");
  assert.equal(offered({ dbkey: "FUELQT", name: "Total" }), null);
  assert.equal(offered({ tank: "tk_lm", dbkey: "FUELQ3", name: "Tank 3" }), null, "already bound");
  assert.equal(offered({ dbkey: "FUELQ3", name: "Tank 3" }, "numeric_display"), null);
});

test("the offer changes nothing until clicked, and previews the label in the twin first", () => {
  const e = editor();
  const inst = gauge({ dbkey: "FUELQ3", name: "Tank 3" });
  const box = e.migrationOffer(inst);
  const btn = box.find((x) => x.tagName === "BUTTON");
  assert.equal(btn.textContent, "Bind to L TIP");
  assert.match(box.children[0].textContent, /reads FUELQ3, which the aircraft page calls L TIP/);
  btn.fire("mouseenter");
  assert.deepEqual(inst.options, { dbkey: "FUELQ3", name: "Tank 3" }, "hover is not acceptance");
  assert.equal(e.refResolved(inst).options.name, "L TIP", "the twin shows the new label");
  const el = new El("div"); e.markTankBinding(el, inst);
  assert.ok(el.classList.contains("migrating"));
  btn.fire("mouseleave");
  assert.equal(e.refResolved(inst).options.name, "Tank 3");
  btn.fire("click");
  assert.deepEqual(inst.options, { tank: "tk_lt" });
  assert.equal(e.state.migrationPreview, null);
  assert.equal(e.migrationOffer(inst), null, "offered once");
});

test("both property panels carry the offer, and the editor loads the label module", () => {
  assert.equal((src.match(/const offer = migrationOffer\(inst\);\n\s+if \(offer\) p\.appendChild\(offer\);/g) || []).length, 2);
  assert.match(src, /state\.fuelLabel = await import\("\/fuel_label\.js"\)/);
  assert.match(extractFunction(src, "optionField"), /spec\.type === "aircraft_ref"/);
  assert.match(extractFunction(src, "buildInstrumentTwin"), /markTankBinding\(el, stored\)/);
});
