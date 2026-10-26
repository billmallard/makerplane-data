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
// Pure functions, no imports, so node:test can load this file directly.

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

const isEmpty = (p: DevicePreferences) => !p.units || Object.keys(p.units).length === 0;

async function shortHash(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf).slice(0, 6), (b) => b.toString(16).padStart(2, "0")).join("");
}

// What GET /device/config serves: the stored panel YAML, plus a preferences
// block when the profile sets one, and the ETag covering BOTH.
//
// * The panel text is never re-serialised -- the block is appended as text, so
//   the user's YAML reaches the device byte-for-byte (a js-yaml load/dump
//   round trip would re-type scalars under YAML 1.2 rules that PyYAML reads as
//   1.1, e.g. `1e3`).
// * No preferences -> ETag stays `"v<n>"`, exactly as before this change, so
//   deploying it re-pulls no device whose profile does not set a unit.
// * With preferences -> `"v<n>-p<hash>"`, the hash over the block's content
//   (not the profile version), so editing an unrelated profile value does not
//   make every device reinstall and restart pyEfis, but changing the unit does.
// * A blob that already has its own top-level `preferences:` key is served
//   unchanged (a duplicate key would be resolved silently by PyYAML).
export async function serveDeviceConfig(
  panelYaml: string,
  version: number | string,
  profile: unknown,
): Promise<{ body: string; etag: string }> {
  const prefs = devicePreferences(profile);
  if (isEmpty(prefs) || /^preferences\s*:/m.test(panelYaml)) {
    return { body: panelYaml, etag: `"v${version}"` };
  }
  const block = JSON.stringify(prefs);
  const body = panelYaml.replace(/\s*$/, "\n")
    + "# Aircraft preferences from the configurator's aircraft profile (not part\n"
    + "# of the panel design); pyefis-data config-pull merges these into\n"
    + "# preferences.yaml.custom.\n"
    + "preferences:\n  units:\n"
    + Object.entries(prefs.units!).map(([g, u]) => `    ${g}: ${u}\n`).join("");
  return { body, etag: `"v${version}-p${await shortHash(block)}"` };
}
