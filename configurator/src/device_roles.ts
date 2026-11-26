// SPDX-License-Identifier: AGPL-3.0-or-later
// Display roles per device (DSP1, AER-6686).
//
// pyEfis reads two `main:` keys that only make sense across the whole
// aircraft: `nodeID` (replaces {id} in touchscreen-button and HMI keys, so it
// must be unique among displays on one fix-gateway) and `AuralEnabled` (CAS4,
// pyEfis #567: the alert tones play on every host that has it on, so two
// displays into one intercom play every tone twice). A device's panel YAML
// cannot know its siblings, so the roles are computed per project at serve
// time and written into the served `main:` block; pyefis-data config-pull
// sets them over the device's own main config.
//
// Each device may override either role (the `aural` and `node_id` columns,
// migrations/0003_device_roles.sql). NULL means "automatic":
// * nodeID: an explicit number is kept; the automatic ones take the smallest
//   unused numbers, in creation (id) order.
// * AuralEnabled: if no device is switched on explicitly, the first
//   automatic device (lowest id) plays the tones and the other automatic ones
//   do not. One explicit "on" makes every automatic device silent, so moving
//   the tones is one switch.
// A one-device project serves only what was set explicitly: with nothing set
// it is served exactly what it was served before DSP1.
//
// Pure functions, so node:test can load this file directly.

// fix-gateway defines node-indexed keys for nodes 1..5 (its
// database/variables.yaml `n: 5`); a button on node 6 has no key.
export const MAX_NODE = 5;

export interface DeviceRoleRow {
  id: number;
  aural: number | null;   // 1 on, 0 off, NULL automatic
  node_id: number | null; // NULL automatic
}

export interface MainRoles {
  nodeID?: number;
  AuralEnabled?: boolean;
}

// Every device's effective roles (what the dashboard shows), keyed by id.
export function effectiveRoles(rows: DeviceRoleRow[]): Map<number, Required<MainRoles>> {
  const sorted = [...rows].sort((a, b) => a.id - b.id);
  const used = new Set(sorted.filter((r) => r.node_id != null).map((r) => r.node_id!));
  const anyOn = sorted.some((r) => r.aural === 1);
  const host = anyOn ? undefined : sorted.find((r) => r.aural == null)?.id;
  const out = new Map<number, Required<MainRoles>>();
  for (const r of sorted) {
    let node = r.node_id;
    if (node == null) {
      node = 1;
      while (used.has(node)) node++;
      used.add(node);
    }
    out.set(r.id, { nodeID: node, AuralEnabled: r.aural === 1 || (r.aural == null && r.id === host) });
  }
  return out;
}

// The roles served to one device: all of them in a project of two or more,
// only the explicit ones in a project of one. {} for an unknown id.
export function servedRoles(rows: DeviceRoleRow[], deviceId: number): MainRoles {
  const me = rows.find((r) => r.id === deviceId);
  if (!me) return {};
  if (rows.length > 1) return effectiveRoles(rows).get(deviceId) ?? {};
  const out: MainRoles = {};
  if (me.node_id != null) out.nodeID = me.node_id;
  if (me.aural != null) out.AuralEnabled = me.aural === 1;
  return out;
}

// Write `roles` into the panel YAML's top-level block `main:` as text (the
// panel is never re-serialised; see serveDeviceConfig). A key the panel's
// `main:` already sets is left to the panel. A panel with no `main:` gets one
// appended; a `main:` in flow style is left alone. Returns the YAML and the
// roles actually written.
export function injectMainRoles(panelYaml: string, roles: MainRoles): { yaml: string; roles: MainRoles } {
  const entries = Object.entries(roles) as [keyof MainRoles, number | boolean][];
  if (!entries.length) return { yaml: panelYaml, roles: {} };
  const lines = panelYaml.split("\n");
  const at = lines.findIndex((l) => /^main[ \t]*:/.test(l));
  if (at < 0) {
    const body = panelYaml.replace(/\s*$/, "\n");
    return { yaml: body + "main:\n" + entries.map(([k, v]) => `  ${k}: ${v}\n`).join(""), roles };
  }
  if (!/^main[ \t]*:[ \t]*(#.*)?$/.test(lines[at])) return { yaml: panelYaml, roles: {} };
  let end = at + 1;
  let indent = "";
  for (; end < lines.length; end++) {
    const l = lines[end];
    if (!l.trim() || /^[ \t]*#/.test(l)) continue;
    const m = /^([ \t]+)/.exec(l);
    if (!m) break;                       // next top-level key
    if (!indent) indent = m[1];
  }
  indent = indent || "  ";
  const block = lines.slice(at + 1, end);
  const written: MainRoles = {};
  const add: string[] = [];
  for (const [k, v] of entries) {
    if (block.some((l) => new RegExp(`^${indent}${k}[ \\t]*:`).test(l))) continue;
    add.push(`${indent}${k}: ${v}`);
    (written as Record<string, unknown>)[k] = v;
  }
  lines.splice(at + 1, 0, ...add);
  return { yaml: lines.join("\n"), roles: written };
}
