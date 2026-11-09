// SPDX-License-Identifier: AGPL-3.0-or-later
// Server-side checks on an aircraft profile before it is saved
// (docs/canfix_configurator.md section 5).
//
// Pure functions; the one import is pure too, so node:test can load this file
// directly.

import { ownshipErrors } from "./ownship.ts";

export const MAX_PROFILE_BYTES = 256 * 1024;

export type ProfileCheck =
  | { ok: true; text: string }
  | { ok: false; status: 400 | 413 | 422; error: string; errors?: string[] };

export const SPEED_UNITS = ["kt", "mph", "kph"] as const;
export const BARO_UNITS = ["inHg", "hPa"] as const;

// Section 6 ordering. Each chain is checked over every pair of values present,
// not only neighbours, so a blank in the middle (blank = "leave unset", which
// is allowed) cannot hide an inversion. A pair is strict if any link between
// them is "<".
type Link = "<=" | "<";
const AIRSPEED_CHAIN: [string, Link][] = [
  ["Vs0", "<="], ["Vs", "<"], ["Vno", "<"], ["Vne", "<="], ["Max", "<="],
];
const VFE_CHAIN: [string, Link][] = [["Vs0", "<="], ["Vfe", "<="], ["Vno", "<="]];
// Warns inside alarms inside Min/Max, for every banded key instance.
const BAND_CHAIN: [string, Link][] = [
  ["Min", "<="], ["lowAlarm", "<="], ["lowWarn", "<="],
  ["highWarn", "<="], ["highAlarm", "<="], ["Max", "<="],
];

function chainErrors(values: Record<string, unknown>, inst: string,
                     chain: [string, Link][]): string[] {
  const errs: string[] = [];
  for (let i = 0; i < chain.length; i++) {
    const a = values[`${inst}.${chain[i][0]}`];
    if (typeof a !== "number") continue;
    let strict = false;
    for (let j = i + 1; j < chain.length; j++) {
      strict = strict || chain[j - 1][1] === "<";
      const b = values[`${inst}.${chain[j][0]}`];
      if (typeof b !== "number") continue;
      if (strict ? !(a < b) : !(a <= b))
        errs.push(`${inst}: ${chain[i][0]} (${a}) must be ${strict ? "<" : "<="} `
                  + `${chain[j][0]} (${b})`);
    }
  }
  return errs;
}

// Every rule the profile breaks, as a user-facing line; [] when it is valid.
export function aircraftProfileErrors(profile: object): string[] {
  const errs: string[] = [];
  const p = profile as { meta?: unknown; values?: unknown };

  const meta = (p.meta ?? {}) as Record<string, unknown>;
  if (typeof meta !== "object" || Array.isArray(meta)) errs.push("meta must be an object");
  else {
    if (meta.speed_unit !== undefined
        && !(SPEED_UNITS as readonly unknown[]).includes(meta.speed_unit))
      errs.push(`meta.speed_unit must be one of ${SPEED_UNITS.join(", ")}`);
    if (meta.baro_unit !== undefined
        && !(BARO_UNITS as readonly unknown[]).includes(meta.baro_unit))
      errs.push(`meta.baro_unit must be one of ${BARO_UNITS.join(", ")}`);
    errs.push(...ownshipErrors(meta));
  }

  const values = (p.values ?? {}) as Record<string, unknown>;
  if (typeof values !== "object" || Array.isArray(values)) {
    errs.push("values must be an object");
    return errs;
  }
  // Stored in the FIX database's native units; blank is an absent key.
  for (const [k, v] of Object.entries(values))
    if (typeof v !== "number" || !Number.isFinite(v))
      errs.push(`${k} must be a number`);

  errs.push(...chainErrors(values, "IAS", AIRSPEED_CHAIN));
  errs.push(...chainErrors(values, "IAS", VFE_CHAIN));
  const insts = new Set(Object.keys(values).map(k => k.split(".")[0]));
  for (const inst of insts)
    if (inst !== "IAS") errs.push(...chainErrors(values, inst, BAND_CHAIN));
  return errs;
}

// The PUT /api/projects/:id/aircraft body -> the JSON text to store, or the
// HTTP status and message to refuse it with.
export function checkAircraftProfilePut(body: unknown): ProfileCheck {
  const profile = (body as { profile?: unknown } | null)?.profile;
  if (!profile || typeof profile !== "object")
    return { ok: false, status: 400, error: "profile object required" };
  const text = JSON.stringify(profile);
  if (text.length > MAX_PROFILE_BYTES)
    return { ok: false, status: 413, error: "profile too large" };
  const errors = aircraftProfileErrors(profile);
  if (errors.length)
    return { ok: false, status: 422, error: "profile fails validation", errors };
  return { ok: true, text };
}
