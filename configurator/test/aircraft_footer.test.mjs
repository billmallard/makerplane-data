// SPDX-License-Identifier: AGPL-3.0-or-later
//
// AER-5900 (makerplane-data #218): the Aircraft page footer tells the user
// which profile settings reach the aircraft with config-pull. It once said the
// ownship identity was "not sent" after P3f had shipped it. Each sentence that
// names a delivered setting carries data-device-pull="<key>"; this test drives
// the REAL serveDeviceConfig with a profile that sets everything it can serve
// and requires the footer's keys to equal what was actually served.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";
import { FALLBACK_REFS } from "../public/aircraft_refs.js";
import { serveDeviceConfig } from "../src/device_prefs.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = readFileSync(resolve(HERE, "..", "public", "aircraft.html"), "utf8");
const FOOTER = PAGE.match(/<p class="footer-note">([\s\S]*?)<\/p>/)[1];

const footerKeys = () =>
  [...FOOTER.matchAll(/data-device-pull="([^"]+)"/g)].map((m) => m[1]).sort();

// Everything the configurator can put on a served panel today.
const PROFILE = JSON.stringify({
  schema_version: 1,
  meta: {
    baro_unit: "hPa", registration: "N825V",
    fuel: { schema: 2, tanks: [
      { id: "tk_l", label: "L MAIN", role: "main", slot: 1, feeds: [], capacity: 20, unusable: 0 },
    ] },
  },
  values: { "IAS.Vne": 160 },
});
const gauge = (options) => ({ type: "vertical_bar_gauge", row: 0, column: 0,
  span: { rows: 20, columns: 10 }, options });
const PANEL = yaml.dump({
  main: { screenWidth: 1280, screenHeight: 800, defaultScreen: "EMS" },
  screens: { EMS: { module: "pyefis.screens.screenbuilder", title: "EMS",
    layout: { rows: 110, columns: 200 },
    instruments: Object.keys(FALLBACK_REFS).map((opt) => gauge({ [opt]: "tk_l" })) } },
}, { lineWidth: 100, noRefs: true });

test("the footer names exactly the settings config-pull receives", async () => {
  const { body } = await serveDeviceConfig(PANEL, 1, PROFILE);
  const doc = yaml.load(body);
  const panelKeys = Object.keys(yaml.load(PANEL));
  const served = [
    // Every top-level block the profile added (preferences:, fixgw:, and
    // any block added later), by its keys. A `<key>_sha256` is the
    // integrity hash of a delivered setting, not a setting of its own.
    ...Object.keys(doc).filter((k) => !panelKeys.includes(k))
      .flatMap((k) => Object.keys(doc[k])).filter((k) => !k.endsWith("_sha256")),
    // A resolved reference shows as its resolved options on the instrument.
    ...Object.entries(FALLBACK_REFS)
      .filter(([, r]) => doc.screens.EMS.instruments.some((i) =>
        Object.keys(r.resolves).every((k) => i.options[k] !== undefined)))
      .map(([opt]) => opt),
  ].sort();
  assert.deepEqual(footerKeys(), served);
});

test("the footer does not call a delivered setting undelivered", () => {
  assert.doesNotMatch(FOOTER, /not (sent|delivered)/i);
});
