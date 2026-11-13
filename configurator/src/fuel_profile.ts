// SPDX-License-Identifier: AGPL-3.0-or-later
// The fuel-system provider, `meta.fuel` schema 2: tanks with stable ids,
// labels, slots, feed edges, capacity and unusable fuel
// (docs/fuel_provider_model.md section 2).
//
// fuelCheck() has a copy in public/aircraft.html (the page cannot load .ts);
// test/fuel_profile.test.mjs holds the two equal, message for message, over
// one table of profiles. Change both or neither.
//
// Pure; its one import is the label rule the page and the editor twin share
// (public/fuel_label.js), so node:test can load this file directly.

import { labelFitError } from "../public/fuel_label.js";

export const FUEL_ROLES = ["main", "aux", "tip", "header", "transfer"];
// The UI's tank ceiling: a product choice, not a FIX limit (brief section 3).
export const TANK_CEILING = 8;
// A ceiling on the text box only. The limit that matters is that the label
// fits every stock pyEfis tank bar, as measured off the real widget
// (labelFitError, brief section 6); no 13-character label passes that.
export const TANK_LABEL_MAX = 12;

type Msgs = { errors: string[]; warnings: string[] };

// Every rule meta.fuel breaks. errors refuse the save (422); warnings are
// shown on the page and still save. A missing meta.fuel, or a schema-1
// positional array from a page loaded before schema 2, is not checked: the
// page migrates it on its next load.
export function fuelCheck(meta: any, values: any): Msgs {
  const errors: string[] = [], warnings: string[] = [];
  const fuel = meta && meta.fuel;
  if (fuel === undefined || Array.isArray(fuel)) return { errors, warnings };
  if (!fuel || typeof fuel !== "object" || fuel.schema !== 2 || !Array.isArray(fuel.tanks)) {
    errors.push("meta.fuel must be a schema-2 tank list");
    return { errors, warnings };
  }
  const tanks = fuel.tanks;
  const engines = Number.isInteger(meta.engines) ? meta.engines : 1;
  const V = values && typeof values === "object" ? values : {};
  if (tanks.length > TANK_CEILING) errors.push(`at most ${TANK_CEILING} fuel tanks`);
  const name = (t: any, i: number) =>
    t && typeof t.label === "string" && t.label.trim() ? t.label.trim() : `tank ${i + 1}`;
  const ids = new Map(), slots = new Map(), labels = new Map();
  tanks.forEach((t: any, i: number) => {
    if (!t || typeof t !== "object") { errors.push(`tank ${i + 1}: must be an object`); return; }
    const n = name(t, i);
    if (typeof t.id !== "string" || !/^[A-Za-z0-9_-]{1,32}$/.test(t.id))
      errors.push(`${n}: id must be 1-32 letters, digits, _ or -`);
    else if (ids.has(t.id)) errors.push(`${n}: duplicate id ${t.id}`);
    else ids.set(t.id, i);
    if (typeof t.label !== "string" || !t.label.trim()) errors.push(`tank ${i + 1}: label required`);
    else if (t.label.length > TANK_LABEL_MAX)
      errors.push(`${n}: label at most ${TANK_LABEL_MAX} characters`);
    else if (labelFitError(t.label)) errors.push(`${n}: ${labelFitError(t.label)}`);
    else {
      const key = t.label.trim().toUpperCase();
      if (labels.has(key)) errors.push(`${n}: label used by another tank`);
      else labels.set(key, i);
    }
    if (!FUEL_ROLES.includes(t.role)) errors.push(`${n}: role must be one of ${FUEL_ROLES.join(", ")}`);
    if (!Number.isInteger(t.slot) || t.slot < 1 || t.slot > TANK_CEILING)
      errors.push(`${n}: slot must be 1-${TANK_CEILING}`);
    else if (slots.has(t.slot)) errors.push(`${n}: slot ${t.slot} (FUELQ${t.slot}) used by another tank`);
    else slots.set(t.slot, i);
    const cap = t.capacity, unu = t.unusable == null ? 0 : t.unusable;
    const capSet = cap !== null && cap !== undefined;
    if (capSet && (typeof cap !== "number" || !Number.isFinite(cap) || cap <= 0))
      errors.push(`${n}: capacity must be a number above 0`);
    else if (typeof unu !== "number" || !Number.isFinite(unu) || unu < 0)
      errors.push(`${n}: unusable must be 0 or more`);
    else if (!capSet && unu > 0) errors.push(`${n}: unusable needs a capacity`);
    else if (capSet && unu >= cap) errors.push(`${n}: unusable must be less than capacity`);
    else if (capSet && Number.isInteger(t.slot)) {
      const max = V[`FUELQ${t.slot}.Max`];
      if (typeof max !== "number" || Math.abs(max - (cap - unu)) > 1e-6)
        errors.push(`${n}: FUELQ${t.slot}.Max must be capacity - unusable (${cap - unu})`);
    }
  });
  // Edges, once every id is known.
  const fed = new Set(), out = new Map();
  tanks.forEach((t: any, i: number) => {
    if (!t || typeof t !== "object") return;
    const n = name(t, i), tankEdges: number[] = [];
    if (!Array.isArray(t.feeds)) { errors.push(`${n}: feeds must be a list`); return; }
    for (const e of t.feeds) {
      if (e && e.to === "engine" && Number.isInteger(e.n) && e.n >= 1 && e.n <= engines) fed.add(e.n);
      else if (e && e.to === "engine") errors.push(`${n}: feeds engine ${e.n}, which does not exist`);
      else if (e && e.to === "tank" && e.id === t.id) errors.push(`${n}: a tank cannot feed itself`);
      else if (e && e.to === "tank" && ids.has(e.id)) tankEdges.push(ids.get(e.id));
      else if (e && e.to === "tank") errors.push(`${n}: feeds a tank that does not exist`);
      else errors.push(`${n}: a feed must go to an engine or a tank`);
    }
    out.set(i, tankEdges);
    const toEngine = t.feeds.some((e: any) => e && e.to === "engine");
    if (t.role === "header" && !toEngine) warnings.push(`${n}: a header tank should feed an engine`);
    if ((t.role === "tip" || t.role === "transfer") && !tankEdges.length)
      warnings.push(`${n}: a ${t.role} tank should feed another tank`);
  });
  if (tanks.length)
    for (let e = 1; e <= engines; e++)
      if (!fed.has(e)) warnings.push(`engine ${e} is fed by no tank`);
  // Spec 6b: a transfer cycle is flagged, not refused. Report the first one.
  const state = new Map(), path: number[] = [];
  let cycle: number[] | null = null;
  const visit = (i: number) => {
    state.set(i, 1); path.push(i);
    for (const j of out.get(i) || []) {
      if (cycle) break;
      if (state.get(j) === 1) cycle = path.slice(path.indexOf(j)).concat(j);
      else if (!state.has(j)) visit(j);
    }
    path.pop(); state.set(i, 2);
  };
  for (const i of out.keys()) if (!cycle && !state.has(i)) visit(i);
  if (cycle) warnings.push(`tank feeds form a cycle: ${(cycle as number[]).map(i => name(tanks[i], i)).join(" -> ")}`);
  // Values for a slot no tank holds still compile into aircraft.ini.
  const orphan = new Set<string>();
  for (const k of Object.keys(V)) {
    const m = /^FUELQ(\d+)\./.exec(k);
    if (m && !slots.has(Number(m[1]))) orphan.add(`FUELQ${m[1]}`);
  }
  for (const k of [...orphan].sort())
    warnings.push(`${k} values belong to no tank but still go into aircraft.ini`);
  return { errors, warnings };
}
