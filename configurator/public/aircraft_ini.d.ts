// SPDX-License-Identifier: AGPL-3.0-or-later
// Types for aircraft_ini.js, so the Worker (TypeScript) can import the same
// module the aircraft page loads. Keep in step with the .js file.

export function expandCylinders(values: unknown, cylinders: unknown): Record<string, unknown>;
export function aircraftIniLines(profile: unknown): string[];
export function buildAircraftIni(profile: unknown, header?: unknown[]): string;
