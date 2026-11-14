// SPDX-License-Identifier: AGPL-3.0-or-later
// Types for fuel_label.js, so the Worker (TypeScript) can import the same
// module the aircraft page and the editor load. Keep in step with the .js file.

export interface StockTankBar { w: number; h: number; px: number; width: number }
export interface NameLine { text: string; advance: number; wraps: boolean; clipped: boolean }
export interface LabelFit {
  fits: boolean;
  worst: null | { w: number; h: number; px: number; width: number; over: number; line: string; clipped: boolean };
}

export const STOCK_TANK_BARS: StockTankBar[];
export const PRINTABLE: string;
export const GLYPH_ADVANCES: Record<number, number[]>;
export function labelAdvance(text: string, px: number): number;
export function nameLine(text: string, width: number, measure: (s: string) => number): NameLine;
export function stockLabelFit(label: unknown): LabelFit;
export function labelFitError(label: unknown): string | null;
