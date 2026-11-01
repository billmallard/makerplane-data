// SPDX-License-Identifier: Apache-2.0
//
// The trust and query logic behind inspect.html, kept free of the DOM so
// tests/inspector/*.test.mjs can run the same code in node.
//
// The order the page follows is the device's order (pyefis_data/core.py):
// verify the manifest's minisign signature against the published key, find
// the pack's entry in the now-trusted manifest, fetch the bytes, check their
// length and sha256 against that entry, and only then hand them to SQLite.
// Nothing here opens bytes the signed manifest has not vouched for.

// keys/minisign.pub; the same key pyefis_data/cli.py embeds.
export const PUBLIC_KEY =
  "untrusted comment: makerplane-data production signing key\n" +
  "RWQXjK7+q8WvsWtCd+QPQNxO7OcI5XuF1uxY7MGp+R2JidxFSO/20AR4\n";

// Kinds whose pack is a single SQLite file small enough to read whole.
// water and highways are SQLite too, but hundreds of MB to GB: they need the
// range-read mode (HTTP Range pages), which is a follow-on.
export const SQLITE_KINDS = ["navdata", "airports", "navaids", "obstacles", "procedures"];

export const MAX_ROWS = 1000;

const b64 = (s) => Uint8Array.from(atob(s.trim()), (c) => c.charCodeAt(0));
const hex = (buf) => [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, "0")).join("");
const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

export function parsePublicKey(text) {
  const line = text.split(/\r?\n/).find((l) => l.trim() && !l.startsWith("untrusted comment:"));
  const blob = b64(line || "");
  if (blob.length !== 42) throw new Error("public key must be 42 bytes (2 alg + 8 id + 32 key)");
  return { keyId: blob.slice(2, 10), key: blob.slice(10) };
}

export function parseMinisig(text) {
  const lines = text.split(/\r?\n/);
  if (lines.length < 4 || !lines[2].startsWith("trusted comment: "))
    throw new Error("malformed signature file");
  const blob = b64(lines[1]);
  if (blob.length !== 74) throw new Error("signature must be 74 bytes (2 alg + 8 id + 64 sig)");
  return {
    alg: String.fromCharCode(blob[0], blob[1]),
    keyId: blob.slice(2, 10),
    sig: blob.slice(10),
    trustedComment: lines[2].slice("trusted comment: ".length),
    globalSig: b64(lines[3]),
  };
}

// Returns the trusted comment; throws on any failure. Only the legacy "Ed"
// form (signature over the raw bytes) is accepted -- what packtools.signing
// writes. A prehashed "ED" signature needs BLAKE2b, which WebCrypto lacks;
// it is refused rather than half-checked.
export async function verifyManifest(manifestBytes, sigText, pubText, subtle) {
  const pk = parsePublicKey(pubText);
  const s = parseMinisig(sigText);
  if (!eq(s.keyId, pk.keyId)) throw new Error("signature key id does not match the published key");
  if (s.alg !== "Ed")
    throw new Error(`signature type ${JSON.stringify(s.alg)} is not checkable in a browser; ` +
                    "use minisign -Vm manifest.json");
  if (s.globalSig.length !== 64) throw new Error("malformed trusted-comment signature");
  const key = await subtle.importKey("raw", pk.key, { name: "Ed25519" }, false, ["verify"]);
  if (!(await subtle.verify({ name: "Ed25519" }, key, s.sig, manifestBytes)))
    throw new Error("manifest signature does not verify");
  const tc = new TextEncoder().encode(s.trustedComment);
  const bound = new Uint8Array(s.sig.length + tc.length);
  bound.set(s.sig); bound.set(tc, s.sig.length);
  if (!(await subtle.verify({ name: "Ed25519" }, key, s.globalSig, bound)))
    throw new Error("trusted comment signature does not verify");
  return s.trustedComment;
}

export async function sha256Hex(bytes, subtle) {
  return hex(await subtle.digest("SHA-256", bytes));
}

export function sqliteEntries(manifest) {
  return (manifest.packs || []).filter((p) => SQLITE_KINDS.includes(p.kind));
}

// The edition a bare `pack=<id>` opens: the one in effect today, else the
// newest listed. Cycle strings of one id sort in issue order.
export function pickEntry(manifest, id, cycle, today) {
  const all = (manifest.packs || []).filter((p) => p.id === id);
  if (cycle) return all.find((p) => p.cycle === cycle) || null;
  const live = all.filter((p) => p.effective && p.expires && p.effective <= today && today < p.expires);
  const pool = live.length ? live : all;
  return pool.slice().sort((a, b) => (a.cycle < b.cycle ? -1 : a.cycle > b.cycle ? 1 : 0)).pop() || null;
}

// Throws unless `bytes` are exactly the pack the signed entry names.
export async function checkPack(entry, bytes, subtle) {
  if (bytes.byteLength !== entry.bytes)
    throw new Error(`size ${bytes.byteLength} does not match the signed catalog's ${entry.bytes}`);
  const got = await sha256Hex(bytes, subtle);
  if (got !== entry.sha256)
    throw new Error(`sha256 ${got} does not match the signed catalog's ${entry.sha256}`);
  return got;
}

// A locator is the page's query string (docs/pack_inspector.md). Every field
// is optional; unknown fields are ignored.
export function parseLocator(search) {
  const p = new URLSearchParams(search);
  const num = (k) => (p.has(k) && p.get(k) !== "" && Number.isFinite(+p.get(k)) ? +p.get(k) : null);
  return {
    pack: p.get("pack") || "",
    cycle: p.get("cycle") || "",
    sha256: (p.get("sha256") || "").toLowerCase(),
    table: p.get("table") || "",
    row_id: num("row_id"),
    lat: num("lat"),
    lon: num("lon"),
    q: p.get("q") || "",
  };
}

export function locatorUrl(entry, extra = {}) {
  const p = new URLSearchParams({ pack: entry.id, cycle: entry.cycle, sha256: entry.sha256 });
  for (const [k, v] of Object.entries(extra)) if (v !== null && v !== undefined && v !== "") p.set(k, v);
  return "?" + p.toString();
}

const quoteId = (name) => '"' + name.replace(/"/g, '""') + '"';
export const LAT_LON_BOX = 0.05; // degrees either side, about 3 nm

// SQL for a locator's place in the pack, or null when it names none. The
// SQL goes into the editor, so it carries its values inline and can be
// re-run or edited as shown. Nothing foreign gets in: parseLocator only yields
// finite numbers for row_id/lat/lon, and `tables` (table name -> column
// names, read from the opened pack) means only a real table can be named.
export function locatorQuery(loc, tables) {
  if (loc.q) return loc.q;
  if (!loc.table) return null;
  const cols = tables[loc.table];
  if (!cols) throw new Error(`the pack has no table ${JSON.stringify(loc.table)}`);
  const t = quoteId(loc.table);
  if (loc.row_id !== null) return `SELECT rowid AS row_id, * FROM ${t} WHERE rowid = ${loc.row_id}`;
  if (loc.lat !== null && loc.lon !== null && cols.includes("lat") && cols.includes("lon")) {
    const d = (x) => +x.toFixed(6);
    return `SELECT rowid AS row_id, * FROM ${t}\n` +
      `WHERE lat BETWEEN ${d(loc.lat - LAT_LON_BOX)} AND ${d(loc.lat + LAT_LON_BOX)}\n` +
      `  AND lon BETWEEN ${d(loc.lon - LAT_LON_BOX)} AND ${d(loc.lon + LAT_LON_BOX)}`;
  }
  return `SELECT rowid AS row_id, * FROM ${t} LIMIT 100`;
}

// The FAA airport pack keys airports by FAA location id (OSH, not KOSH);
// OurAirports packs by ICAO.
const airportQueries = (ident) => [
  ["An airport and its runways",
   "SELECT a.icao, a.name, a.elev_ft, r.rwy_id, r.length_ft, r.width_ft, r.surface\n" +
   `FROM airports a JOIN runways r USING (site_no)\nWHERE a.icao = '${ident}'`],
  ["Runway ends at an airport",
   `SELECT e.*\nFROM airports a JOIN runway_ends e USING (site_no)\nWHERE a.icao = '${ident}'`],
  ["Airports per state",
   "SELECT state, count(*) AS airports FROM airports GROUP BY state ORDER BY airports DESC"],
  ["Highest airports",
   "SELECT icao, name, state, elev_ft FROM airports ORDER BY elev_ft DESC LIMIT 25"],
  ["Longest runways",
   "SELECT a.icao, r.rwy_id, r.length_ft, r.surface\n" +
   "FROM runways r JOIN airports a USING (site_no)\nORDER BY r.length_ft DESC LIMIT 25"],
];

export const CANNED = {
  navdata: airportQueries("OSH"),
  airports: airportQueries("CYYZ"),
  navaids: [
    ["A navaid by identifier", "SELECT * FROM navaids WHERE id = 'OSH'"],
    ["Navaids by type", "SELECT type, count(*) AS n FROM navaids GROUP BY type ORDER BY n DESC"],
    ["A fix by name", "SELECT * FROM fixes WHERE id = 'BAYLI'"],
    ["One airway, in order", "SELECT * FROM awy_segments WHERE awy_id = 'V2' ORDER BY seq"],
  ],
  obstacles: [
    ["Tallest obstacles (AGL)",
     "SELECT oas_no, type, agl_ft, amsl_ft, state, city, lat, lon\n" +
     "FROM obstacles ORDER BY agl_ft DESC LIMIT 25"],
    ["Obstacles by type", "SELECT type, count(*) AS n FROM obstacles GROUP BY type ORDER BY n DESC"],
    ["Obstacles per state", "SELECT state, count(*) AS n FROM obstacles GROUP BY state ORDER BY n DESC"],
    ["Near a point (about 3 nm box)",
     "SELECT oas_no, type, agl_ft, amsl_ft, lat, lon FROM obstacles\n" +
     "WHERE lat BETWEEN 43.93 AND 44.03 AND lon BETWEEN -88.61 AND -88.51\nORDER BY agl_ft DESC"],
  ],
  procedures: [
    ["Procedures at an airport",
     "SELECT id, kind, ident, runway, approach_type, rnp FROM procedures\n" +
     "WHERE airport = 'KOSH' ORDER BY kind, ident"],
    ["Legs of one procedure",
     "SELECT t.role, t.ident AS transition, l.*\n" +
     "FROM procedures p JOIN transitions t ON t.proc_id = p.id\n" +
     "JOIN legs l ON l.transition_id = t.id\n" +
     "WHERE p.airport = 'KOSH' AND p.ident = (SELECT min(ident) FROM procedures WHERE airport = 'KOSH')\n" +
     "ORDER BY t.id, l.seq"],
    ["Procedures by kind", "SELECT kind, count(*) AS n FROM procedures GROUP BY kind"],
    ["One airway, in order",
     "SELECT l.* FROM airways w JOIN airway_legs l ON l.airway_id = w.id\n" +
     "WHERE w.ident = 'V2' ORDER BY l.seq"],
  ],
};

export const COMMON_QUERIES = [
  ["Pack header (pack_meta)", "SELECT * FROM pack_meta"],
  ["Tables and indexes", "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"],
];
