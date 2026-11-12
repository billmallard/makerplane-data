// SPDX-License-Identifier: AGPL-3.0-or-later
// Device preferences derived from the project's aircraft profile (#108).
//
// The aircraft profile is per PROJECT; the panel YAML is per DEVICE. The few
// profile settings the device itself must honour at boot (today only the
// altimeter-setting unit) are delivered by appending a top-level
// `preferences:` block to the served panel YAML. pyefis-data config-pull
// merges its `units` into the device's preferences.yaml.custom, which pyEfis
// reads as `units: {<unit group>: <unit>}`.
//
// The aircraft's ownship address (P3f, AER-5458) rides the same way, as a
// top-level `fixgw:` block: config-pull writes its one key into
// fix-gateway's preferences.yaml.custom, where the stratux traffic writer
// reads it when its own connection setting is blank.
//
// Pure functions, so node:test can load this file directly.

import { ownshipAddress } from "./ownship.ts";

export const BARO_UNITS = ["inHg", "hPa"] as const;
export type BaroUnit = (typeof BARO_UNITS)[number];

export interface DevicePreferences {
  units?: { Pressure?: BaroUnit };
}

// The device-relevant subset of an aircraft profile (a JSON string from D1, or
// an already-parsed object). Never throws: a missing/unparseable profile or an
// unknown value yields {} -- "the configurator does not manage this", which the
// device reads as "remove only what config-pull itself set earlier".
export function devicePreferences(profile: unknown): DevicePreferences {
  let doc: unknown = profile;
  if (typeof doc === "string") {
    try { doc = JSON.parse(doc); } catch { return {}; }
  }
  if (!doc || typeof doc !== "object") return {};
  const meta = (doc as { meta?: unknown }).meta;
  if (!meta || typeof meta !== "object") return {};
  const baro = (meta as { baro_unit?: unknown }).baro_unit;
  if (typeof baro !== "string" || !(BARO_UNITS as readonly string[]).includes(baro)) return {};
  return { units: { Pressure: baro as BaroUnit } };
}

// The fix-gateway settings derived from the profile: today only the traffic
// writer's ownship_address (six upper-case hex digits). {} when the profile
// sets no registration or address -- never throws.
export interface DeviceFixgw {
  ownship_address?: string;
}

export function deviceFixgw(profile: unknown): DeviceFixgw {
  let doc: unknown = profile;
  if (typeof doc === "string") {
    try { doc = JSON.parse(doc); } catch { return {}; }
  }
  if (!doc || typeof doc !== "object") return {};
  const addr = ownshipAddress((doc as { meta?: unknown }).meta);
  return addr === null ? {} : { ownship_address: addr };
}

const isEmpty = (p: DevicePreferences) => !p.units || Object.keys(p.units).length === 0;

async function shortHash(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf).slice(0, 6), (b) => b.toString(16).padStart(2, "0")).join("");
}

// What GET /device/config serves: the stored panel YAML, plus a preferences
// block and a fixgw block when the profile sets them, and the ETag covering
// all of it.
//
// * The panel text is never re-serialised -- the blocks are appended as text,
//   so the user's YAML reaches the device byte-for-byte (a js-yaml load/dump
//   round trip would re-type scalars under YAML 1.2 rules that PyYAML reads as
//   1.1, e.g. `1e3`).
// * Neither block -> ETag stays `"v<n>"`, exactly as before #108, so
//   deploying it re-pulls no device whose profile does not set one.
// * Otherwise -> `"v<n>-p<hash>"`, the hash over the blocks' content (not the
//   profile version), so editing an unrelated profile value does not make
//   every device reinstall and restart pyEfis, but changing the unit or the
//   address does. A preferences-only hash is the one #108 served, so adding
//   the fixgw block re-pulls no device whose profile sets no address.
// * A blob that already has its own top-level `preferences:` or `fixgw:` key
//   is served without that block (a duplicate key would be resolved silently
//   by PyYAML).
export async function serveDeviceConfig(
  panelYaml: string,
  version: number | string,
  profile: unknown,
): Promise<{ body: string; etag: string }> {
  let prefs = devicePreferences(profile);
  let fixgw = deviceFixgw(profile);
  if (/^preferences\s*:/m.test(panelYaml)) prefs = {};
  if (/^fixgw\s*:/m.test(panelYaml)) fixgw = {};
  const withPrefs = !isEmpty(prefs), withFixgw = fixgw.ownship_address !== undefined;
  if (!withPrefs && !withFixgw) {
    return { body: panelYaml, etag: `"v${version}"` };
  }
  let body = panelYaml.replace(/\s*$/, "\n");
  if (withPrefs) {
    body += "# Aircraft preferences from the configurator's aircraft profile (not part\n"
      + "# of the panel design); pyefis-data config-pull merges these into\n"
      + "# preferences.yaml.custom.\n"
      + "preferences:\n  units:\n"
      + Object.entries(prefs.units!).map(([g, u]) => `    ${g}: ${u}\n`).join("");
  }
  if (withFixgw) {
    // Quoted: fix-gateway refuses an unquoted address (123456 loads as an int).
    body += "# fix-gateway settings from the aircraft profile; pyefis-data config-pull\n"
      + "# writes them into fix-gateway's preferences.yaml.custom.\n"
      + "fixgw:\n"
      + `  ownship_address: "${fixgw.ownship_address}"\n`;
  }
  const block = withFixgw ? JSON.stringify({ preferences: prefs, fixgw }) : JSON.stringify(prefs);
  return { body, etag: `"v${version}-p${await shortHash(block)}"` };
}
