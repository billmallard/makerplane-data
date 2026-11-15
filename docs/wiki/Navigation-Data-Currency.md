# Navigation Data Currency

The navdata service keeps the reference data on an EFIS current: airports,
navaids, procedures, obstacles, terrain, water and roads. Packs are built from
public sources, tested, signed, and served at
[navdata.aerocommons.org](https://navdata.aerocommons.org). On the aircraft,
`pyefis-data` installs them and tells the pilot when one is out of date.

The pilot's side of this (the Data Status screen and the DATA annunciation) is
in the pyEfis manual:
[Data Status, Updates and Notices](https://github.com/billmallard/pyEfis/wiki/Data-Status-Updates-and-Notices).
For the trust model, read [[Architecture]] first.

## The pages in this section

| Page | Covers |
|---|---|
| [[The navdata Website]] | The catalog, the pack inspector, the test reports and the format pages, with pictures. |
| [[Pack Format and the Manifest]] | What is inside a pack, every `manifest.json` field, and the signature. |
| [[Building and Publishing Packs]] | The daily build, the hand-started builds, the checks every pack must pass, signing, and taking a pack down. |
| [[Pack Reports and Testing]] | The test record each pack carries, what each check can and cannot catch, and how to report a data problem. |
| [[The On-Device Updater]] | `pyefis-data`: commands, `data.yaml`, the timer, USB import, `status.json`, the software notice and the advisory feed. |

## What a pack is

A pack is one file holding one dataset for one cycle or edition, for example the
US airports for AIRAC 2612. Each pack carries its own header (`pack_meta`) with
its id, kind, cycle, dates and attribution, so a pack on a USB stick still says
what it is. See [[Pack Format and the Manifest]].

A pack id names the dataset and its coverage (`airports-conus`,
`terrain-us-west`). A cycle names the edition: an AIRAC id such as `2612`, an
FAA obstacle date such as `261001`, or an edition tag such as `2024edr4`.

## Pack kinds

These kinds are in the live catalog
([`packtools/sources.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/sources.py),
[`packtools/packmeta.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/packmeta.py)):

| Kind | Pack ids | Contents | Source | Cadence | Terms | On the device |
|---|---|---|---|---|---|---|
| `navdata` | `airports-conus` | Airports and runways | FAA NASR | AIRAC, 28 days | Public domain | Tracked by default |
| `navaids` | `navaids-conus` | VORs, NDBs, fixes and airways | FAA NASR | AIRAC | Public domain | Tracked by default |
| `procedures` | `procedures-conus` | Instrument procedures, airways, MSAs and grid MORAs | FAA CIFP | AIRAC | Public domain | Tracked by default |
| `obstacles` | `obstacles-conus` | Towers, antennas and other obstacles | FAA Digital Obstacle File | 56 days | Public domain | Tracked by default |
| `airports` | `airports-canada` | Airports outside the US | OurAirports | Edition | Public domain | Add it by id |
| `terrain` | `terrain-<region>`, `terrain-mosaic` | Elevation tiles for synthetic vision | Copernicus GLO-30 | Edition | Redistribution permitted | Opt in by region |
| `water` | `water-na` | Coastlines and inland water | OpenStreetMap, Natural Earth | Edition | ODbL, attribution | Opt in by region |
| `highways` | `highways-conus` | Motorways and trunk roads | OpenStreetMap | Edition | ODbL, attribution | Opt in by region |

- **Procedures** is published, but no source independent of the FAA has yet
  been compared against it. Its test report says so (check `PROC-DIFF-001`,
  not run).
- **The terrain mosaic** is a small, coarse whole-continent pack. The updater
  adds it automatically when any terrain region is selected
  ([`pyefis_data/core.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/core.py)).
- **Sizes** run from a few MB for the FAA packs to several GB for each terrain
  region and for water. The catalog on the website lists each one.

These kinds are defined but not published, and the updater cannot install
them yet:

| Kind | State |
|---|---|
| `cifp` | Deferred. The raw CIFP indexer is GPL-licensed and these tools are permissively licensed. The `procedures` pack is built from the same FAA file instead. |
| `airspace` | A builder exists, but publishing is switched off until its checks are accepted (`implemented=False` in `packtools/sources.py`). |
| `plates` | `packtool make-plates` builds FAA d-TPP plate packs by hand. No workflow publishes them. |
| `rivers` | Reserved. There is no builder. |

## Regions

Large datasets are split by region
([`packtools/regions.yaml`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/regions.yaml)).
Each region is a box, `[lat_min, lon_min, lat_max, lon_max]`:

| Region | Name | Box |
|---|---|---|
| `conus` | Continental US | 24, -125, 50, -66 |
| `us-west` | US West | 31, -125, 49, -102 |
| `us-central` | US Central | 25, -104, 49, -90 |
| `us-east` | US East | 24, -90, 49, -66 |
| `us-south` | US South | 24, -107, 37, -75 |
| `alaska` | Alaska | 51, -177, 72, -129 |
| `canada-west` | Canada West | 48, -140, 72, -100 |
| `canada-east` | Canada East | 42, -100, 72, -52 |
| `mexico-central-america` | Mexico & Central America | 7, -118, 33, -77 |

The regions overlap. A pack lists the regions it covers, and the manifest
carries the region table, so the device can match a selection to packs.

## Cycles

The cycle dates come from
[`packtools/cycles.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/cycles.py):

- **AIRAC** cycles are 28 days on a fixed grid, anchored at 2024-04-18 (AIRAC
  2404). The id is `YYNN`: the year, then the cycle's number within that year.
  A cycle is valid from its effective date up to, but not including, the next
  one.
- **Obstacle (DOF)** packs use a 56-day grid from the same anchor. The id is
  the effective date as `YYMMDD`.
- **Edition** packs (terrain, water, roads, OurAirports) have no expiry. The
  tag names the build, for example `2026q4r1`.

The FAA posts the next AIRAC cycle before it takes effect. The daily build
publishes it as soon as it appears, and the updater downloads it ahead of time,
so the change of cycle needs no network on the day.

## What the device installs, and where

By default a device tracks `navdata`, `navaids`, `obstacles` and `procedures`
for the continental US. Everything else is opt-in: by region for terrain,
water and roads, and by pack id for any pack. The updater stores packs under
its data root, `~/makerplane-data` unless `data.yaml` sets `root:`:

| Kind | Path under the data root |
|---|---|
| `navdata` | `navdata/current/airports.sqlite` |
| `navaids` | `navaids/current/navaids.sqlite` |
| `obstacles` | `obstacles/current/obstacles.sqlite` |
| `procedures` | `procedures/current/procedures.sqlite` |
| `water` | `water/current/water.sqlite` |
| `highways` | `highways/current/highways.sqlite` |
| `airports` | `airports/<pack id>/current/airports.sqlite` |
| `terrain` | `terrain/tiles/`, one tree shared by every region |

`current` is a symlink to the folder for the installed cycle. Selection,
commands and status are on [[The On-Device Updater]].

## The currency flag

The EFIS informs the pilot; it never restricts. Each pack's status (current,
update available, expiring within 7 days, expired, missing) goes into
`status.json`. pyEfis shows it on its Data Status screen and raises the DATA
annunciation when something needs attention. It never refuses to run on old
data. That is the pilot's call.
