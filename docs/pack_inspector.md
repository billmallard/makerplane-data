# Pack inspector

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

**Status:** 2026-11-01 (AER-4570, testing-visibility item TV6).

`https://navdata.aerocommons.org/inspect.html` is a read-only SQL page over the
published pack bytes. Anyone can open an airports, navaids, obstacles or
procedures pack in a browser and query it, with nothing to install. It is the
in-browser half of "check it yourself"; `packtool report --check` is the other.

| File | What |
|---|---|
| `site/inspect.html` | The page. Deployed by `site.yml` with the rest of `site/`. |
| `site/assets/inspect-core.js` | Signature check, sha256 gate, locator parsing, canned queries. No DOM, so it is tested in node. |
| `tests/inspector/inspect_core.test.mjs` | Node tests of that module. |
| `tests/test_inspector.py` | Python signer -> JS verifier round trip, the kind list, the report-page links. |

## What it trusts, in order

The page follows the device's order (`pyefis_data/core.py`). Nothing reaches
SQLite until every step has passed, and the page shows each step as it goes.

1. Fetch `manifest.json` and `manifest.json.minisig` uncached. Verify the
   minisign signature against the production public key embedded in
   `inspect-core.js`. It is the same key as `keys/minisign.pub` and the one
   embedded in `pyefis_data/cli.py`, and a test holds them together. The
   check uses the browser's own WebCrypto Ed25519. The trusted-comment
   signature is checked too. If the signature does not verify, the page
   refetches both files once, because a read can race a publish and pair a
   new body with an old signature. A second failure stops the page.
2. Only the legacy `Ed` signature type is accepted. That is what
   `packtools.signing` writes. A prehashed `ED` signature needs BLAKE2b,
   which WebCrypto does not have, so the page refuses it rather than checking
   half of it.
3. Take the pack's entry from the manifest whose signature just passed. If a link pins a
   `sha256` that differs from the entry's, stop: the link was made for
   different bytes.
4. Download the pack from the entry's `url`. Check its length against
   `bytes` and its SHA-256 against `sha256`. Any difference stops the page.
5. Open the bytes with sql.js 1.15.2. The script and its wasm both come from
   cdnjs, each pinned by an SRI hash, so a changed file on the CDN fails to
   load rather than running. Then run `PRAGMA query_only = 1`. The database
   exists only in the page's memory, and with this pragma a write statement
   fails even there.

Browser support: Ed25519 in WebCrypto is in Chrome/Edge 137+, Firefox 129+
and Safari 17+. An older browser cannot run the check, so the page stops at
step 1 and opens nothing.

## Kinds

The page opens `navdata`, `airports`, `navaids`, `obstacles` and
`procedures`: one SQLite file each, at most about 80 MB today, read whole.
`water` and `highways` are SQLite too, but range from hundreds of MB to
several GB. They need a range-read mode that fetches pages over HTTP `Range`,
which is a follow-on. `terrain` is a zip of tiles, not SQLite.

`packtools.reportsite.INSPECTOR_KINDS` and `SQLITE_KINDS` in
`inspect-core.js` name the same list; a test fails if they drift.

## Links: the locator format

Everything after `?` is optional. Unknown fields are ignored.

| Field | Meaning |
|---|---|
| `pack` | Pack id, e.g. `obstacles-conus`. With no `pack`, the page waits for a choice. |
| `cycle` | Edition. With no `cycle`, the edition in effect today, else the newest listed. |
| `sha256` | Pin. The page opens the pack only if the signed catalog lists exactly this hash for it. |
| `table` | A table in the pack. A name the pack does not have is an error. |
| `row_id` | With `table`: the row whose SQLite `rowid` is this value. |
| `lat`, `lon` | With `table` and no `row_id`: rows within 0.05 degrees (about 3 nm) of the point, if the table has `lat` and `lon` columns. |
| `q` | A SQL query, run as given. Takes precedence over `table`. |

`table` with neither `row_id` nor a point shows the first 100 rows.
The locator's query appears in the editor with its values written in, so
it can be re-run or edited as shown. Only finite numbers are accepted for
`row_id`, `lat` and `lon`, and `table` must name a table the opened pack
has. Results show at most 1,000 rows.

Examples:

```
inspect.html?pack=navaids-conus&q=SELECT+*+FROM+navaids+WHERE+id+%3D+%27OSH%27
inspect.html?pack=obstacles-conus&cycle=261001&sha256=<hex>&table=obstacles&row_id=7
inspect.html?pack=obstacles-conus&table=obstacles&lat=43.98&lon=-88.56
```

**Report findings.** A finding's `locator` in a test record
(`docs/pack_report.md`) carries `table`, `row_id`, `lat` and `lon` among its
fields. On a report page, each finding on a kind the inspector opens gets an
**open in the inspector** link. The link carries those four fields, plus the
record's pack id, cycle and sha256, so it opens the exact bytes the finding
was made against. Other locator fields (`hole`, for example) are shown on the
report page but not used by the inspector. The link is the locator, not a
judgement: the page shows the row, and the reader decides.

`row_id` is read as the SQLite `rowid`. `docs/pack_report.md` does not yet
say what `row_id` means. Until it does, this page's reading is the one that
applies here.

## Canned queries

Each kind has a few starting queries, plus two for every pack: the
`pack_meta` header and the `sqlite_master` listing. Each was run against the
live pack of its kind on 2026-11-01, and each returned rows. Clicking one
puts it in the editor and runs it. Edit the identifier in the query to look
at a different airport, fix or airway. The FAA airport pack keys airports by
FAA location id (`OSH`), while the procedures and OurAirports packs use ICAO
(`KOSH`, `CYYZ`).

> Experimental / Amateur-Built use only. Not certified. This data is provided
> as-is with no warranty and any use of the data is at your own risk.
