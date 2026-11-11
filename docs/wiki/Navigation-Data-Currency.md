# Navigation Data Currency

A Garmin-style subscription for your homebuilt EFIS: pull signed navigation
packs at the hangar (or from a USB stick), and the display tells you when
something is stale. This is the original purpose of makerplane-data and it is
**live** at [navdata.aerocommons.org](https://navdata.aerocommons.org).

For the trust model and the atomic-install safety contract, read
**[[Architecture]]** first — this page assumes it.

## What a "pack" is

A **pack** is one self-describing data file for one dataset and one cycle — for
example, all US airports for the current 28-day cycle. Every pack is
self-identifying: it carries its own metadata (its kind, cycle, effective and
expiry dates, and data attribution) *inside* the file, so a lone pack on a USB
stick still knows what it is.

### Pack kinds shipping today

| Kind | Contents | Source | Terms |
|---|---|---|---|
| `navdata` | Airports & runways | FAA NASR | Public domain |
| `navaids` | VORs/NDBs, fixes, airways | FAA NASR | Public domain |
| `obstacles` | Towers & antennas | FAA DOF | Public domain |
| `terrain` | Elevation tiles, by region | Copernicus GLO-30 | Redistributable |
| `water` | Coastlines, lakes, reservoirs | OpenStreetMap | ODbL (attribution) |
| `highways` | Motorways & trunk roads | OpenStreetMap | ODbL (attribution) |

Instrument approach procedures (`CIFP`) are recognised but deferred — its
indexer is GPL-licensed and this side of the repo is permissively licensed, so
they are built with separate tooling rather than folded in here.

On the aircraft, installed data lands under `/data/makerplane-data/<kind>/current/…`,
where the EFIS (and its synthetic-vision terrain renderer) reads it.

## The three legs

The system is built as three independent legs joined by the signed
`manifest.json` catalog (see [[Architecture]] → *The manifest is the contract*).

### Leg 1 — the build pipeline (`packtools/`)

A GitHub Actions job runs daily. For each dataset it computes the current (and
next) cycle, and — only if that pack isn't already published — fetches the
upstream data, builds it into a pack, records its SHA-256, uploads it, and
refreshes and re-signs the catalog. It is idempotent: a normal day where nothing
new has been published does nothing. When the FAA publishes the next cycle ahead
of time, the pipeline picks it up automatically so the aircraft can pre-stage it.

The pieces: FAA/AIRAC date arithmetic, the minisign signer, the pack-metadata
embedder, the catalog schema, the upstream-URL registry, the fetcher, and the
orchestrator that ties them together. Deep reference:
[`docs/ci_build_pipeline.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/ci_build_pipeline.md)
and [`docs/cloudflare_setup.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/cloudflare_setup.md).

### Leg 2 — distribution

Packs and the signed catalog are served from Cloudflare R2 object storage at
`navdata.aerocommons.org` — a custom domain, edge-cached, with zero egress cost.
The live catalog is always at
[navdata.aerocommons.org/manifest.json](https://navdata.aerocommons.org/manifest.json).
The same files can be copied to a USB stick for hangars without WiFi; the device
verifies them identically.

### Leg 3 — the on-Pi updater (`pyefis_data/`)

A small program on the aircraft's Raspberry Pi keeps it current. It runs on a
daily timer, and on demand from the EFIS. It shares its signing/verifying and
catalog code with the build side, so verification can never drift from signing.

It follows the safety contract exactly: fetch and **verify the catalog signature
before trusting it**, download each stale pack to a staging area, **verify its
SHA-256 against the signed catalog, then move it into place and flip the
`current` pointer atomically**. A bad download never disturbs the live data, and
offline it falls back to the last good cached catalog. It also pre-stages the
next cycle so a rollover is seamless.

A signature proves who signed a catalog, not when. So the updater also refuses
a validly signed catalog that is **older** than the one it already trusts (an
old USB stick, a stale cache, or a replay), installs nothing from it, and shows
a `Catalog: CATALOG REFUSED` row on the Data Status screen. A deliberate
rollback is `pyefis-data update --allow-rollback` (or `import DIR
--allow-rollback`); it cannot be combined with `--dry-run`. Each use is
appended to `rollback.log` in the data root.

## Choosing what to install

A fresh aircraft automatically tracks the small, everyone-wants-them datasets
(airports, navaids, obstacles). The large, regional datasets (terrain, and
future charts) are **opt-in by region** — nothing ever forces a bulk download.

On the EFIS this is a touch **pack picker** (the Update screen): pick packs from
the catalog, choose which drive to store them on, watch byte-level progress, and
Cancel a long transfer to return to the picker — a cancel unwinds cleanly and
leaves the live data untouched. Deselecting an installed pack removes it on the
next update.

## The currency flag

The aircraft *informs*, it never restricts. A boot **Data Status** screen and a
subtle PFD annunciator report currency in green/amber, so a pilot knows at a
glance whether the terrain or airport data is current — but the EFIS never
refuses to run on stale data. That's the pilot's call.

## The command line (`pyefis-data`)

The updater is also a CLI (the touch picker drives these same commands). The main
ones:

```bash
pyefis-data status      # installed vs available, currency per pack
pyefis-data catalog     # every available pack (the picker's view)
pyefis-data update      # pull stale packs, verify, atomic-swap them in
pyefis-data import DIR   # same verified install, from a USB stick (offline)
pyefis-data verify       # check the live catalog's signature
```

(The same program also pairs the device with the configurator and pulls panel
configs — see **[[The Configurator]]**.)

## The schema guard (catching upstream drift)

Upstream providers change their file formats from time to time (the FAA
announces these as Data Product Notices). Because the build tools read data *by
column name*, a rename or a dropped column would not crash the build — it would
silently produce **wrong or near-empty** data on a green run, and that signed
pack could reach aircraft.

To prevent that, a **schema guard** runs inside the nightly pipeline, against the
live download, before anything is signed or uploaded. It runs four checks per
dataset:

1. **Structure diff** — the download's declared column layout vs a committed
   snapshot of the expected format. Any added, removed, renamed, retyped, or
   widened column fails the run with a readable diff.
2. **Required headers** — every column the builder actually reads must be
   present (the direct guard against a silently empty pack).
3. **Value vocabulary** — a policed field (e.g. an airway type code) that
   contains an unrecognised value fails, so a genuinely new category can't slip
   through untriaged.
4. **Output floors** — the built pack must have a sane number of rows; an empty
   or suspiciously small table fails.

When the guard fires, the build goes **red** and stops — the failing dataset
ships nothing (its previously published pack keeps serving), while healthy
datasets still update. A human then reviews the change (usually against the
provider's notice), refreshes the committed snapshot, and adjusts the builder if
needed. That refresh *is* the acknowledgement step: it turns "the format changed
under us" from a silent event into a reviewed one.

*Background:* this was built ahead of a real FAA format change (NASR 26-01,
effective September 2026). Working notes:
[`docs/nasr_2601_dpn_prep.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/nasr_2601_dpn_prep.md).

## Deeper reference

- Terrain packs: [`docs/terrain.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/terrain.md)
- Water & roads: [`docs/water.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/water.md),
  [`docs/roads.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/roads.md)
- The on-device pack picker: [`docs/on_device_pack_picker.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/on_device_pack_picker.md)
- The full phase plan: [`docs/data_manager_implementation.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/data_manager_implementation.md)
