// SPDX-License-Identifier: AGPL-3.0-or-later
// aircraft.ini: the aircraft profile's values in fix-gateway's init_data
// dialect (docs/canfix_configurator.md section 7). One compiler, two callers:
// the aircraft page's Download button and GET /device/config
// (src/device_prefs.ts), so the file a builder downloads and the file
// config-pull installs hold the same lines.
//
// fix-gateway's loader (fixgw/server.py) skips blank lines and lines that
// start with '#', and splits every other line on '='. A key the device's
// database does not have stops fix-gateway at startup. So:
//   * comments are '#' -- a ';' line holding an '=' would be read as a key;
//   * only `KEY.aux = <number>` (or `KEY = <number>`) lines are written, with
//     a plain FIX key and a finite number. Anything else in the profile is
//     left out rather than handed to the loader.
//
// Pure, no imports, never throws.

const KEY = /^[A-Za-z0-9]+(\.[A-Za-z0-9]+)?$/;

// "CHT1.x"/"EGT1.x" pseudo-instances -> CHT11..CHT1c for the profile's
// cylinder count; every other key passes through.
export function expandCylinders(values, cylinders) {
  const out = {};
  const n = Number.isInteger(cylinders) && cylinders > 0 ? cylinders : 0;
  for (const [fk, v] of Object.entries(values || {})) {
    const m = /^(CHT|EGT)(\d+)\.(.+)$/.exec(fk);
    if (m) {
      for (let c = 1; c <= n; c++) out[`${m[1]}${m[2]}${c}.${m[3]}`] = v;
    } else out[fk] = v;
  }
  return out;
}

// The value lines, sorted by key, a blank line between FIX instances. Never
// starts or ends with a blank line.
export function aircraftIniLines(profile) {
  const p = profile && typeof profile === "object" ? profile : {};
  const values = p.values && typeof p.values === "object" && !Array.isArray(p.values) ? p.values : {};
  const meta = p.meta && typeof p.meta === "object" ? p.meta : {};
  const vals = expandCylinders(values, meta.cylinders);
  const lines = [];
  let prev = null;
  for (const fk of Object.keys(vals).sort()) {
    const v = vals[fk];
    if (!KEY.test(fk) || typeof v !== "number" || !Number.isFinite(v)) continue;
    const inst = fk.split(".")[0];
    if (prev !== null && inst !== prev) lines.push("");
    prev = inst;
    lines.push(`${fk} = ${v}`);
  }
  return lines;
}

// The whole file: `header` lines (each made a '#' comment), a blank line, the
// values. Ends in exactly one newline, never a blank line, so it survives a
// YAML literal block (`|`, clip) byte for byte.
export function buildAircraftIni(profile, header) {
  const head = (header || []).map((h) => "# " + String(h).replace(/[\r\n]+/g, " "));
  const body = aircraftIniLines(profile);
  const lines = body.length ? [...head, "", ...body] : head;
  return lines.length ? lines.join("\n") + "\n" : "";
}
