// SPDX-License-Identifier: AGPL-3.0-or-later
// The two js-yaml 4 calls the Worker makes (aircraft_refs.ts). A local
// declaration rather than an @types dependency.

declare module "js-yaml" {
  export function load(text: string): unknown;
  export function dump(value: unknown, opts?: { lineWidth?: number; noRefs?: boolean }): string;
  const yaml: { load: typeof load; dump: typeof dump };
  export default yaml;
}
