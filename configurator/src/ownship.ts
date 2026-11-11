// SPDX-License-Identifier: AGPL-3.0-or-later
// Ownship identity for the ADS-B traffic filter (P3e, AER-5147).
//
// The pilot enters the aircraft's registration, and optionally its 24-bit
// ICAO address in hex, in the aircraft profile (meta.registration,
// meta.icao_address). fix-gateway's traffic writer drops traffic reports
// carrying that address as ownship (`ownship_address`, pyEfis
// docs/fix_dynamic_list_spec.md section 4.8). Filtering is by identity only:
// an unset or underivable setting filters nothing, and every unmatched
// target is still shown.
//
// A US N-number maps to its address by the FAA's fixed allocation: the
// 915,399 assignable N-numbers, in order, take A00001-ADF7C7. Other states
// have no such public mapping, so the hex entry covers them, and it wins
// over the derived address whenever both are set.
//
// aircraft.html carries a copy of nNumberAddress (a browser page cannot load
// .ts); test/ownship.test.mjs holds the two equal over every N-number.
//
// Pure functions, no imports, so node:test can load this file directly.

// I and O are never issued (14 CFR 47.15(b)), so they look like 1 and 0.
export const N_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ";

export const N_FIRST = 0xa00001;
export const N_LAST = 0xadf7c7;

// Upper case, surrounding and inner blanks removed.
export function normalizeRegistration(raw: unknown): string {
  return typeof raw === "string" ? raw.toUpperCase().replace(/\s+/g, "") : "";
}

// A US N-number ("N825V", any case) -> its 24-bit address, or null when the
// text is not an assignable N-number: 1-5 characters after N, a leading
// digit 1-9, digits then at most two trailing letters, no I or O.
export function nNumberAddress(reg: string): number | null {
  const s = normalizeRegistration(reg);
  if (!/^N[1-9][0-9]{0,4}$|^N[1-9][0-9]{0,3}[A-HJ-NP-Z]$|^N[1-9][0-9]{0,2}[A-HJ-NP-Z]{2}$/.test(s))
    return null;
  // Each block holds a prefix, then (at positions 1-3) its 600 one- and
  // two-letter suffixes, then ten child blocks, one per next digit.
  const SUFFIXES = 1 + N_LETTERS.length * (1 + N_LETTERS.length);   // 601, incl. the prefix
  const BLOCK = [101711, 10111, 951, 35];                             // per prefix of 1..4 digits
  let a = N_FIRST + (Number(s[1]) - 1) * BLOCK[0];
  for (let i = 2; i < s.length; i++) {
    const c = s[i], letter = N_LETTERS.indexOf(c);
    if (i === 5) return a + (letter >= 0 ? 1 + letter : 1 + N_LETTERS.length + Number(c));
    if (letter >= 0) {
      a += 1 + letter * (1 + N_LETTERS.length);
      if (i + 1 < s.length) a += 1 + N_LETTERS.indexOf(s[i + 1]);
      return a;
    }
    a += SUFFIXES + Number(c) * BLOCK[i - 1];
  }
  return a;
}

// "a1b2c3" / "0xA1B2C3" -> 0xA1B2C3; null for anything else. 000000 is not
// an assignable address.
export function parseIcaoAddress(raw: unknown): number | null {
  if (typeof raw !== "string") return null;
  const m = /^(?:0X)?([0-9A-F]{6})$/.exec(raw.trim().toUpperCase());
  if (!m) return null;
  const a = parseInt(m[1], 16);
  return a === 0 ? null : a;
}

export const hex6 = (a: number) => a.toString(16).toUpperCase().padStart(6, "0");

// What the traffic writer's ownship_address should be, as six upper-case hex
// digits, or null to leave it unset: the hex entry if it is set, else the
// address derived from a US N-number, else nothing.
export function ownshipAddress(meta: unknown): string | null {
  if (!meta || typeof meta !== "object") return null;
  const m = meta as { registration?: unknown; icao_address?: unknown };
  const hex = parseIcaoAddress(m.icao_address);
  if (hex !== null) return hex6(hex);
  const derived = nNumberAddress(normalizeRegistration(m.registration));
  return derived === null ? null : hex6(derived);
}

// Save-time checks on meta.registration / meta.icao_address. Blank (absent or
// "") is always fine: it means "no ownship filter beyond the receiver's own".
// A registration is identity only -- up to 10 letters, digits and hyphens --
// and one starting with N must be a valid N-number (no other state uses N).
export function ownshipErrors(meta: Record<string, unknown>): string[] {
  const errs: string[] = [];
  const reg = meta.registration, hex = meta.icao_address;
  if (reg !== undefined && reg !== "") {
    const s = normalizeRegistration(reg);
    if (typeof reg !== "string" || !/^[A-Z0-9][A-Z0-9-]{0,9}$/.test(s))
      errs.push("meta.registration must be up to 10 letters, digits and hyphens");
    else if (s.startsWith("N") && nNumberAddress(s) === null)
      errs.push(`meta.registration ${s} is not a valid US N-number`);
  }
  if (hex !== undefined && hex !== "" && parseIcaoAddress(hex) === null)
    errs.push("meta.icao_address must be 6 hex digits (not 000000)");
  return errs;
}
