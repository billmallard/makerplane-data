# Architecture

makerplane-data delivers three things to an aircraft: **navigation packs**, the
**panel config**, and the **aircraft parameters**. They share one rule:

> **Verify first, install in one step, and never disturb what is running.**

A download that is corrupt, tampered with or interrupted is thrown away. The
data the avionics are using is only replaced by a complete, verified file. This
page describes how each channel is trusted and what the device does with what it
receives.

## The three delivery channels

| Channel | Carries | Who gets it | How it is trusted | Reaches the device by |
|---|---|---|---|---|
| **Navigation packs** | airports, navaids, procedures, obstacles, terrain, water, roads | Everyone: one public catalog | A **signed** manifest, then a SHA-256 per pack | `pyefis-data update` (daily timer), or `pyefis-data import` from a USB stick |
| **Panel config** | the screens and instruments designed in the configurator, plus a `preferences:` block from the aircraft profile | One paired device | A per-device **token** over HTTPS | `pyefis-data config-pull`, once at boot |
| **Aircraft parameters** | V-speeds, gauge bands, engine and fuel limits | One aircraft | The builder downloads the file from their own account | `aircraft.ini`, copied by hand into FIX-Gateway's `init_data` |

The navigation channel is signed because it is public: anyone can fetch it, so
the device needs proof of who produced it. The panel config is private to one
device and is authorised by that device's token. It is not signed.

The aircraft-parameter channel is manual. The configurator's Aircraft page
builds `aircraft.ini` for download, and its footer says the device push arrives
later
([`configurator/public/aircraft.html`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/public/aircraft.html)).
One part of the profile already travels with the panel config: the altimeter
setting unit (`inHg` or `hPa`), which `config-pull` merges into the device's
`preferences.yaml.custom`. See [[Aircraft Parameters]].

## The navigation trust chain

One offline secret key signs the catalog. The matching public key is built into
the updater. The catalog vouches for each pack.

```
  keys/minisign.pub           public key, also embedded in pyefis-data
        |  verifies
        v
  manifest.json.minisig       ed25519 signature, minisign format
        |  over the exact bytes of
        v
  manifest.json               the catalog: every pack's id, kind, cycle,
        |                     dates, size, URL and SHA-256
        |  holds the SHA-256 of
        v
  each .pack file             checked byte for byte before install
```

What follows from that:

- **Taking over the website is not enough to ship a bad pack.** A forged pack
  needs a forged signature, which needs the offline secret key. Without it the
  device refuses the catalog and keeps the data it has.
- **The device trusts one key.** The public key is a constant in
  [`pyefis_data/cli.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/cli.py),
  copied from
  [`keys/minisign.pub`](https://github.com/billmallard/makerplane-data/blob/dev/keys/minisign.pub),
  so verifying needs no key file and no network.
- **The device checks the bytes it downloaded.** It never re-serializes the
  manifest before checking, so signing and verifying cannot drift apart. Both
  sides run the same code,
  [`packtools/signing.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/signing.py).
- **You do not need our tools to check a signature.** The signature and public
  key are standard minisign files:

  ```bash
  minisign -Vm manifest.json -p keys/minisign.pub
  ```

### An older catalog is refused

A signature proves who signed a catalog, not when. A validly signed catalog
whose `generated` time is older than the one the device already trusts (an old
USB stick, a stale cache, a replay) is refused: it is not cached and nothing is
installed from it. `pyefis-data status` reports a `CATALOG REFUSED` row. The
override is `pyefis-data update --allow-rollback` (or `import DIR
--allow-rollback`), and every use is appended to `rollback.log` in the data
root
([`pyefis_data/core.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/core.py)).

### Offline, and when the catalog is unreadable

With no network, the device uses the last catalog it cached, and re-verifies it
first, so unsigned bytes are never trusted. A catalog that downloads and
verifies but fails to parse is reported as an error, not as "offline", because
trying again will not fix it. A pack `kind` the device does not recognise is
dropped from the catalog, and the rest of the catalog stays usable
([`packtools/manifest.py`](https://github.com/billmallard/makerplane-data/blob/dev/packtools/manifest.py)).

### The safety advisory feed uses the same chain

The device can also check a signed safety advisory feed. It is opt-in
(`advisories: true` in `data.yaml`, off by default). It is signed with the same
key as the manifest. A feed with a lower `sequence` than the cached one is
refused, so a replay cannot hide a newer advisory. The device fetches the feed
and its signature from fixed URLs on the same site as the manifest, with no
query string and nothing that identifies it. It matches the feed against what
is installed on the device itself
([`pyefis_data/advisories.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/advisories.py)).

## The manifest is the contract

`manifest.json`, at a stable URL, holds the whole navigation system together.
Because its schema is fixed, three parts can each be rebuilt or replaced on
their own:

```
  BUILD        packtools/      fetch FAA, Copernicus and OpenStreetMap data,
                               build packs, test each one, sign the manifest
       |  packs + manifest.json + manifest.json.minisig + test reports
       v
  DISTRIBUTE                   object storage behind navdata.aerocommons.org
       |  HTTPS, or a USB stick
       v
  UPDATE       pyefis_data/    verify, stage, install, report currency
```

Every pack is tested before it is added to the manifest. Its test record is
published beside it, and the live catalog at
[navdata.aerocommons.org](https://navdata.aerocommons.org) links each pack to
its report. See [[Navigation Data Currency]].

## How the device installs a pack

The updater
([`pyefis_data/core.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/core.py))
works in this order:

1. **Verify the catalog** against the embedded key, before caching or using it.
2. **Download each pack to `staging/`** under the data root, never on top of
   the live file.
3. **Check its SHA-256** against the signed catalog. On a mismatch the
   download is deleted and the live data is left alone.
4. **Put it in place.**
   - Database packs (airports, navaids, obstacles, water, roads and the like)
     are renamed into a per-cycle folder. The kind's `current` symlink is then
     flipped in one atomic rename.
   - Terrain packs are a set of elevation tiles. The verified pack is unzipped
     into the shared `terrain/tiles/` tree, which every terrain region adds to.
     There is no `current` symlink for terrain.

A program that already has the old file open keeps reading it. The new data is
used the next time it opens the file. The data root defaults to
`~/makerplane-data` and is set by `root:` in `~/.makerplane/pyefis/data.yaml`.

The updater also downloads the next cycle before it takes effect, so a cycle
change needs no network on the day. It writes a summary to
`~/.makerplane/pyefis/status.json`, which pyEfis reads for its Data Status
screen and the DATA annunciation
([Data Status, Updates and Notices](https://github.com/billmallard/pyEfis/wiki/Data-Status-Updates-and-Notices)
in the pyEfis wiki).

## How the device installs a panel

`pyefis-data config-pull`
([`pyefis_data/config_pull.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/config_pull.py))
fetches the device's panel from the configurator with its token, then:

1. **Asks only for what changed.** It sends the last `ETag` it stored. An
   unchanged panel and profile get a `304` and nothing is touched.
2. **Takes a snapshot** of the current panel into `.panel_backup/` in the pyEfis
   config folder.
3. **Writes the panel as managed files** beside the shipped config, each with
   an atomic write. The shipped config files are never modified.
4. **Restarts pyEfis and checks it came up.** It counts as up only if the
   process ID stays the same and its log shows no traceback. A process ID alone
   is not enough: a screen that fails to build can leave the process running
   with no display.
5. **Rolls back** to the snapshot if pyEfis did not come up.

The pull runs once at boot, after pyEfis has started
(`pyefis-config-pull.service`). There is no timer, so the display is never
restarted in flight by an automatic pull. The device is paired once with
`pyefis-data pair <code>`. See [[The Configurator]].

## Two contracts, kept apart

- **The flight-data contract:** live values (airspeed, attitude, engine data)
  moving over FIX-Gateway and CAN-FIX in flight. Not this repository.
- **The reference and configuration contract:** the packs, panels and
  parameters that change on a cycle or at the hangar. This repository.

Keeping them apart is deliberate. What updates at the hangar is never in the
path of what runs in the air.

## Where to go next

- **[[Navigation Data Currency]]**: pack kinds, cycles, the build and the
  updater commands.
- **[[The Configurator]]**: the editor, pairing and panel delivery.
- **[[Aircraft Parameters]]**: the V-speed and gauge-band profile.
