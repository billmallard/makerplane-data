// SPDX-License-Identifier: AGPL-3.0-or-later
//
// P3e / AER-5147: the aircraft profile's ownship identity. A US N-number
// derives its ICAO 24-bit address offline, a hex entry overrides it, and an
// empty setting yields no address. Runs the REAL src/ownship.ts and the REAL
// copy in aircraft.html (brace-matched, not reimplemented).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { extractConst, extractFunction } from "./support/extract.mjs";
import {
  N_FIRST, N_LAST, N_LETTERS, hex6, nNumberAddress, ownshipAddress, ownshipErrors,
  parseIcaoAddress,
} from "../src/ownship.ts";
import { checkAircraftProfilePut } from "../src/aircraft_profile.ts";
import { devicePreferences } from "../src/device_prefs.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = readFileSync(resolve(HERE, "..", "public", "aircraft.html"), "utf8");
const page = new Function(
  [extractConst(PAGE, "N_LETTERS"), extractFunction(PAGE, "normalizeRegistration"),
   extractFunction(PAGE, "nNumberAddress"), extractFunction(PAGE, "parseIcaoAddress"),
   extractFunction(PAGE, "ownshipStatus"),
   "return { nNumberAddress, parseIcaoAddress, ownshipStatus };"].join("\n"))();

// FAA registry rows (N-NUMBER, MODE S CODE HEX), provenance in the file.
const FAA = readFileSync(resolve(HERE, "fixtures", "faa_nnumber_samples.csv"), "utf8")
  .split("\n").filter((l) => l && !l.startsWith("#") && !l.startsWith("n_number"))
  .map((l) => l.split(","));

// Every assignable N-number: 1-5 digits (no leading 0), then up to two
// letters while the whole stays at most 5 characters.
function* allNNumbers() {
  function* digits(p) {
    yield p;
    if (p.length <= 4) for (const a of N_LETTERS) {
      yield p + a;
      if (p.length <= 3) for (const b of N_LETTERS) yield p + a + b;
    }
    if (p.length < 5) for (let d = 0; d <= 9; d++) yield* digits(p + d);
  }
  for (let d = 1; d <= 9; d++) yield* digits(String(d));
}

test("FAA registry samples derive their registered address, edge letters included", () => {
  assert.equal(FAA.length, 64);
  for (const [n, hex] of FAA) {
    assert.equal(hex6(nNumberAddress(n)), hex, n);
    assert.equal(nNumberAddress(n.toLowerCase()), nNumberAddress(n), `${n} lower case`);
  }
  // Letters next to the never-issued I and O, at the last position.
  const by = Object.fromEntries(FAA);
  assert.equal(by.N1H, "A000B1"); assert.equal(by.N1J, "A000CA");
  assert.equal(by.N1N, "A0012E"); assert.equal(by.N1P, "A00147");
  assert.equal(by["N999ZZ"], "ADF669");
});

test("the N-numbers fill A00001-ADF7C7 exactly, in order, and the page copy agrees", () => {
  let n = 0, prev = N_FIRST - 1;
  const seen = new Set();
  for (const s of allNNumbers()) {
    const a = nNumberAddress("N" + s);
    assert.ok(a >= N_FIRST && a <= N_LAST, `N${s} -> ${a}`);
    seen.add(a);
    // Allocation order is depth-first in this same enumeration order.
    assert.equal(a, prev + 1, `N${s} follows its predecessor`);
    prev = a;
    if (page.nNumberAddress("N" + s) !== a) assert.fail(`aircraft.html disagrees on N${s}`);
    n++;
  }
  assert.equal(n, 915399);
  assert.equal(seen.size, N_LAST - N_FIRST + 1);
  assert.equal(hex6(nNumberAddress("N1")), "A00001");
  assert.equal(hex6(nNumberAddress("N99999")), "ADF7C7");
});

test("what is not an assignable N-number derives nothing", () => {
  for (const s of ["", "N", "N0", "N01", "N1I", "N1O", "N1AI", "N123456", "N12ABC",
                   "N1A2", "N1234AB", "C-GABC", "G-ABCD", "825V", "N 1 2 3 4 5 6"])
    assert.equal(nNumberAddress(s), null, JSON.stringify(s));
  assert.equal(hex6(nNumberAddress(" n825v ")), hex6(nNumberAddress("N825V")));
});

test("the hex override wins over the derived address", () => {
  assert.equal(ownshipAddress({ registration: "N825V", icao_address: "a1b2c3" }), "A1B2C3");
  assert.equal(ownshipAddress({ registration: "N825V", icao_address: "0xA1B2C3" }), "A1B2C3");
  assert.equal(ownshipAddress({ registration: "C-GABC", icao_address: "C01234" }), "C01234");
  assert.equal(ownshipAddress({ registration: "N825V" }), hex6(nNumberAddress("N825V")));
  const st = page.ownshipStatus({ registration: "N825V", icao_address: "A1B2C3" });
  assert.match(st.text, /A1B2C3 is hidden as ownship \(from the override\)/);
  assert.equal(st.warn.length, 1, "a disagreeing override is flagged");
  assert.equal(st.hard.length, 0);
});

test("an empty setting yields no address and changes nothing else", () => {
  for (const meta of [{}, { registration: "" }, { icao_address: "" },
                      { registration: "", icao_address: "" }, null, "x"])
    assert.equal(ownshipAddress(meta), null, JSON.stringify(meta));
  // A non-N registration without hex has no address either (never a guess).
  assert.equal(ownshipAddress({ registration: "G-ABCD" }), null);
  const base = { schema_version: 1, meta: { engines: 1, baro_unit: "hPa" }, values: {} };
  const blank = { ...base, meta: { ...base.meta, registration: "", icao_address: "" } };
  assert.deepEqual(checkAircraftProfilePut({ profile: blank }).ok, true);
  assert.deepEqual(devicePreferences(blank), devicePreferences(base));
  assert.match(page.ownshipStatus({}).text, /Not set/);
});

test("save-time validation refuses malformed identity, page and server alike", () => {
  const put = (meta) => checkAircraftProfilePut({ profile: { meta, values: {} } });
  assert.equal(put({ registration: "N825V", icao_address: "A1B2C3" }).ok, true);
  assert.equal(put({ registration: "G-ABCD" }).ok, true);
  for (const [meta, re] of [
    [{ registration: "N0123" }, /not a valid US N-number/],
    [{ registration: "N12ABC" }, /not a valid US N-number/],
    [{ registration: "TOO-LONG-REG" }, /up to 10 letters/],
    [{ registration: "N8<25>" }, /up to 10 letters/],
    [{ registration: 825 }, /up to 10 letters/],
    [{ icao_address: "A1B2C" }, /6 hex digits/],
    [{ icao_address: "000000" }, /6 hex digits/],
    [{ icao_address: "GGGGGG" }, /6 hex digits/],
  ]) {
    const r = put(meta);
    assert.equal(r.ok, false, JSON.stringify(meta));
    assert.equal(r.status, 422);
    assert.match(r.errors.join(" "), re);
    assert.deepEqual(ownshipErrors(meta), r.errors);
    if (typeof meta.registration !== "number")
      assert.ok(page.ownshipStatus(meta).hard.length > 0, `page blocks ${JSON.stringify(meta)}`);
  }
  assert.equal(parseIcaoAddress("ffffff"), 0xffffff);
  assert.equal(page.parseIcaoAddress("ffffff"), 0xffffff);
});
