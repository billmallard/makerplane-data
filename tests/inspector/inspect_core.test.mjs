// SPDX-License-Identifier: Apache-2.0
//
// The inspector's trust and locator logic, run in node against the same
// module the page loads (site/assets/inspect-core.js). Signing here builds
// minisign "Ed" text the way packtools.signing does; tests/test_inspector.py
// checks the real Python signer against this verifier.
//
//   node --test tests/inspector/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import * as core from "../../site/assets/inspect-core.js";

const subtle = webcrypto.subtle;
const b64 = (u8) => Buffer.from(u8).toString("base64");
const enc = new TextEncoder();

async function keypair(keyId = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])) {
  const kp = await subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const raw = new Uint8Array(await subtle.exportKey("raw", kp.publicKey));
  const pub = `untrusted comment: test key\n${b64([..."Ed"].map((c) => c.charCodeAt(0)).concat([...keyId, ...raw]))}\n`;
  return { kp, keyId, pub };
}

async function minisig({ kp, keyId }, data, trusted, alg = "Ed") {
  const sig = new Uint8Array(await subtle.sign({ name: "Ed25519" }, kp.privateKey, data));
  const tc = enc.encode(trusted);
  const glob = new Uint8Array(await subtle.sign({ name: "Ed25519" }, kp.privateKey,
                                                new Uint8Array([...sig, ...tc])));
  const blob = new Uint8Array([alg.charCodeAt(0), alg.charCodeAt(1), ...keyId, ...sig]);
  return `untrusted comment: x\n${b64(blob)}\ntrusted comment: ${trusted}\n${b64(glob)}\n`;
}

const MANIFEST = enc.encode('{"manifest_version":1,"packs":[]}\n');

test("a good signature verifies and returns the trusted comment", async () => {
  const k = await keypair();
  const sig = await minisig(k, MANIFEST, "nightly 2026-11-01");
  assert.equal(await core.verifyManifest(MANIFEST, sig, k.pub, subtle), "nightly 2026-11-01");
});

test("one changed manifest byte is refused", async () => {
  const k = await keypair();
  const sig = await minisig(k, MANIFEST, "t");
  const bad = MANIFEST.slice(); bad[5] ^= 1;
  await assert.rejects(core.verifyManifest(bad, sig, k.pub, subtle), /does not verify/);
});

test("an edited trusted comment is refused", async () => {
  const k = await keypair();
  const sig = (await minisig(k, MANIFEST, "real")).replace("trusted comment: real", "trusted comment: fake");
  await assert.rejects(core.verifyManifest(MANIFEST, sig, k.pub, subtle), /trusted comment/);
});

test("a signature from another key is refused", async () => {
  const k = await keypair(), other = await keypair();
  const sig = await minisig(other, MANIFEST, "t");
  await assert.rejects(core.verifyManifest(MANIFEST, sig, k.pub, subtle), /does not verify/);
  const otherId = await keypair(new Uint8Array(8).fill(9));
  const sig2 = await minisig(otherId, MANIFEST, "t");
  await assert.rejects(core.verifyManifest(MANIFEST, sig2, k.pub, subtle), /key id/);
});

test("a prehashed ED signature is refused, not half-checked", async () => {
  const k = await keypair();
  const sig = await minisig(k, MANIFEST, "t", "ED");
  await assert.rejects(core.verifyManifest(MANIFEST, sig, k.pub, subtle), /not checkable/);
});

test("a truncated signature file is refused", async () => {
  const k = await keypair();
  const sig = (await minisig(k, MANIFEST, "t")).split("\n").slice(0, 2).join("\n");
  await assert.rejects(core.verifyManifest(MANIFEST, sig, k.pub, subtle), /malformed/);
});

test("the embedded key is keys/minisign.pub", () => {
  const file = readFileSync(new URL("../../keys/minisign.pub", import.meta.url), "utf8");
  assert.deepEqual(core.parsePublicKey(core.PUBLIC_KEY), core.parsePublicKey(file));
});

test("checkPack opens only the exact signed bytes", async () => {
  const bytes = enc.encode("SQLite format 3\0 pretend");
  const sha = await core.sha256Hex(bytes, subtle);
  const entry = { bytes: bytes.length, sha256: sha };
  assert.equal(await core.checkPack(entry, bytes, subtle), sha);
  await assert.rejects(core.checkPack({ ...entry, bytes: bytes.length + 1 }, bytes, subtle), /size/);
  const bad = bytes.slice(); bad[bad.length - 1] ^= 1;
  await assert.rejects(core.checkPack(entry, bad, subtle), /sha256/);
});

test("only the whole-file SQLite kinds are offered", () => {
  const m = { packs: ["navdata", "navaids", "obstacles", "procedures", "airports",
                      "water", "highways", "terrain"].map((kind) => ({ id: kind, kind })) };
  assert.deepEqual(core.sqliteEntries(m).map((p) => p.kind).sort(),
                   ["airports", "navaids", "navdata", "obstacles", "procedures"]);
});

test("a bare pack id opens the edition in effect today", () => {
  const m = { packs: [
    { id: "navaids-conus", cycle: "2611", effective: "2026-10-29", expires: "2026-11-26" },
    { id: "navaids-conus", cycle: "2612", effective: "2026-11-26", expires: "2026-12-24" },
    { id: "airports-canada", cycle: "2026.06" }] };
  assert.equal(core.pickEntry(m, "navaids-conus", "", "2026-11-01").cycle, "2611");
  assert.equal(core.pickEntry(m, "navaids-conus", "", "2026-12-01").cycle, "2612");
  assert.equal(core.pickEntry(m, "navaids-conus", "2612", "2026-11-01").cycle, "2612");
  assert.equal(core.pickEntry(m, "navaids-conus", "9999", "2026-11-01"), null);
  assert.equal(core.pickEntry(m, "airports-canada", "", "2026-11-01").cycle, "2026.06");
});

const TABLES = { obstacles: ["oas_no", "lat", "lon", "agl_ft"], pack_meta: ["key", "value"] };

test("a row locator selects that rowid", () => {
  const loc = core.parseLocator("?pack=obstacles-conus&cycle=261001&table=obstacles&row_id=7");
  assert.equal(loc.pack, "obstacles-conus");
  assert.equal(core.locatorQuery(loc, TABLES), 'SELECT rowid AS row_id, * FROM "obstacles" WHERE rowid = 7');
});

test("a lat/lon locator selects a box around the point", () => {
  const q = core.locatorQuery(core.parseLocator("?table=obstacles&lat=44&lon=-88.5"), TABLES);
  assert.match(q, /WHERE lat BETWEEN 43\.95 AND 44\.05\n {2}AND lon BETWEEN -88\.55 AND -88\.45$/);
  // No lat/lon columns: falls back to the first rows of the table.
  assert.match(core.locatorQuery(core.parseLocator("?table=pack_meta&lat=1&lon=2"), TABLES), /LIMIT 100/);
});

test("only a real table and numeric values reach the SQL", () => {
  const loc = core.parseLocator('?table=x"; DROP TABLE obstacles; --&row_id=1');
  assert.throws(() => core.locatorQuery(loc, TABLES), /no table/);
  const odd = core.parseLocator("?table=obstacles&row_id=1;DROP&lat=1)--&lon=2");
  assert.equal(odd.row_id, null);
  assert.equal(odd.lat, null);
  assert.match(core.locatorQuery(odd, TABLES), /LIMIT 100$/);
  // A table name with a quote in it is quoted, not ended.
  assert.equal(core.locatorQuery(core.parseLocator('?table=a"b&row_id=2'), { 'a"b': [] }),
               'SELECT rowid AS row_id, * FROM "a""b" WHERE rowid = 2');
});

test("q passes through; no table and no q means no locator query", () => {
  assert.equal(core.locatorQuery(core.parseLocator("?q=SELECT+1"), TABLES), "SELECT 1");
  assert.equal(core.locatorQuery(core.parseLocator("?pack=x"), TABLES), null);
  assert.equal(core.parseLocator("?row_id=abc").row_id, null);
  assert.equal(core.parseLocator("?sha256=ABCD").sha256, "abcd");
});

test("locatorUrl pins id, cycle and sha256", () => {
  const u = core.locatorUrl({ id: "a", cycle: "1", sha256: "ff" }, { table: "t", row_id: 3, q: "" });
  assert.equal(u, "?pack=a&cycle=1&sha256=ff&table=t&row_id=3");
});
