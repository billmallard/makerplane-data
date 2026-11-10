# AER-5271 (FUEL1a) Fuel card on meta.fuel schema 2

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Taken 2026-11-09 in headless Chromium (Playwright's `chrome-headless-shell`,
build 1234), from `configurator/public/aircraft.html` with the API routed by
the script: it serves a profile saved by the schema-1 page (three positional
tanks: 1 and 2 feed tank 3, tank 3 feeds the engine), and the fuel group of
the live parameter schema (v1, 2026-07-04). Each image is the Fuel card at 2x.

- `1-migrated.png`: the page has migrated the profile on load. Tanks are
  labelled "Tank 1-3", keep slots FUELQ1-3, and tanks 1 and 2 feed Tank 3.
  Capacity is read from each `FUELQn.Max`.
- `2-tank2-removed.png`: tank 2 removed. Tank 3 keeps slot FUELQ3 and its
  engine edge, and tank 1 still feeds it. On the schema-1 page it would
  have become tank 2 and taken FUELQ2's values.
- `3-labelled.png`: tanks labelled "L TIP" and "HEADER" by hand (the
  page defaults no such label), and the header given 1 gal unusable.

The script then clicks Save and runs the PUT body through the Worker's
`checkAircraftProfilePut`. That run printed `save: saved` and
`FUELQ3.Max: 4, FUELQ3.Min: 0` (capacity 5 - unusable 1).

```bash
export CHROME=<path to chrome-headless-shell> PLAYWRIGHT_CORE=<path to playwright-core>
node shot.mjs ../../../configurator .     # from this directory
```
