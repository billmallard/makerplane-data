# Pairing and Config Delivery

A device gets its panel from the configurator over a private channel: it is
paired once, receives a token of its own, and from then on pulls the panel
saved for it, at boot. This page covers each step from both ends: the service
([`configurator/src/index.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/index.ts))
and the device
([`pyefis_data/config_pull.py`](https://github.com/billmallard/makerplane-data/blob/dev/pyefis_data/config_pull.py)).
How this channel is trusted compared with the navigation packs is on
[[Architecture]].

## 1. Pair the device

On the dashboard, press **Pair** (or **Re-pair**) on the device. The page shows
the command to run on the device, with a code:

```bash
pyefis-data pair ABCD2345
```

- The code is 8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (no `I`,
  `L`, `O`, `0` or `1`), is valid for 15 minutes, and works once.
- `pyefis-data pair` sends it to `/device/pair` and receives a **device token**
  (32 random bytes, base64url). The service stores only the token's SHA-256,
  never the token itself.
- The device saves `configurator_url`, `device_token` and `device_id` in
  `~/.makerplane/pyefis/data.yaml` and prints
  `paired as '<name>' (device <id>); token saved to <path>`.
- A bad or expired code gets `invalid or expired code`.

To pair with a configurator other than `https://pyefis.aerocommons.org`, pass
`--configurator-url`.

**Re-pairing** replaces the token. The old token stops working when the new code
is redeemed, not when the code is shown. **Deleting the device** in the service
makes its token unknown, and the device's pulls get `401 unknown device`. There
is no separate revoke step.

## 2. Save a panel

**Save** in the Panel Editor stores the panel as a new numbered version for that
device. Earlier versions are kept. The dashboard then shows `update pending` on
the device until it next pulls.

## 3. What the device downloads

`GET /device/config`, with `Authorization: Bearer <device token>`, returns the
newest version:

| Response | When |
|---|---|
| `200`, `application/x-yaml`, with `ETag` and `X-Config-Version` | A panel the device does not have yet |
| `304` | The `If-None-Match` the device sent matches: nothing changed |
| `404 no config yet` | Nothing has been saved for this device |
| `401` | No token, or a token the service does not know |

Every request, including a `304`, updates the device's **pulled** time on the
dashboard.

The body is the panel YAML as saved. When the project's aircraft profile sets
an altimeter unit, a `preferences:` block is added after it:

```yaml
preferences:
  units:
    Pressure: inHg      # or hPa
```

The ETag is `"v<version>"`, or `"v<version>-p<hash>"` when a preferences block
is added, so changing only the aircraft profile's unit still counts as a change
([`configurator/src/device_prefs.ts`](https://github.com/billmallard/makerplane-data/blob/dev/configurator/src/device_prefs.ts)).
Nothing else from the aircraft profile is sent yet; see [[Aircraft Parameters]].

The channel is authorised by the token over HTTPS. Unlike the navigation
catalog, the panel is not signed.

## 4. Install it: `pyefis-data config-pull`

```bash
pyefis-data config-pull                    # pull, install, restart pyEfis
pyefis-data config-pull --no-restart       # pull and install only
pyefis-data config-pull --wait-online 60   # retry every 3 s for up to 60 s with no network
```

It runs once at every boot, after pyEfis has started, from
`pyefis-config-pull.service` (`config-pull --wait-online 60`). There is no
timer, so a pull never restarts the display in flight. The unit file is in
[`pyefis_data/systemd/`](https://github.com/billmallard/makerplane-data/tree/dev/pyefis_data/systemd).

### What it writes

The panel goes into the pyEfis config folder, `~/makerplane/pyefis/config`
(or `$PYEFIS_CONFIG_DIR`), as **managed files** beside the shipped ones. The
shipped files are never modified, and every write is atomic.

- **One screen:** written to `screens/managed.yaml` under the name of the
  device's existing boot screen (`defaultScreen` in `main/default.yaml`,
  `PANEL` if none). `preferences.yaml.custom` points that screen's include at
  the managed file, so the device boots straight into the panel.
- **Several screens:** each is written to `screens/managed_<name>.yaml`. The
  panel's default screen takes the boot screen's name. The others are added to
  a copy of the device's own screen list, `screens/managed_list.yaml`, so the
  stock screens stay available. Each screen gets a `SCREEN >` button
  (`buttons/managed-next-<name>.yaml`) that moves to the next panel screen, so a
  touch-only panel can reach them all.
- **Synthetic vision:** each synthetic-vision instrument gets the paths to the
  installed terrain, water and road data.
- **Units:** the `preferences:` block's units are merged into
  `preferences.yaml.custom`. A unit you set by hand is never removed; only a
  unit `config-pull` itself wrote is removed when the profile stops setting it
  (recorded in `.panel_prefs.yaml`).

### Backup, restart, rollback

1. **Before writing**, the current panel files are copied to `.panel_backup/`
   in the config folder. The very first install also keeps the original
   `preferences.yaml.custom` as `preferences.yaml.custom.prepanel`.
2. **After writing**, pyEfis is restarted and watched for 16 seconds. It counts
   as up only if its process ID stayed the same, the service is active, and its
   log for that process shows no `Traceback`, `Fatal Python error`,
   `Segmentation fault` or `Unable to load module`. A steady process ID alone is
   not enough: a screen that fails to build can leave pyEfis running with no
   display.
3. **If it did not come up**, the backup is restored, the stored version is
   reset, pyEfis is restarted on the previous panel, and the command prints
   `new config crashed pyEfis on load; rolled back to ...` and exits 1.
4. **If the restart itself could not run** (for example the pyEfis service is
   in a systemd scope `config-pull` cannot reach), the new panel stays
   installed, the command says how to restart by hand, and exits 1.

`config-pull` finds the pyEfis service in the user or the system scope on its
own. `pyefis_unit_scope: user` or `system` in `data.yaml` sets it.

### What it prints

| Message | Exit |
|---|---|
| `installed panel config v<n> (boot screen: <name>)` (or `-- <k> screens ...`), then `restarted pyEfis -- panel is up` | 0 |
| `panel config up to date (v<n>)` | 0 |
| `no panel config published for this device yet` | 0 |
| `not paired -- run pyefis-data pair <code> first` | 2 |
| `config pull failed: ...` (a network error) | 2 |
| `could not reach configurator after <n>s (...); ...` (with `--wait-online`) | 0 |
| `config install skipped: ...` (the served file is not a pyEfis config) | 2 |
| `installed panel config v<n>, but could not restart pyEfis ...` | 1 |
| `new config crashed pyEfis on load; rolled back to ...` | 1 |

On success it stores the new `config_version` and `config_etag` in `data.yaml`,
so the next pull sends `If-None-Match` and gets a `304` when nothing changed.

## Further reading

- [`docs/device_deployment.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/device_deployment.md):
  the design of this channel.
- [`docs/panel_config_format.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/panel_config_format.md):
  the panel file format.
