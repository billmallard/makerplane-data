# Pack Format and the Manifest

This page is for anyone reading packs outside pyEfis, or writing a client of
their own. The format of each kind's contents, table by table, is published on
the website at [navdata.aerocommons.org/schema/](https://navdata.aerocommons.org/schema/index.html),
generated from the live packs. This page covers what is common to all of them:
the pack header, the catalog, and the signature.

## The pack file

A pack is one of two containers:

- **SQLite** for every database kind: `navdata`, `navaids`, `obstacles`,
  `procedures`, `airports`, `water`, `highways`.
- **Zip** for `terrain`: `<N|S>NN/<name>.hgt` elevation tiles, with reduced
  `.mip/` levels and a `.wmask` water mask beside each tile
  ([`packtools/make_terrain.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/make_terrain.py)).

Packs are served as `packs/<id>-<cycle>.pack`, whatever the container.

### The header: `pack_meta`

Every pack carries its own header, so it still identifies itself away from the
catalog
([`packtools/packmeta.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/packmeta.py)):

- In a SQLite pack it is a table, `pack_meta(key TEXT PRIMARY KEY, value
  TEXT)`, with every value stored as text.
- In a zip pack it is a member, `pack_meta.json`.

| Key | Meaning |
|---|---|
| `id` | Stable pack id, such as `navaids-conus` |
| `kind` | One of the pack kinds; anything else is refused |
| `cycle` | AIRAC id (`2612`), obstacle date (`261001`) or edition tag (`2024edr4`) |
| `effective` | ISO date the data takes effect. Absent for edition packs |
| `expires` | ISO date it stops being current, exclusive. Absent means it does not expire |
| `attribution` | The text to show the pilot, such as `FAA NASR (public domain)` |
| `license` | A short tag: an SPDX id such as `ODbL-1.0`, or a `LicenseRef-` name |
| `license_url` | Link to the licence text, where there is one |
| `pyefis_commit` | The pyEfis commit whose build tools produced the pack, for kinds built by pyEfis tools |
| `schema_version` | The version of the pack's format |

A reader ignores keys it does not know. `schema_version` is 1 for most kinds.
For `highways` (1 or 2) and `procedures` (1 to 3) the build reads it from the
tables the pack actually contains. A reader should still check for the tables
and columns it needs, because editions packed before a kind's rule existed say
1 whatever they hold.

## The catalog: `manifest.json`

One JSON document lists every published pack
([`packtools/manifest.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/manifest.py)).
It lives at
[navdata.aerocommons.org/manifest.json](https://navdata.aerocommons.org/manifest.json).

Top level:

| Field | Meaning |
|---|---|
| `manifest_version` | `1` |
| `generated` | When the catalog was signed, ISO-8601 UTC. The device refuses a catalog older than the one it holds |
| `packs` | One entry per published edition |
| `regions` | The region table: `{"<region>": {"name": ..., "bbox": [lat_min, lon_min, lat_max, lon_max]}}` |

Each entry in `packs`:

| Field | Required | Meaning |
|---|---|---|
| `id`, `kind`, `cycle` | yes | As in `pack_meta` |
| `bytes` | yes | Size of the pack file |
| `sha256` | yes | SHA-256 of the pack file, 64 hex characters |
| `url` | yes | Where to download it |
| `effective`, `expires` | no | The currency window. An entry with `expires` must also have `effective` |
| `regions` | no | Region ids the pack covers |
| `attribution`, `license`, `license_url` | no | As in `pack_meta` |
| `origin` | no | `federal`, `community` or `user-supplied` |
| `notice` | no | The standing notice shown with the data |
| `report_url`, `report_sha256` | no | The pack's test record and its SHA-256 |
| `build_commit` | no | The makerplane-data commit that built the pack |
| `min_pyefis` | no | Oldest pyEfis that can read it |
| `tiles_bbox` | no | For terrain, the box the tiles cover |

The build refuses to sign a catalog that breaks these rules: a missing required
field, an unknown kind, a malformed hash, or the same `(id, cycle)` twice. On
the build side every new edition must also carry a test record
(`report_url` and `report_sha256`) and the notice. See
[[Building and Publishing Packs]].

### Which edition is current

A pack id can have more than one edition listed, normally the current cycle and
the next. Each daily build prunes the catalog to the two newest editions of
each id. An edition is
current on a given day when that day falls in `[effective, expires)`. An entry
with no `effective` covers every day. When more than one covers the day, the
newest wins.

The device installs the current edition, and downloads the next one (the
earliest `effective` after today) in advance without switching to it.

### Unknown kinds

A client reading the catalog drops entries whose `kind` it does not know and
uses the rest. The build side stays strict and never signs an unknown kind. New
kinds can therefore be added without breaking older devices.

## The signature

`manifest.json.minisig` is an ed25519 signature in
[minisign](https://jedisct1.github.io/minisign/) format over the exact bytes of
`manifest.json`
([`packtools/signing.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/signing.py)).
The public key is
[`keys/minisign.pub`](https://github.com/billmallard/makerplane-data/blob/dev/keys/minisign.pub).
Verify with the stock tool:

```bash
curl -O https://navdata.aerocommons.org/manifest.json
curl -O https://navdata.aerocommons.org/manifest.json.minisig
minisign -Vm manifest.json -p keys/minisign.pub
```

or with this repository's tools:

```bash
packtool verify manifest.json --pub keys/minisign.pub
```

Then check a pack against its entry:

```bash
sha256sum navaids-conus-2612.pack   # must equal the entry's sha256
```

Verify the bytes you downloaded. The signer writes the catalog as
`json.dumps(indent=2, sort_keys=True, ensure_ascii=False)` plus a newline, but
the device never re-serializes before checking, and neither should you.

## Further reading

- [`docs/pack_report.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/pack_report.md):
  the `pack-report/1` test record format.
- [`docs/advisories.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/advisories.md):
  the safety advisory feed format.
