// SPDX-License-Identifier: AGPL-3.0-or-later
//
// INS4 (AER-6587): the editor reads schema.json's version and the pyEfis
// commit that built it, and says so in its header when the version is newer
// than it knows. Runs the REAL schemaNotice out of editor.html.

import { test } from "node:test";
import assert from "node:assert/strict";
import { editorSource, extractFunction } from "./support/extract.mjs";

const SRC = editorSource();
const SHA = "0123456789abcdef0123456789abcdef01234567";


test("schemaNotice: version and commit in the title; a warning only when newer or missing", () => {
  const f = new Function(`${SRC.match(/const SCHEMA_VERSION_MAX = \d+;/)[0]}\n${extractFunction(SRC, "schemaNotice")}\nreturn schemaNotice;`)();
  assert.deepEqual(f({ schema_version: 4, pyefis_commit: SHA }),
    { title: "Instrument schema v4, built from pyEfis 0123456789ab", warn: null });
  assert.deepEqual(f({ schema_version: 3 }),
    { title: "Instrument schema v3, pyEfis build commit not stated", warn: null });
  assert.match(f({ schema_version: 5, pyefis_commit: SHA }).warn, /is v5; this editor knows up to v4/);
  assert.match(f({}).warn, /states no version/);
  assert.equal(f({ schema_version: 4, pyefis_commit: "<script>" }).title, "Instrument schema v4, pyEfis build commit not stated");
  assert.match(SRC, /const sn = schemaNotice\(state\.schema\);/);
});
