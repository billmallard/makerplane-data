// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// INS4 (AER-6587): check the editor's twins against a pyEfis schema.json.
// The twins restate some of what the schema says (an enum, a colour default,
// which types draw a live preview), and nothing else compares the two. This
// runs in pyEfis's CI against the schema it has just built, so a pyEfis
// change that leaves a twin behind goes red there, before it ships:
//
//   node configurator/tools/schema_twin_check.mjs path/to/schema.json [--strict]
//
// Exit 1 on any error, or on any warning with --strict. Node built-ins only,
// so pyEfis CI needs no npm install. It reads the REAL public/editor.html
// (test/support/extract.mjs), never a copy of its constants.
//
// Errors (a twin disagrees with the schema):
//   * schema_version missing, or newer than the editor's SCHEMA_VERSION_MAX;
//   * a twin constant that restates a spec enum or default and differs:
//     FD_STYLES (fd_style), _ALIGN_JUSTIFY (static_text alignment),
//     AP_COLORS (autopilot_panel), AP_STRIP_COLORS (ap_annunciator),
//     VALUE_TEXT_DEFAULTS (value_text), and a TEXT_DEFAULTS font where the
//     schema states a per-type font_family default;
//   * a type the schema declares with no twin that is not on PLACEHOLDER_ONLY,
//     or a PLACEHOLDER_ONLY type that has a twin;
//   * an aircraft_ref the resolver cannot resolve, or one option declared
//     two ways (public/aircraft_refs.js refsFromSchema).
// Warnings (worth fixing, not yet a mismatch; --strict fails on them):
//   * no `pyefis_commit` stamp;
//   * a TEXT_DEFAULTS font that differs from the common default where the
//     schema states no per-type one.
// Notes (never fail): a PLACEHOLDER_ONLY or twinned type, or an option a
// twin restates, that this schema does not declare. The editor may be ahead
// of an older pyEfis branch (qa and main lag dev); that is not drift.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { REF_SOURCE_FIELDS, refsFromSchema } from "../public/aircraft_refs.js";
import { editorSource, extractConst, extractFunction } from "../test/support/extract.mjs";

// The types that deliberately draw their palette picture (the static
// thumbnail) instead of a live twin. A new type must either get a twin or be
// added here, so a type never lands in the static state unnoticed.
export const PLACEHOLDER_ONLY = [
  "about", "checklist", "data_status", "flight_plan", "listbox", "nav_status", "weston",
];

const CONSTS = ["FD_STYLES", "_ALIGN_JUSTIFY", "AP_COLORS", "AP_STRIP_COLORS", "VALUE_TEXT_DEFAULTS",
  "TEXT_DEFAULTS", "LIVE_TEXT", "LIVE_GAUGE", "SCHEMA_VERSION_MAX"];

function editorConsts(src) {
  const body = CONSTS.map((n) => extractConst(src, n)).join("\n")
    + `\n${extractFunction(src, "textJustify")}\nreturn { ${CONSTS.join(", ")}, textJustify };`;
  return new Function(body)();
}

// The instrument types buildInstrumentTwin draws a live twin for: each
// `inst.type === "x"` branch and each `SET.has(inst.type)` set it tests.
// Containers (a type whose schema meta lists containers) draw one too.
export function twinTypes(src, schema) {
  const fn = extractFunction(src, "buildInstrumentTwin");
  const out = new Set();
  for (const m of fn.matchAll(/inst\.type === "([a-z0-9_]+)"/g)) out.add(m[1]);
  for (const m of fn.matchAll(/\b([A-Z_]+)\.has\(inst\.type\)/g))
    for (const t of new Function(`${extractConst(src, m[1])}\nreturn [...${m[1]}];`)()) out.add(t);
  out.delete("group");  // an editor element group, not a pyEfis type
  for (const [t, meta] of Object.entries(schema.instruments || {}))
    if (meta && Array.isArray(meta.containers) && meta.containers.length) out.add(t);
  return out;
}

const lc = (v) => (typeof v === "string" ? v.toLowerCase() : v);
const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

export function checkSchemaTwins(schema, src = editorSource()) {
  const errors = [], warnings = [], notes = [];
  const I = (schema && schema.instruments) || {};
  const opt = (type, name) => (I[type] && I[type].options && I[type].options[name]) || null;
  const C = editorConsts(src);

  // --- version and provenance ---
  const v = schema && schema.schema_version;
  if (!Number.isInteger(v)) errors.push("schema_version: missing or not an integer");
  else if (v > C.SCHEMA_VERSION_MAX)
    errors.push(`schema_version ${v} is newer than the editor's SCHEMA_VERSION_MAX ${C.SCHEMA_VERSION_MAX}: teach the editor v${v} first`);
  if (!(schema && typeof schema.pyefis_commit === "string" && /^[0-9a-f]{40}$/.test(schema.pyefis_commit)))
    warnings.push("pyefis_commit: schema.json does not say which pyEfis commit built it");

  // --- enums ---
  const fdTypes = Object.keys(I).filter((t) => opt(t, "fd_style")).sort();
  if (!fdTypes.length) notes.push("fd_style: no type declares it; FD_STYLES not checked");
  for (const t of fdTypes) {
    const f = opt(t, "fd_style");
    if (!sameSet(C.FD_STYLES, f.enum || []))
      errors.push(`${t}.fd_style enum ${JSON.stringify(f.enum)} != FD_STYLES ${JSON.stringify(C.FD_STYLES)}`);
    if (f.default !== C.FD_STYLES[0])
      errors.push(`${t}.fd_style default ${JSON.stringify(f.default)} != FD_STYLES[0] ${JSON.stringify(C.FD_STYLES[0])}`);
  }
  const al = opt("static_text", "alignment");
  if (!al) notes.push("static_text.alignment: not declared; _ALIGN_JUSTIFY not checked");
  else {
    if (!sameSet(Object.keys(C._ALIGN_JUSTIFY), al.enum || []))
      errors.push(`static_text.alignment enum ${JSON.stringify(al.enum)} != _ALIGN_JUSTIFY keys ${JSON.stringify(Object.keys(C._ALIGN_JUSTIFY))}`);
    const unset = C.textJustify({ type: "static_text", options: {} });
    const dflt = C.textJustify({ type: "static_text", options: { alignment: al.default } });
    if (unset !== dflt)
      errors.push(`static_text.alignment default ${JSON.stringify(al.default)} previews ${dflt}, but an unset one previews ${unset}`);
  }

  // --- defaults the twin restates ---
  const sameDefaults = (name, type, table, pick) => {
    if (!I[type]) { notes.push(`${type}: not declared; ${name} not checked`); return; }
    const want = {};
    for (const [k, f] of Object.entries(I[type].options || {})) if (pick(k, f) && f.default != null) want[k] = f.default;
    for (const k of new Set([...Object.keys(want), ...Object.keys(table)])) {
      if (!(k in want)) errors.push(`${name}.${k}: ${type} declares no default for it`);
      else if (!(k in table)) errors.push(`${name}.${k}: missing; ${type} defaults it to ${JSON.stringify(want[k])}`);
      else if (lc(want[k]) !== lc(table[k]))
        errors.push(`${name}.${k} ${JSON.stringify(table[k])} != ${type} default ${JSON.stringify(want[k])}`);
    }
  };
  const isColor = (k, f) => f && f.type === "color";
  sameDefaults("AP_COLORS", "autopilot_panel", C.AP_COLORS, isColor);
  sameDefaults("AP_STRIP_COLORS", "ap_annunciator", C.AP_STRIP_COLORS, isColor);
  sameDefaults("VALUE_TEXT_DEFAULTS", "value_text", C.VALUE_TEXT_DEFAULTS, (k) => k in C.VALUE_TEXT_DEFAULTS);

  // --- text twins: one entry per live text type, its font the type's own ---
  if (!sameSet(Object.keys(C.TEXT_DEFAULTS), [...C.LIVE_TEXT]))
    errors.push(`TEXT_DEFAULTS keys ${JSON.stringify(Object.keys(C.TEXT_DEFAULTS))} != LIVE_TEXT ${JSON.stringify([...C.LIVE_TEXT])}`);
  const common = schema && schema.common_options && schema.common_options.font_family;
  for (const [t, d] of Object.entries(C.TEXT_DEFAULTS)) {
    if (!I[t]) continue;  // reported as a stale twin below
    const own = opt(t, "font_family");
    if (own && own.default != null) {
      if (own.default !== d.font) errors.push(`TEXT_DEFAULTS.${t}.font ${JSON.stringify(d.font)} != ${t}.font_family default ${JSON.stringify(own.default)}`);
    } else if (common && common.default !== d.font) {
      warnings.push(`${t}: the twin's font is ${JSON.stringify(d.font)}, but the schema states no per-type font_family default and the common one is ${JSON.stringify(common.default)}`);
    }
  }

  // --- twin coverage ---
  const twins = twinTypes(src, schema);
  for (const t of Object.keys(I).sort()) {
    if (twins.has(t) && PLACEHOLDER_ONLY.includes(t))
      errors.push(`${t}: has a twin now; take it off PLACEHOLDER_ONLY (tools/schema_twin_check.mjs)`);
    else if (!twins.has(t) && !PLACEHOLDER_ONLY.includes(t))
      errors.push(`${t}: no twin and not on PLACEHOLDER_ONLY: give it a twin, or list it as placeholder-only`);
  }
  for (const t of PLACEHOLDER_ONLY) if (!I[t]) notes.push(`${t}: on PLACEHOLDER_ONLY, but the schema no longer declares it`);
  for (const t of [...twins].sort()) if (!I[t]) notes.push(`${t}: the editor has a twin for it, but the schema no longer declares it`);

  // --- aircraft references ---
  const conflicts = [];
  const refs = refsFromSchema(schema, conflicts) || {};
  for (const c of conflicts) errors.push(`${c.type}.${c.option}: an aircraft_ref declared differently from another type's ${c.option}`);
  for (const [t, meta] of Object.entries(I))
    for (const [k, f] of Object.entries((meta && meta.options) || {})) {
      if (!f || f.type !== "aircraft_ref") continue;
      if (f.multi) errors.push(`${t}.${k}: a multi aircraft_ref; the resolver resolves single references only`);
      else if (!REF_SOURCE_FIELDS[f.source]) errors.push(`${t}.${k}: aircraft_ref source ${JSON.stringify(f.source)} the resolver does not know`);
      else if (!(refs[k] && refs[k].types.includes(t)) && !conflicts.some((c) => c.type === t && c.option === k))
        errors.push(`${t}.${k}: aircraft_ref resolves ${JSON.stringify(f.resolves)}, fields a ${f.source} item does not carry`);
    }

  return { errors, warnings, notes };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const strict = args.includes("--strict");
  const path = args.find((a) => !a.startsWith("--"));
  if (!path) {
    console.error("usage: node tools/schema_twin_check.mjs <schema.json> [--strict]");
    process.exit(2);
  }
  const { errors, warnings, notes } = checkSchemaTwins(JSON.parse(readFileSync(path, "utf8")));
  for (const e of errors) console.log(`error: ${e}`);
  for (const w of warnings) console.log(`warning: ${w}`);
  for (const n of notes) console.log(`note: ${n}`);
  const fail = errors.length > 0 || (strict && warnings.length > 0);
  console.log(`${errors.length} error(s), ${warnings.length} warning(s)${strict ? " (strict)" : ""}: ${fail ? "FAIL" : "ok"}`);
  process.exit(fail ? 1 : 0);
}
