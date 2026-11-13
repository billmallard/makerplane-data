// SPDX-License-Identifier: AGPL-3.0-or-later
// resolveAircraftRefs: the served panel YAML with every aircraft reference
// resolved against the active profile (docs/fuel_provider_model.md section
// 5, row F3). The resolution itself is public/aircraft_refs.js, which the
// editor preview loads too; this file only adds the YAML around it.
//
// Imported by device_prefs.ts, so node:test can load it directly.

import yaml from "js-yaml";
import { AIRCRAFT_REFS, itemsBySource, resolveDoc } from "../public/aircraft_refs.js";

export type Binding = Record<string, unknown> & { at: string; option: string; id: string };

// A reference option at the start of a YAML key, in block or flow style.
const MENTION = new RegExp(`(^|[\\s{,])(${Object.keys(AIRCRAFT_REFS).join("|")})\\s*:`, "m");

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
export function resolveAircraftRefs(
  panelYaml: string,
  profile: unknown,
  retired: Record<string, string> = {},
): { yaml: string; bindings: Binding[] } {
  if (!MENTION.test(panelYaml)) return { yaml: panelYaml, bindings: [] };
  let doc: unknown;
  try { doc = yaml.load(panelYaml); } catch { return { yaml: panelYaml, bindings: [] }; }
  const r = resolveDoc(doc, itemsBySource(profile), retired);
  // No binding (only empty references): serve the stored text, so the body
  // changes exactly when the ETag's bindings do. pyEfis skips an empty one.
  if (!r.bindings.length) return { yaml: panelYaml, bindings: [] };
  return {
    yaml: yaml.dump(r.doc, { lineWidth: 100, noRefs: true }),
    bindings: r.bindings as Binding[],
  };
}
