<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Panel changed by a config pull: the Data Status cue (CERT17)

`pyefis-config-pull.service` pulls this device's panel from the configurator
at boot, unattended, and installs it if it changed. The pull is HTTPS with a
device token, but the config is not signed end to end, so its trust rests on
the configurator account and the deployment. Rollback catches a config that
crashes pyEfis. It does not catch one that boots cleanly with an instrument
bound to the wrong key. So a pull that changes the panel is shown on the
Data Status screen until a pilot acknowledges it on the ground.

## What counts as a change

The config hash is the sha256 of the served YAML exactly as pulled: panel,
`preferences:` and `fixgw:` blocks together. A pull is a change when that hash
differs from the config installed before. A `304`, and a `200` that serves the
same bytes again, record nothing; an acknowledged panel stays acknowledged.

A device that installed its panel before this feature has no record, so its
first changed pull is shown. That is conservative on purpose.

## The record

An append-only JSON-lines log beside the software-notice log, written with the
same fsynced `O_APPEND` writer:

    $XDG_STATE_HOME/makerplane/panel_changes.jsonl
    (default ~/.local/state/makerplane/panel_changes.jsonl; MAKERPLANE_PANEL_LOG overrides)

| `event` | Written when | Fields beyond `schema`, `event`, `timestamp`, `config_hash` |
|---|---|---|
| `changed` | a pull installed a config with a new hash, before pyEfis restarts onto it | `config_version`, `trigger` (`boot` for the boot unit, which passes `--wait-online`; else `manual`), `host` |
| `rolled_back` | that config crashed pyEfis and the previous panel was restored | none |
| `acknowledged` | a person acknowledged that hash | `user`, `host`, `via` (`caller` or `tty`) |

A rollback withdraws the change it names, so the cue follows the panel
actually installed. Install snapshots go one level deep, and so does this.

## status.json: what the Data Status screen reads

`config-pull` rewrites only the `panel_change` key of
`~/.makerplane/pyefis/status.json` (keeping every other key) after it records
a change, and again after a rollback, so the screen has the change the next
time it opens, with no catalog fetch. `pyefis-data status --json --out`
carries the same block. With no pull ever recorded:

```json
"panel_change": {"pending": false, "config_hash": null, "config_version": null,
                 "changed": null, "trigger": null, "acknowledged": null}
```

After a boot pull changed the panel:

```json
"panel_change": {
  "pending": true,
  "config_hash": "9f2c...e1",
  "config_version": 8,
  "changed": "2026-11-12T14:03:22Z",
  "trigger": "boot",
  "acknowledged": null
}
```

`pending` is the only field the screen needs to decide whether to show the
cue. If `status.json` did not exist yet, `config-pull` writes one with
`"ok": false` and no packs, so the pack list reads as unknown while the cue
still shows.

## Acknowledging

    pyefis-data panel-ack --yes --hash <config_hash> [--json]

The Data Status screen collects the tap, then calls this with the hash it
displayed. If the installed config is no longer that hash (a later pull
changed it again) nothing is recorded and the exit code is 2, so the screen
can never acknowledge a change it did not show. At a terminal, `panel-ack`
with no flags shows the change and asks for the word `acknowledge`. Exit 0
also when nothing is waiting. The command rewrites status.json itself.

`panel-ack` is never run by a timer or a service; a person runs it, from the
Data Status screen or a terminal. Note that the Data Status screen is not the
boot screen: since BACKLOG L3 pyEfis boots the flight screen (`defaultScreen:
PFD`), and Data Status opens from the PFD DATA flag. That flag lights from
pack currency only, so a pending panel change is not yet visible from the
PFD; whether the flag also lights for it is Bill's question on AER-5481.

## The cue (approved by Bill 2026-11-13, as written; the pyEfis side is AVIONICS's)

- **Placement:** a banner at the top of the Data Status screen, above the
  pack list, shown while `panel_change.pending` is true. Continue stays
  available, as everywhere on that screen: the EFIS informs, it never
  restricts.
- **Wording:**
  - title: `PANEL CHANGED AT BOOT` (`PANEL CHANGED` when `trigger` is `manual`)
  - body: `Config v8 installed 2026-11-12 14:03Z, hash 9f2c4b7a01de. Check every instrument shows the right source before flight.`
  - button: `Acknowledge`
- **Colour:** amber, the screen's existing attention colour.
- Reaching the screen: the banner is seen only once Data Status is open,
  which today means the pilot tapped a lit DATA flag. See "Acknowledging"
  above; the PFD side is open on AER-5481.
