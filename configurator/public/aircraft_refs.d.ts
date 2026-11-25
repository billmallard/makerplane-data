// SPDX-License-Identifier: AGPL-3.0-or-later
// Types for aircraft_refs.js, so the Worker (TypeScript) can import the same
// module the editor loads. Keep in step with the .js file.

export interface RefSpec {
  source: string;
  resolves: Record<string, string>;
  types: string[];
}
export interface RefItem {
  id: string;
  label: string;
  slot: number;
  role?: string;
  slot_key: string;
}
export type RefBinding = Record<string, unknown> & { option: string; id: string; unbound?: true };

export type RefTable = Record<string, RefSpec>;
export const FALLBACK_REFS: RefTable;
export const REF_SOURCE_FIELDS: Record<string, string[]>;
export function refsFromSchema(schema: unknown, conflicts?: { type: string; option: string }[]): RefTable | null;
export function refItems(profile: unknown, source: string): RefItem[];
export function retiredLabels(profiles: unknown[], source: string, current: unknown): Record<string, string>;
export function resolveOptions(
  type: string, options: unknown, items: Record<string, RefItem[]>, retired?: Record<string, string>,
  refs?: RefTable,
): { options: unknown; bindings: RefBinding[] };
export function resolveDoc(
  doc: unknown, items: Record<string, RefItem[]>, retired?: Record<string, string>, refs?: RefTable,
): { doc: unknown; bindings: (RefBinding & { at: string })[] };
export function itemsBySource(profile: unknown, refs?: RefTable): Record<string, RefItem[]>;
