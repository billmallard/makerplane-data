// SPDX-License-Identifier: AGPL-3.0-or-later
// Aircraft references: a panel option that holds the id of an item on the
// aircraft profile (a fuel tank today), resolved to plain pyEfis options at
// serve time (docs/fuel_provider_model.md section 5, row F3).
//
// One module, two callers: the Worker imports it for GET /device/config and
// the served-YAML download (src/aircraft_refs.ts), and the editor imports it
// for the preview. So a pulled config, a downloaded one and the preview
// cannot resolve a reference differently.
//
// Pure, no imports, never throws: a missing or malformed profile resolves
// every reference as unbound.

// The reference options pyEfis declares (field type `aircraft_ref` in its
// schema.json), by option name. test/aircraft_refs.test.mjs pins this table
// to the pyEfis schema fixture; change both or neither.
export const AIRCRAFT_REFS = {
  tank: {
    source: "aircraft.fuel.tanks",
    resolves: { dbkey: "slot_key", name: "label" },
    types: ["arc_gauge", "horizontal_bar_gauge", "vertical_bar_gauge"],
  },
};

const parse = (profile) => {
  let doc = profile;
  if (typeof doc === "string") {
    try { doc = JSON.parse(doc); } catch { return null; }
  }
  return doc && typeof doc === "object" ? doc : null;
};

// The items a source offers on one profile: for aircraft.fuel.tanks,
// [{id, label, slot, role, slot_key}] in page order. A schema-1 profile, a
// tank without a usable id, label or slot, or an unknown source offers
// nothing. The first of two tanks sharing an id wins (the save refuses that).
export function refItems(profile, source) {
  if (source !== "aircraft.fuel.tanks") return [];
  const doc = parse(profile);
  const fuel = doc && doc.meta && doc.meta.fuel;
  if (!fuel || fuel.schema !== 2 || !Array.isArray(fuel.tanks)) return [];
  const out = [], seen = new Set();
  for (const t of fuel.tanks) {
    if (!t || typeof t.id !== "string" || !t.id || seen.has(t.id)) continue;
    if (typeof t.label !== "string" || !t.label.trim()) continue;
    if (!Number.isInteger(t.slot) || t.slot < 1) continue;
    seen.add(t.id);
    out.push({ id: t.id, label: t.label.trim(), slot: t.slot, role: t.role, slot_key: `FUELQ${t.slot}` });
  }
  return out;
}

// The last label each id had, for ids the current profile no longer offers:
// `profiles` is every saved version of the aircraft, newest first. This is
// the label an orphaned gauge keeps, so a pilot can tell which gauge lost its
// tank (brief section 5).
export function retiredLabels(profiles, source, current) {
  const live = new Set(refItems(current, source).map((i) => i.id));
  const out = {};
  for (const p of profiles || [])
    for (const i of refItems(p, source))
      if (!live.has(i.id) && !(i.id in out)) out[i.id] = i.label;
  return out;
}

// Resolve the references in one instrument's options. `items` maps a source
// to its refItems() list; `retired` maps an id to its last label.
//
// * A reference to an item the profile has: the reference is removed and
//   each `resolves` option written from the item (tank -> dbkey: FUELQ<slot>,
//   name: <label>). An `unbound` left over from an earlier serve goes.
// * A reference to an item it does not have: the reference and every key
//   option are removed and `unbound: true` is written, so pyEfis draws its
//   failed-sensor state and never looks up a key (pyEfis#452). The label
//   option keeps the last label known for the id, else the panel's own.
// * An empty reference is no reference: it is removed, nothing else changes.
//
// Returns {options, bindings}; options is the input object when nothing
// applies. Each binding is what the ETag covers.
export function resolveOptions(type, options, items, retired) {
  if (!options || typeof options !== "object") return { options, bindings: [] };
  let out = options;
  const bindings = [];
  for (const [opt, spec] of Object.entries(AIRCRAFT_REFS)) {
    if (!(opt in options) || !spec.types.includes(type)) continue;
    if (out === options) out = { ...options };
    const raw = out[opt];
    delete out[opt];
    if (raw === null || raw === undefined || raw === "") continue;
    const id = typeof raw === "string" || typeof raw === "number" ? String(raw) : "";
    const item = id ? ((items && items[spec.source]) || []).find((i) => i.id === id) : undefined;
    if (item) {
      delete out.unbound;
      for (const [k, field] of Object.entries(spec.resolves)) out[k] = item[field];
      bindings.push({ option: opt, id, ...Object.fromEntries(Object.keys(spec.resolves).map((k) => [k, out[k]])) });
    } else {
      const last = retired && typeof retired[id] === "string" ? retired[id] : null;
      for (const [k, field] of Object.entries(spec.resolves)) {
        if (field === "label" && last !== null) out[k] = last;
        else if (field !== "label") delete out[k];
      }
      out.unbound = true;
      const b = { option: opt, id, unbound: true };
      for (const [k, field] of Object.entries(spec.resolves))
        if (field === "label" && k in out) b[k] = out[k];
      bindings.push(b);
    }
  }
  return { options: out, bindings };
}

// Resolve every instrument of a parsed native panel document
// ({main, screens: {NAME: {instruments: [...]}}}), recursing into container
// slots (an instrument key holding a list of {instruments} pages, as
// tab_section's `tabs`). Returns {doc, bindings}; doc is a copy wherever an
// instrument changed and the input is never mutated. Each binding carries
// `at`, the instrument's path, so two gauges on one tank stay distinct.
export function resolveDoc(doc, items, retired) {
  const bindings = [];
  const walkList = (list, at) => {
    if (!Array.isArray(list)) return list;
    let changed = false;
    const next = list.map((inst, i) => {
      const r = walkInst(inst, `${at}/${i}`);
      if (r !== inst) changed = true;
      return r;
    });
    return changed ? next : list;
  };
  const walkInst = (inst, at) => {
    if (!inst || typeof inst !== "object" || Array.isArray(inst)) return inst;
    let out = inst;
    const r = resolveOptions(inst.type, inst.options, items, retired);
    for (const b of r.bindings) bindings.push({ at, ...b });
    if (r.options !== inst.options) out = { ...inst, options: r.options };
    for (const [k, v] of Object.entries(inst)) {
      if (!Array.isArray(v) || !v.some((p) => p && typeof p === "object" && Array.isArray(p.instruments))) continue;
      const pages = v.map((p, j) => {
        if (!p || typeof p !== "object" || !Array.isArray(p.instruments)) return p;
        const ins = walkList(p.instruments, `${at}/${k}/${j}`);
        return ins === p.instruments ? p : { ...p, instruments: ins };
      });
      if (pages.some((p, j) => p !== v[j])) {
        if (out === inst) out = { ...inst };
        out[k] = pages;
      }
    }
    return out;
  };
  if (!doc || typeof doc !== "object" || !doc.screens || typeof doc.screens !== "object")
    return { doc, bindings };
  let screens = doc.screens;
  for (const name of Object.keys(doc.screens)) {
    const sc = doc.screens[name];
    if (!sc || typeof sc !== "object") continue;
    const ins = walkList(sc.instruments, name);
    if (ins !== sc.instruments) {
      if (screens === doc.screens) screens = { ...doc.screens };
      screens[name] = { ...sc, instruments: ins };
    }
  }
  return { doc: screens === doc.screens ? doc : { ...doc, screens }, bindings };
}

// The sources a panel can reference, and their items, for one profile.
export function itemsBySource(profile) {
  const out = {};
  for (const spec of Object.values(AIRCRAFT_REFS)) out[spec.source] = refItems(profile, spec.source);
  return out;
}
