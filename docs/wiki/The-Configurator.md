# The Configurator

A web app where a builder signs in, **lays out their EFIS panel visually**, and
pushes the result to a paired aircraft — live at
[pyefis.aerocommons.org](https://pyefis.aerocommons.org). It also hosts the
**[[Aircraft Parameters]]** editor (V-speeds and gauge limits), covered on its
own page.

Everything runs in a **single Cloudflare Worker**: it serves the editor UI *and*
the accounts / storage API from one origin, so your sign-in "just works" with no
cross-site complications.

## What you can do today

- **Sign in** with Google or a passwordless email magic-link.
- **Organise by aircraft.** A *project* is an aircraft (or system design); it
  holds one or more *devices* (e.g. a PFD screen, an MFD).
- **Design panels visually** — drag instruments onto a grid, set their options
  in a properties panel, arrange multiple screens, and see a **live preview that
  matches what the real EFIS draws**.
- **Push to your aircraft** — pair a device once, then the aircraft pulls its
  panel and installs it safely.

Everything is **ownership-scoped**: you can only ever see and edit your own
aircraft, devices, and designs.

## The visual editor

The editor is a canvas over pyEfis's normalised instrument grid. You place
instruments, group them (e.g. an engine cluster), target a specific screen size
and DPI, and use guides + snapping to align things. A live, two-way **YAML code
pane** shows the underlying config and edits back into the canvas.

Colour options offer **Default** (the widget's own colour), a named **colour
token** such as `ink.primary` or `nav.ownship`, or **Custom…** (a colour
picker). A token is saved into the YAML by name; the editor's **Theme** selector
(currently `pyEfis built-in`) sets the colour each token previews as.

### Device-faithful previews (the fidelity rule)

The editor's instrument previews are not approximations — they are **twins of
the real pyEfis widgets**, built to reproduce what the aircraft actually draws as
closely as the browser allows. Airspeed and altimeter tapes, the attitude
indicator, HSI, arc/bar gauges, dials, trend tapes, and synthetic-vision terrain
all render live as you change their options. The rule is deliberate: the target
look is *whatever pyEfis renders*, so when a widget improves on the EFIS side,
its twin follows. No freelancing on instrument appearance.

### Why the instrument data comes from pyEfis

A Cloudflare Worker can't run Qt, so it can't render the real instruments
itself. Instead, the **pyEfis** project generates the instrument catalog
(`schema.json`), palette thumbnails, element groups, and synthetic-vision
backdrops, and publishes them to object storage; the browser editor reads them.

This is the important two-repo rule for contributors: **if you add or change an
instrument or its options, you edit pyEfis** (its schema + widget), regenerate
those assets, and re-upload them — you do not edit the configurator's data. See
[`configurator/README.md → Editor assets`](https://github.com/billmallard/makerplane-data/blob/main/configurator/README.md#editor-assets-generated-by-pyefis).

## From editor to aircraft (device deployment)

Getting a design onto a specific aircraft closes the loop: **editor → paired
device → live screen.** It rides the same verify-then-atomic-swap safety
contract as navigation data (see **[[Architecture]]**), on a private,
token-authorised channel.

### 1. Pair the device (once)

In the dashboard, choose *Pair device* — the app shows a short, single-use claim
code that expires in a few minutes. On the aircraft you redeem it:

```bash
pyefis-data pair <claim-code>
```

The aircraft receives a long-lived **device token** (only the hash is stored
server-side) and remembers it. From then on the device authenticates as itself
over TLS. The token is revocable and the device can be re-paired.

### 2. The design *is* the device file

The editor saves your panel as **native pyEfis screen YAML** — the same format
the EFIS reads — so there is no separate "compile" step. Element groups are
expanded to concrete instruments and the screen size travels with it.

### 3. Pull and install (safely)

```bash
pyefis-data config-pull      # fetch this device's latest panel, install, restart
```

The aircraft fetches its panel (a conditional request, so an unchanged config is
a cheap "not modified"), installs it as a **managed screen** layered over the
device's stock configuration, and restarts the display. Then the safety net:

- **It confirms the EFIS actually came back up** with a working display — not
  just that the process is alive — and if the new panel would crash it, **rolls
  back to the last working panel** automatically. This has caught real crashes in
  production and kept the aircraft on its previous panel.
- **Pulls happen at boot, not in flight.** The auto-pull runs once after the EFIS
  is up on power-on; there is no periodic timer that could restart your display
  mid-flight. On-demand updates are the explicit `config-pull` command.
- The install layers over your device's existing screens rather than replacing
  them, and keeps a backup for rollback. Stock files are left untouched.

Unlike the public navigation packs, a per-device config isn't separately signed:
there's no public identity to forge, the pull is already token-authorised over
TLS, and a SHA-256 covers integrity. Full design notes:
[`docs/device_deployment.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/device_deployment.md)
and [`docs/panel_config_format.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/panel_config_format.md).

## Under the hood (for contributors)

- **One Worker, Hono router.** Static UI + `/auth/*` + `/api/*` from one origin.
- **Storage:** Cloudflare **D1** (accounts, projects, devices, config versions),
  **KV** (sign-in state), **R2** (private design blobs + public editor assets).
- **Sessions** are stateless signed cookies; all crypto uses the Workers Web
  Crypto API; the only runtime dependency is Hono.
- **Licensing:** the service is **AGPL-3.0** — run it or modify it, but a
  modified copy operated as a service must publish its source. (One file,
  `editor.html`, is GPL-3.0 because its instrument-preview twins are ported from
  GPL pyEfis widget code.)

> Server secrets (session key, OAuth credentials) and hosting tokens live only in
> the deployment environment — never in the repo or this wiki.

Orientation for AI/dev sessions:
[`configurator/CLAUDE.md`](https://github.com/billmallard/makerplane-data/blob/main/configurator/CLAUDE.md).
