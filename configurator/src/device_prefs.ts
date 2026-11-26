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
// The aircraft's parameter values (ACINI1, AER-5993) ride in the same block
// as `aircraft_ini`, the compiled init_data file (public/aircraft_ini.js, the
// compiler the page's Download button runs), with its `aircraft_ini_sha256`.
// config-pull checks the hash, dry-runs the file through fix-gateway's own
// loader, and installs it as init_data/aircraft.ini, listed last in
// fix-gateway's default.yaml (docs/canfix_configurator.md section 7).
//
// Pure functions, so node:test can load this file directly.

import { ownshipAddress } from "./ownship.ts";
import { resolveAircraftRefs } from "./aircraft_refs.ts";
import { FALLBACK_REFS, type RefTable } from "../public/aircraft_refs.js";
import { aircraftIniLines, buildAircraftIni } from "../public/aircraft_ini.js";
import { injectMainRoles, type MainRoles } from "./device_roles.ts";

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

// The fix-gateway settings derived from the profile: the traffic writer's
// ownship_address (six upper-case hex digits) when the profile sets a
// registration or address, and the compiled aircraft.ini when the profile
// holds at least one value. A profile with no values sends no file, and the
// device keeps whatever it has (an absent key means "not managed", the way
// an absent block always has). {} when neither applies -- never throws.
export interface DeviceFixgw {
  ownship_address?: string;
  aircraft_ini?: string;
}

// The delivered file's header. No date or profile version: either would make
// a save that changes no value a new file, and so a fix-gateway restart.
export const AIRCRAFT_INI_HEADER = [
  "aircraft.ini -- compiled by the pyEfis configurator from this aircraft's profile.",
  "Installed by pyefis-data config-pull, which replaces it on the next pull:",
  "edit the profile, not this file. Values are FIX-database native units.",
  "VERIFY AGAINST YOUR POH.",
];

export function deviceFixgw(profile: unknown): DeviceFixgw {
  let doc: unknown = profile;
  if (typeof doc === "string") {
    try { doc = JSON.parse(doc); } catch { return {}; }
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return {};
  const out: DeviceFixgw = {};
  const addr = ownshipAddress((doc as { meta?: unknown }).meta);
  if (addr !== null) out.ownship_address = addr;
  if (aircraftIniLines(doc).length) out.aircraft_ini = buildAircraftIni(doc, AIRCRAFT_INI_HEADER);
  return out;
}

const isEmpty = (p: DevicePreferences) => !p.units || Object.keys(p.units).length === 0;

async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

const shortHash = async (text: string) => (await sha256Hex(text)).slice(0, 12);

// What GET /device/config serves: the stored panel YAML with its aircraft
// references resolved (aircraft_refs.ts), plus a preferences block and a
// fixgw block when the profile sets them, and the ETag covering all of it.
//
// * A panel with no references is never re-serialised -- the blocks are
//   appended as text, so the user's YAML reaches the device byte-for-byte (a
//   js-yaml load/dump round trip would re-type scalars under YAML 1.2 rules
//   that PyYAML reads as 1.1, e.g. `1e3`).
// * No block and no reference -> ETag stays `"v<n>"`, exactly as before #108.
// * Otherwise -> `"v<n>-p<hash>"`, the hash over the blocks' content and the
//   resolved bindings (not the profile version), so a save that changes
//   nothing the device receives does not make it reinstall and restart, but
//   changing the unit, the address, a referenced tank's label or slot, or a
//   parameter value does. A preferences-only hash is the one #108 served. Since ACINI1 the
//   fixgw block carries the compiled aircraft.ini whenever the profile holds a
//   value, so the hash covers every parameter value: a value change is a
//   new ETag (that is the delivery), and each device whose profile holds values
//   re-pulls once when this ships.
// * `retired` loads the last label of each id the profile no longer has. It
//   is called only when a reference is unbound.
// * `refs` is the reference table from the served schema.json
//   (refsFromSchema); FALLBACK_REFS when it could not be read.
// * A blob that already has its own top-level `preferences:` or `fixgw:` key
//   is served without that block (a duplicate key would be resolved silently
//   by PyYAML).
// * `roles` (DSP1, device_roles.ts) are written into the panel's `main:`
//   block, and the hash covers them, so a device whose node or tone switch
//   changes re-pulls. No roles (a one-device project with nothing set) leaves
//   the body and the ETag exactly as before DSP1.
export async function serveDeviceConfig(
  panelYaml: string,
  version: number | string,
  profile: unknown,
  retired?: () => Promise<Record<string, string>>,
  table: RefTable = FALLBACK_REFS,
  roles: MainRoles = {},
): Promise<{ body: string; etag: string }> {
  let refs = resolveAircraftRefs(panelYaml, profile, {}, table);
  if (retired && refs.bindings.some((b) => b.unbound))
    refs = resolveAircraftRefs(panelYaml, profile, await retired(), table);
  const main = injectMainRoles(refs.yaml, roles);
  const panel = main.yaml;
  const withRoles = Object.keys(main.roles).length > 0;
  let prefs = devicePreferences(profile);
  let fixgw = deviceFixgw(profile);
  if (/^preferences\s*:/m.test(panel)) prefs = {};
  if (/^fixgw\s*:/m.test(panel)) fixgw = {};
  const withPrefs = !isEmpty(prefs);
  const withFixgw = fixgw.ownship_address !== undefined || fixgw.aircraft_ini !== undefined;
  const withRefs = refs.bindings.length > 0;
  if (!withPrefs && !withFixgw && !withRefs && !withRoles) {
    return { body: panel, etag: `"v${version}"` };
  }
  let body = panel.replace(/\s*$/, "\n");
  if (withPrefs) {
    body += "# Aircraft preferences from the configurator's aircraft profile (not part\n"
      + "# of the panel design); pyefis-data config-pull merges these into\n"
      + "# preferences.yaml.custom.\n"
      + "preferences:\n  units:\n"
      + Object.entries(prefs.units!).map(([g, u]) => `    ${g}: ${u}\n`).join("");
  }
  if (withFixgw) {
    body += "# fix-gateway settings from the aircraft profile; pyefis-data config-pull\n"
      + "# writes them into fix-gateway's config.\n"
      + "fixgw:\n";
    // Quoted: fix-gateway refuses an unquoted address (123456 loads as an int).
    if (fixgw.ownship_address !== undefined)
      body += `  ownship_address: "${fixgw.ownship_address}"\n`;
    if (fixgw.aircraft_ini !== undefined) {
      // A literal block: the compiler ends the file in one newline and never a
      // blank line, which is exactly what `|` (clip) reads back.
      body += `  aircraft_ini_sha256: "${await sha256Hex(fixgw.aircraft_ini)}"\n`
        + "  aircraft_ini: |\n" + fixgw.aircraft_ini.split("\n").slice(0, -1)
          .map((l) => (l ? "    " + l : "")).join("\n") + "\n";
    }
  }
  const block = withRoles
    ? JSON.stringify({ preferences: prefs, fixgw, refs: refs.bindings, main: main.roles })
    : withRefs ? JSON.stringify({ preferences: prefs, fixgw, refs: refs.bindings })
    : withFixgw ? JSON.stringify({ preferences: prefs, fixgw }) : JSON.stringify(prefs);
  return { body, etag: `"v${version}-p${await shortHash(block)}"` };
}
