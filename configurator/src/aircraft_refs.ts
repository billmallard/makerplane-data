// SPDX-License-Identifier: AGPL-3.0-or-later
// resolveAircraftRefs: the served panel YAML with every aircraft reference
// resolved against the active profile (docs/fuel_provider_model.md section
// 5, row F3). The resolution itself is public/aircraft_refs.js, which the
// editor preview loads too; this file only adds the YAML around it.
//
// Imported by device_prefs.ts, so node:test can load it directly.

import yaml from "js-yaml";
import { FALLBACK_REFS, itemsBySource, resolveDoc, type RefTable } from "../public/aircraft_refs.js";

export type Binding = Record<string, unknown> & { at: string; option: string; id: string };

// A reference option at the start of a YAML key, in block or flow style.
// The names come from schema.json, so they are escaped.
const mention = (refs: RefTable) => new RegExp(
  `(^|[\\s{,])(${Object.keys(refs).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\s*:`, "m");

// * A panel that mentions no reference option is returned as the same text,
//   unparsed, so a panel with no references reaches the device byte for byte
//   as it did before references existed.
// * Otherwise the panel is parsed, resolved and dumped the way the editor
//   saves it (js-yaml, lineWidth 100, noRefs). The editor writes every
//   stored panel with that same dump, so the round trip does not retype a
//   panel the editor saved.
// * A panel that does not parse is returned unchanged: the device's own
//   validation judges it.
// * `retired` maps an id the profile no longer has to its last label.
// * `refs` is the reference table the served schema.json declares
//   (refsFromSchema); FALLBACK_REFS when the schema could not be read.
export function resolveAircraftRefs(
  panelYaml: string,
  profile: unknown,
  retired: Record<string, string> = {},
  refs: RefTable = FALLBACK_REFS,
): { yaml: string; bindings: Binding[] } {
  if (!Object.keys(refs).length || !mention(refs).test(panelYaml)) return { yaml: panelYaml, bindings: [] };
  let doc: unknown;
  try { doc = yaml.load(panelYaml); } catch { return { yaml: panelYaml, bindings: [] }; }
  const r = resolveDoc(doc, itemsBySource(profile, refs), retired, refs);
  // No binding (only empty references): serve the stored text, so the body
  // changes exactly when the ETag's bindings do. pyEfis skips an empty one.
  if (!r.bindings.length) return { yaml: panelYaml, bindings: [] };
  // The dump drops comments; keep the leading block, which carries the
  // editor's layout advisory (LAYADV1), so the device still receives it.
  const head = /^(?:[ \t]*(?:#[^\n]*)?\n)*/.exec(panelYaml)![0];
  return {
    yaml: head + yaml.dump(r.doc, { lineWidth: 100, noRefs: true }),
    bindings: r.bindings as Binding[],
  };
}

export type Orphan = { at: string; option: string; id: string; label: string | null };

// The references in one panel that resolve under the `before` profile and
// come out unbound under `after`: the gauges a profile change would orphan
// (brief section 5). A reference already unbound before is not counted, and
// nothing is ever refused here -- removing a tank is legitimate; the caller
// only warns.
export function orphanedBy(
  panelYaml: string, before: unknown, after: unknown, refs: RefTable = FALLBACK_REFS,
): Orphan[] {
  const was = resolveAircraftRefs(panelYaml, before, {}, refs).bindings;
  const now = new Map(resolveAircraftRefs(panelYaml, after, {}, refs).bindings.map((b) => [`${b.at}|${b.option}`, b]));
  const out: Orphan[] = [];
  for (const b of was) {
    if (b.unbound) continue;
    const n = now.get(`${b.at}|${b.option}`);
    if (n && n.unbound)
      out.push({ at: b.at, option: b.option, id: b.id, label: typeof b.name === "string" ? b.name : null });
  }
  return out;
}
