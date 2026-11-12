# makerplane-data

makerplane-data is the service side of the MakerPlane avionics stack. It runs
the two websites a builder uses before the aircraft ever powers up, and the
program on the aircraft that keeps what they deliver current:

| Part | What it does | Code |
|---|---|---|
| **The configurator**, [pyefis.aerocommons.org](https://pyefis.aerocommons.org) | Design the panel (screens, instruments, tab sections, controls) and the aircraft parameters (V-speeds, gauge bands, engine and fuel limits). A paired device pulls its panel from here. | [`configurator/`](https://github.com/billmallard/makerplane-data/tree/dev/configurator) |
| **The navdata service**, [navdata.aerocommons.org](https://navdata.aerocommons.org) | Serves signed reference-data packs: airports, navaids, procedures, obstacles, terrain, water and roads. Every pack has a public test report. | [`packtools/`](https://github.com/billmallard/makerplane-data/tree/dev/packtools), [`site/`](https://github.com/billmallard/makerplane-data/tree/dev/site) |
| **The on-device updater**, `pyefis-data` | Verifies and installs packs, pairs the device with the configurator, and pulls its panel. | [`pyefis_data/`](https://github.com/billmallard/makerplane-data/tree/dev/pyefis_data) |

None of this carries live flight data. Airspeed, attitude and engine values
travel over FIX-Gateway and CAN-FIX in flight. makerplane-data carries the
reference and configuration data the avionics read: the things that change on a
cycle or at the hangar, never in the air.

## Start here

- **Building a panel or setting up a device?** Start with the system manual in
  the [pyEfis wiki](https://github.com/billmallard/pyEfis/wiki): it walks the
  configurator, device setup and the screens in the order you meet them. Its
  [How the System Fits Together](https://github.com/billmallard/pyEfis/wiki/How-the-System-Fits-Together)
  page is the one-page overview. Come back here for depth.
- **Integrating, or need to know how it is trusted?** Read **[[Architecture]]**:
  the delivery channels, the signing chain and the install rules the device
  follows.
- **Keeping navigation data current?** **[[Navigation Data Currency]]**: pack
  kinds, cycles, and the `pyefis-data` commands.
- **Working on the configurator?** **[[The Configurator]]**: the editor, device
  pairing and panel delivery, and how it runs.
- **Setting V-speeds and gauge limits?** **[[Aircraft Parameters]]**.

## Where else to look

- [pyEfis wiki](https://github.com/billmallard/pyEfis/wiki): the system manual,
  the screens and instruments, and the Data Status screen that reports pack
  currency to the pilot.
- [fix-gateway wiki](https://github.com/billmallard/fix-gateway/wiki): the data
  bus, its plugins, and the flight-plan engine that reads the navigation packs.

## Licensing (open by design)

This is an open project with no secret sauce. The architecture is published as
prior art
([`docs/AC-DP-001-architecture-disclosure.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/AC-DP-001-architecture-disclosure.md))
so it stays free to implement. Components are licensed by role: the pack tools
and the device updater are Apache-2.0 (build against them freely, with an
express patent grant); the hosted configurator service is AGPL-3.0, and its
`editor.html` is GPL-3.0 because its instrument previews are ported from pyEfis;
documentation is CC BY 4.0. Packaged data keeps its own terms (FAA public
domain, Copernicus, OpenStreetMap ODbL), and every pack carries its attribution.
Full detail:
[`docs/LICENSE-AUDIT.md`](https://github.com/billmallard/makerplane-data/blob/dev/docs/LICENSE-AUDIT.md).

The signing secret and the hosting credentials are never in the repository or
in this wiki. Only the public key is published, and that is the point.
