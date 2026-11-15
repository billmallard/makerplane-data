# The On-Device Updater

`pyefis-data` runs on the aircraft. It installs navigation packs from the
navdata service or a USB stick, writes the status file pyEfis reads, and pairs
the device with the configurator and pulls its panel. The EFIS's touch screens
drive the same commands. Setting up a device from scratch is in the pyEfis
manual,
[Setting Up a Device](https://github.com/billmallard/pyEfis/wiki/Setting-Up-a-Device);
what the pilot sees is in
[Data Status, Updates and Notices](https://github.com/billmallard/pyEfis/wiki/Data-Status-Updates-and-Notices).
This page is the reference underneath
([`pyefis_data/`](https://github.com/billmallard/makerplane-data/tree/dev/pyefis_data)).

How it decides what to trust is on [[Architecture]]. Pairing and the panel pull
are on [[The Configurator]].

## Commands

Global options, before the command: `--config PATH` (default
`~/.makerplane/pyefis/data.yaml`), `--base-url URL` (another data origin),
`--root DIR` (another data root).

| Command | What it does |
|---|---|
| `status [--json] [--out [PATH]]` | Installed packs against the catalog, with each one's status. `--out` writes the JSON to `~/.makerplane/pyefis/status.json`, or to PATH |
| `catalog [--json] [--source DIR]` | Every available pack, as the picker lists it, with the data root's free space |
| `sources [--json]` | Where an update could come from: the network, and any USB stick with packs on it |
| `drives [--json]` | Writable drives that could hold the data root, fixed drives first |
| `update` | Download, verify and install whatever is out of date. Options below |
| `import DIR` | The same install, from a folder such as a USB stick. Takes `--dry-run`, `--accept-notice`, `--allow-rollback`, `--software` |
| `verify [PATH]` | Check a manifest's signature. With no path, fetch and check the live one |
| `notice [--json]` | Show the software notice and the accept history |
| `accept [--yes] [--software NAME=VERSION] [--trigger TEXT]` | Accept the software notice and record it |
| `advisories [--enable \| --disable] [--check] [--json]` | The opt-in safety advisory feed |
| `pair CODE [--configurator-url URL]` | Redeem a claim code from the configurator |
| `config-pull [--no-restart] [--wait-online SECONDS]` | Pull and install this device's panel, restart pyEfis, roll back if it does not come up |

`update` options:

| Option | Effect |
|---|---|
| `--dry-run` | Say what would be installed or removed, and change nothing |
| `--only ID,ID,...` | Install exactly these packs, and save that as the selection in `data.yaml` (below) |
| `--source DIR` | Install from a folder instead of the network |
| `--progress` | Print one JSON event per line, for the on-screen picker |
| `--accept-notice` | The caller has shown the full software notice and the user accepted it |
| `--allow-rollback` | Accept a signed catalog older than the one the device trusts. Logged to `rollback.log`. Cannot be combined with `--dry-run` |
| `--software NAME=VERSION` | Record this component's version in an accept (repeatable) |

Exit codes: `0` success; `2` a verification error, an unreadable or refused
catalog, or a bad argument; `3` the software notice has not been accepted; `130`
the update was cancelled. `config-pull` returns `1` when the panel installed
but pyEfis could not be restarted, or crashed and was rolled back.

### Cancelling

A `SIGTERM` during `update` (the picker's **Cancel**) stops the transfer,
deletes the partial downloads in `staging/`, prints `update canceled; current
data left untouched`, and exits 130. Installed data is not touched.

### Progress events

With `--progress`, stdout carries one JSON object per line:

```
{"event": "begin", "total": 3}
{"event": "pack", "index": 1, "total": 3, "id": "navaids-conus", "name": "..."}
{"event": "progress", "id": "navaids-conus", "done": 2097152, "total": 5812224, "pct": 36}
{"event": "notice_required", "notice": "...", "notice_id": "..."}
{"event": "canceled"}
```

`progress` events come at each whole percent. When the size is unknown they
come every 8 MB, with `total` and `pct` set to null.

## Choosing what to install: `data.yaml`

The selection and settings live in `~/.makerplane/pyefis/data.yaml`
([`pyefis_data/config.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/config.py)).
A missing or unreadable file means the defaults, and a key with the wrong type
is ignored, so a typo cannot stop the updater. A starting file with every
setting explained is at
[navdata.aerocommons.org/data.yaml.sample](https://navdata.aerocommons.org/data.yaml.sample).

| Key | Default | Meaning |
|---|---|---|
| `base_url` | `https://navdata.aerocommons.org` | Where the catalog and packs come from |
| `root` | `~/makerplane-data` | The data root, where packs are installed |
| `track_kinds` | `[navdata, navaids, obstacles, cifp, procedures]` | Kinds tracked automatically, every pack of the kind |
| `regions` | `[]` | Regions to track for the large kinds: terrain, water, roads |
| `packs` | `[]` | Extra pack ids to track, such as `airports-canada` |
| `stage_next` | `true` | Download the next cycle before it takes effect |
| `advisories` | `false` | Check the safety advisory feed |
| `configurator_url` | `https://pyefis.aerocommons.org` | The configurator this device pairs with |
| `device_token`, `device_id`, `config_version`, `config_etag` | none | Written by `pair` and `config-pull` |
| `pyefis_unit_scope` | auto-detect | `user` or `system`: where `config-pull` finds the pyEfis service |

A pack is tracked when its id is in `packs`, or its kind is in `track_kinds`,
or it is a large kind whose regions overlap `regions`. When any terrain region
is tracked, the coarse `terrain-mosaic` pack is tracked too. `cifp` is in the
default list but is not published, so it installs nothing.

`update --only` replaces the selection: it writes `packs:` with exactly the
ids given and empties `track_kinds` and `regions`. This is what the on-screen
picker does, so after using the picker the device tracks exactly what was ticked.

A pack that is no longer tracked is removed on the next update. For terrain,
only the tiles that no remaining region provides are removed. For every pack,
version folders other than the current and the staged one are deleted after
each update.

`auto_update` and `storage_budget_gb` are read from the file but have no effect.
The daily timer runs regardless of `auto_update`, and no storage limit is
applied.

## Pack status

`status` gives each tracked pack one of these
([`pyefis_data/core.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/core.py)),
checked in this order:

| Status | Severity | When |
|---|---|---|
| `unknown` | white | The pack is not in the catalog |
| `MISSING` | white | Nothing is installed |
| `EXPIRED` | amber | The installed cycle has expired |
| `update-staged` | none | A newer cycle is current and already downloaded |
| `UPDATE AVAILABLE` | amber | A newer cycle is current and not yet downloaded |
| `EXPIRES` | white | The installed cycle expires within 7 days |
| `current` | none | Otherwise |
| `CATALOG REFUSED` | white | A row of its own: an older signed catalog was offered and refused |

## `status.json`

`~/.makerplane/pyefis/status.json` is what pyEfis reads for its Data Status
screen and DATA annunciation. It sits at a fixed path outside the data root, so
pyEfis finds it wherever the data is stored. `update` rewrites it after each
run, and so does the timer's service. `import` does not; run `pyefis-data status
--json --out` after an import to refresh it. The write is atomic. Its shape,
with values shortened:

```json
{
  "ok": true,
  "generated": "2026-11-11T00:00:00Z",
  "worst": "none",
  "any_attention": false,
  "rollback_refused": null,
  "packs": [
    {"id": "navaids-conus", "name": "...", "kind": "navaids",
     "status": "current", "severity": "none", "cycle": "2612",
     "effective": "...", "expires": "...", "days": 14,
     "next_cycle": null, "next_effective": null, "next_staged": false,
     "notice": "...", "detail": "..."}
  ],
  "advisories": {"enabled": false}
}
```

`worst` is the highest severity across the packs (`none`, `white`, `amber`).
`any_attention` is true when any pack is `EXPIRED`, `EXPIRES`, `UPDATE
AVAILABLE`, `MISSING`, or the catalog was refused. When the catalog cannot be
read at all, `status --json` prints `{"ok": false, "error": "...", "packs":
[]}`.

## The daily timer and the boot pull

The units are in
[`pyefis_data/systemd/`](https://github.com/billmallard/makerplane-data/tree/dev/pyefis_data/systemd),
and each file's header says where it goes.

| Unit | Kind | What it does |
|---|---|---|
| `pyefis-data.timer` | user | Daily, with up to an hour's random delay, and catches up after a missed day (`Persistent=true`) |
| `pyefis-data.service` | user | `pyefis-data update`, then writes `status.json` |
| `pyefis-config-pull.service` | user | At boot, after pyEfis starts: `pyefis-data config-pull --wait-online 60`. No timer, so the display is never restarted in flight |
| `99-makerplane-data.rules` | udev | Starts the USB import when a partition with a filesystem is plugged in |
| `makerplane-usb-import@.service` | system | Runs `usb-import.sh` for that partition |

```bash
systemctl --user enable --now pyefis-data.timer
systemctl --user enable pyefis-config-pull.service
```

The user units expect `pyefis-data` at `~/.local/bin/pyefis-data`. With
`--wait-online 60`, `config-pull` retries every 3 seconds for up to 60 seconds,
then gives up quietly if there is no network.

## USB import

For a hangar with no network:

1. On [navdata.aerocommons.org](https://navdata.aerocommons.org), tick the packs
   and press **Download selected**. That downloads the packs and
   `manifest.json` and `manifest.json.minisig`.
2. Put them on a USB stick in a folder named `makerplane-data/`, with the packs
   in `makerplane-data/packs/`. `advisories.json` and its signature can go
   beside the manifest, but are optional.
3. Plug it into the device. Either choose **USB drive** on the EFIS's update
   screen, or run `pyefis-data import /path/to/makerplane-data`.

When the udev rule is installed, plugging the stick in is enough.
`usb-import.sh` mounts the partition read-only, runs `pyefis-data import` on its
`makerplane-data/` folder as the `pyefis` user if a `manifest.json` is there, and
unmounts it. A USB import verifies exactly as a network update does. It needs a
software-notice accept already on record, because an unattended import cannot
ask for one.

`sources` finds a stick by looking for `manifest.json` at the top of, or under
`makerplane-data/` in, each mount under `/media`, `/run/media` and `/mnt`.

## The software notice

`update` and `import` will not install until the software notice has been
accepted on the ground
([`pyefis_data/acknowledge.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/acknowledge.py),
[`docs/install_acknowledgment.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/install_acknowledgment.md)).
An accept counts for the current notice text and the current `pyefis-data`
version; a new version or a changed notice asks again. A dry run does not ask.

- At a terminal, the notice is printed and you type `accept`.
- The on-screen picker shows the notice and passes `--accept-notice`; with
  `--progress` the updater first sends a `notice_required` event.
- With no terminal and no accept, it prints the notice and exits 3, and nothing
  is fetched.

Each accept appends one record (notice text and hash, software versions,
installed packs, user, host, time) to
`~/.local/state/makerplane/acknowledgments.jsonl` (`$XDG_STATE_HOME` is honoured,
and `MAKERPLANE_ACK_LOG` overrides the path). The log is append-only and lives
outside the config folders, so resetting a config does not erase it. pyEfis
reads the same file for its startup banner and About screen.

## Safety advisories

The device can check a signed feed of safety advisories
([`pyefis_data/advisories.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/advisories.py),
[`docs/advisories.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/advisories.md)).
It is off by default.

```bash
pyefis-data advisories --enable     # saved in data.yaml; checks now
pyefis-data advisories --check      # fetch and verify now
pyefis-data advisories --disable    # nothing is fetched after this
```

When it is on, each `update` and `import` also fetches `advisories.json` and its
signature from the data origin, verifies them with the same key as the
catalog, and refuses a feed whose `sequence` is lower than the one it has. The
device sends nothing about itself: the request has no query string, no body and
no added headers.

The match runs on the device. An advisory names the software versions or pack
cycles it affects. It applies when an installed version is in range, or an
installed or staged cycle is listed; it may apply when a version is unknown.
Matching advisories go into `status.json` (`advisories.active`, with id,
severity, title, description and action) and are printed by the updater as
`ADVISORY <id> [severity] <title>`. Severity is `warning`, `caution` or
`advisory`. An advisory never blocks an update.
