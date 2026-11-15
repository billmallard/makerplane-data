// SPDX-License-Identifier: AGPL-3.0-or-later
// A tank label as the pyEfis bar gauge draws it (docs/fuel_provider_model.md
// section 6, row F6). One module, three callers: the Worker's profile check
// (src/fuel_profile.ts), the aircraft page's copy of that check, and the
// editor twin's bar name. So the label the page accepts, the label the
// server accepts and the label the twin draws follow one rule.
//
// Everything below is measured, not assumed: tools/fuel_gauge_oracle.py
// builds the stock pyEfis EMS2 screen and the bar widgets offscreen and
// records what they draw (test/fixtures/fuel_gauge_pyefis.json).
// test/fuel_label.test.mjs pins these tables to that fixture; regenerate
// both together.
//
// Pure, no imports, never throws.

// Every distinct FUELQn bar on the stock EMS2 screen, at the stock
// resolutions with and without the button column: its size, the pixel size
// of its name font, and the width of its name rectangle.
export const STOCK_TANK_BARS = [
  { w: 65, h: 327, px: 20, width: 65 },
  { w: 69, h: 344, px: 21, width: 69 },
  { w: 70, h: 349, px: 21, width: 70 },
  { w: 98, h: 491, px: 29, width: 98 },
  { w: 44, h: 218, px: 13, width: 44 },
  { w: 55, h: 273, px: 16, width: 55 },
];

// The printable ASCII characters, in the order of each GLYPH_ADVANCES row.
export const PRINTABLE = Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)).join("");

// DejaVu Sans Condensed glyph advances, in device pixels, at each stock name
// font size, as Qt measures them (hinted, so not proportional to the size).
export const GLYPH_ADVANCES = {
  13: [4, 5, 5, 10, 7, 11, 9, 3, 5, 5, 6, 10, 4, 4, 4, 4, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 4, 4, 10, 10, 10, 6, 12, 8, 8, 8, 9, 7, 7, 9, 9, 3, 3, 8, 7, 10, 9, 9, 7, 9, 8, 7, 7, 9, 8, 12, 8, 7, 8, 5, 4, 5, 10, 6, 6, 7, 7, 6, 7, 7, 4, 7, 7, 3, 3, 7, 3, 11, 7, 7, 7, 7, 5, 6, 5, 7, 7, 10, 7, 7, 6, 7, 4, 7, 10],
  16: [5, 6, 7, 12, 9, 14, 11, 4, 6, 6, 7, 12, 5, 5, 5, 5, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 5, 5, 12, 12, 12, 8, 14, 10, 10, 10, 11, 9, 8, 11, 11, 4, 4, 9, 8, 12, 11, 11, 9, 11, 10, 9, 9, 11, 10, 14, 10, 9, 10, 6, 5, 6, 12, 7, 7, 9, 9, 8, 9, 9, 5, 9, 9, 4, 4, 8, 4, 14, 9, 9, 9, 9, 6, 8, 6, 9, 9, 12, 9, 9, 8, 9, 5, 9, 12],
  20: [6, 7, 8, 15, 11, 17, 14, 5, 7, 7, 9, 15, 6, 7, 6, 6, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 6, 6, 15, 15, 15, 10, 18, 12, 12, 13, 14, 11, 10, 14, 14, 5, 5, 12, 10, 16, 13, 14, 11, 14, 13, 11, 11, 13, 12, 18, 12, 11, 12, 7, 6, 7, 15, 9, 9, 11, 11, 10, 11, 11, 6, 11, 11, 5, 5, 10, 5, 18, 11, 11, 11, 11, 7, 9, 7, 11, 11, 15, 11, 11, 9, 11, 6, 11, 15],
  21: [6, 8, 9, 16, 12, 18, 15, 5, 7, 7, 9, 16, 6, 7, 6, 6, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 6, 6, 16, 16, 16, 10, 19, 13, 13, 13, 15, 12, 11, 15, 14, 6, 6, 12, 11, 16, 14, 15, 11, 15, 13, 12, 12, 14, 13, 19, 13, 12, 13, 7, 6, 7, 16, 9, 9, 12, 12, 10, 12, 12, 7, 12, 12, 5, 5, 11, 5, 18, 12, 12, 12, 12, 8, 10, 7, 12, 11, 15, 11, 11, 10, 12, 6, 12, 16],
  29: [8, 10, 12, 22, 17, 25, 20, 7, 10, 10, 13, 22, 8, 9, 8, 9, 17, 17, 17, 17, 17, 17, 17, 17, 17, 17, 9, 9, 22, 22, 22, 14, 26, 18, 18, 18, 20, 16, 15, 20, 20, 8, 8, 17, 15, 23, 20, 21, 16, 21, 18, 17, 16, 19, 18, 26, 18, 16, 18, 10, 9, 10, 22, 13, 13, 16, 17, 14, 17, 16, 9, 17, 17, 7, 7, 15, 7, 25, 17, 16, 17, 17, 11, 14, 10, 17, 15, 21, 15, 15, 14, 17, 9, 17, 22],
};

// The advance of `text` at `px`: the sum of its glyph advances. A character
// outside the table counts as the widest one. Qt kerns a few pairs ("Ta"),
// so this is never less than what the device measures and at most a few
// pixels more: a label it passes always fits.
export function labelAdvance(text, px) {
  const row = GLYPH_ADVANCES[px];
  if (!row) return Infinity;
  const widest = Math.max(...row);
  let sum = 0;
  for (const ch of String(text)) {
    const i = PRINTABLE.indexOf(ch);
    sum += i < 0 ? widest : row[i];
  }
  return sum;
}

// The line of `text` the bar shows in a name rectangle `width` wide. The
// name is drawn with Qt's default QTextOption, which word-wraps; the bar is
// drawn over every line after the first, so only the first is seen. A first
// word wider than the rectangle stays whole and overflows it (the widget's
// own edge clips it). `measure(s)` is the advance of `s` in the name font.
export function nameLine(text, width, measure) {
  const words = String(text).trim().split(/ +/).filter(Boolean);
  if (!words.length) return { text: "", advance: 0, wraps: false, clipped: false };
  let line = words[0];
  let i = 1;
  for (; i < words.length; i++) {
    const next = line + " " + words[i];
    if (measure(next) > width) break;
    line = next;
  }
  const advance = measure(line);
  return { text: line, advance, wraps: i < words.length, clipped: advance > width };
}

// Does `label` fit every stock tank bar? When it does not, `worst` is the
// narrowest-fitting stock bar and what that bar shows instead: `line` is the
// first wrapped line, `clipped` says even that line is cut at both edges.
export function stockLabelFit(label) {
  const text = String(label == null ? "" : label).trim();
  let worst = null;
  for (const bar of STOCK_TANK_BARS) {
    const measure = (s) => labelAdvance(s, bar.px);
    const full = measure(text);
    if (full <= bar.width) continue;
    const over = full - bar.width;
    if (!worst || over / bar.width > worst.over / worst.width) {
      const line = nameLine(text, bar.width, measure);
      worst = { w: bar.w, h: bar.h, px: bar.px, width: bar.width, over, line: line.text, clipped: line.clipped };
    }
  }
  return { fits: !worst, worst };
}

// The message the profile check gives a label that does not fit.
export function labelFitError(label) {
  const r = stockLabelFit(label);
  if (r.fits) return null;
  const w = r.worst;
  return w.clipped
    ? `label too wide for the stock tank bar: the device cuts "${w.line}" at both edges`
    : `label too wide for the stock tank bar: the device shows only "${w.line}"`;
}
