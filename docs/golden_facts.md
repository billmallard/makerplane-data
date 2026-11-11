# Golden facts: adding a published value

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

`packtools/data/golden_facts.yaml` is a list of published values (a field
elevation, a runway length, a navaid frequency) that every new edition of
a pack is compared against. The check is `GLD-001` on the pack's test
record ([pack_report.md](pack_report.md)). Anyone can add a fact by pull
request. It is the easiest way to help, and every fact you add is one
more thing a broken build cannot slip past unnoticed.

A fact that does not match is shown on the record as a `warn` for a person
to look at. It is not proof that the pack is wrong, or that the
publication is: runways get extended and frequencies change.

## The one rule

**Take the value from the publication named in `source_url`, never from
one of our packs.** A value copied out of a pack only shows that the pack
agrees with itself. If you cannot point at a public document that shows
the value, do not add it.

Good sources: the FAA Chart Supplement (the airport's own page, linked from
the [d-CS search](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dafd/)),
an FAA Form 5010 airport master record, or a sectional chart for an
obstacle. All of these are FAA products, as the NASR and DOF files behind
the packs are. A fact therefore checks our build against the FAA's own
publication. It does not check the FAA against the world, and the record
says so.

## An entry

```yaml
- id: apt-SBA-rwy-07-25-length          # unique; any readable slug
  kind: navdata                          # the pack kind the fact is checked in
  fact: runway_length_ft                 # one of the types below
  key: {airport: "SBA", runway: "07/25"} # exactly the key fields of the type
  value: 6052
  tolerance: 0                           # how far the pack may be from value
  source: "FAA Chart Supplement, Southwest, 29 OCT 2026 to 24 DEC 2026, page 283"
  source_url: https://aeronav.faa.gov/afd/29oct2026/sw_283_29OCT2026.pdf
  added: 2026-11-01
```

Every field is required. `source_url` must be an `https://` link to the
document itself.

## Fact types

The set is closed: each type is one fixed query in `packtools/golden.py`,
so a fact is data, never SQL. To add a type, open an issue or change
`FACT_TYPES` in the same pull request.

| `fact` | `kind` | `key` | Compared with | Notes |
|---|---|---|---|---|
| `airport_elevation_ft` | navdata | `airport` | `airports.elev_ft` | `airport` is the FAA location identifier, as in the Chart Supplement's first parentheses: `SBA`, not `KSBA`. The Chart Supplement rounds to the foot, so use `tolerance: 0.5`. |
| `runway_length_ft` | navdata | `airport`, `runway` | `runways.length_ft` | `runway` as the pack writes it, `07/25` (the Chart Supplement prints `07–25`). |
| `navaid_frequency_mhz` | navaids | `navaid`, `type` | `navaids.freq` | `type` as in the pack: `VOR/DME`, `VORTAC`, `TACAN`, `NDB`, ... (the Chart Supplement's `VORW/DME` is `VOR/DME`, `VORTACW` is `VORTAC`). Use `tolerance: 0.001`. |
| `navaid_elevation_ft` | navaids | `navaid`, `type` | `navaids.elev_ft` | The figure before the slash in the Chart Supplement's `3598/14E`. |
| `obstacle_amsl_ft` | obstacles | `oas_no` | `obstacles.amsl_ft` | `oas_no` is the DOF obstacle number, for example `04-086611`. |

A key that names more than one row is reported as `ambiguous` rather than
guessed. Navaid identifiers are reused (`BOS` is both a VOR/DME and a VOT),
which is why the navaid key includes the type.

## Before you open the pull request

```bash
python -m pytest tests/test_catalogue_v2.py -q    # the file must load cleanly
packtool report <pack> --no-device                # GLD-001 shows your fact
```

`golden.load()` refuses a malformed file and names each problem, so a bad
entry fails the tests rather than a build.
