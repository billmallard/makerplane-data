// SPDX-License-Identifier: AGPL-3.0-or-later
// Server-side checks on an aircraft profile before it is saved
// (docs/canfix_configurator.md section 5).
//
// Pure functions, no imports, so node:test can load this file directly.

export const MAX_PROFILE_BYTES = 256 * 1024;

export type ProfileCheck =
  | { ok: true; text: string }
  | { ok: false; status: 400 | 413 | 422; error: string; errors?: string[] };

// The PUT /api/projects/:id/aircraft body -> the JSON text to store, or the
// HTTP status and message to refuse it with.
export function checkAircraftProfilePut(body: unknown): ProfileCheck {
  const profile = (body as { profile?: unknown } | null)?.profile;
  if (!profile || typeof profile !== "object")
    return { ok: false, status: 400, error: "profile object required" };
  const text = JSON.stringify(profile);
  if (text.length > MAX_PROFILE_BYTES)
    return { ok: false, status: 413, error: "profile too large" };
  return { ok: true, text };
}
