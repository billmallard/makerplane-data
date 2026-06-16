# Canadian airports via OurAirports

Plan to add Canadian airports to the navdata airport database, which today is
FAA-NASR-only (US). Proven by a working prototype. See also
[data_manager_implementation.md](data_manager_implementation.md).

## Problem

The SVS airport database (`airports.sqlite`) is built **exclusively from FAA
NASR** (`tools/build_airport_db.py` in pyEfis), which is US-only — it contains
**zero Canadian airports**, not even those near the border. NAV CANADA does not
provide its data for free (≈ $100k/yr + a $200M liability policy), so the gap has
stood.

### Why CIFP does not fill it

The legacy VirtualVfr renderer used FAA CIFP, which carries some Canadian
airports that have US-relevant instrument procedures. But CIFP/ARINC-424 is a
*navigation-and-procedures* database, not a surface-detail one. Its runway record
(per the parser in `pyavtools/CIFPObjects.py`) carries only:

- threshold lat/lon, runway length, magnetic bearing, threshold elevation.

It does **not** carry runway **markings** (PIR/NPI/BSC), **approach-lighting**
type (MALSR/ALSF…), or published **TDZ elevation** — those are FAA-NASR-only
attributes describing how a runway is painted and lit. So CIFP gives the same
basic runway geometry as the option below, for a *narrower* set of Canadian
airports. It is not a better source.

## Solution: OurAirports (public domain)

[OurAirports](https://ourairports.com) publishes a worldwide airport dataset in
the **public domain** (no fee, no attribution required). Its `runways.csv`
carries **per-end threshold lat/lon/elevation** — exactly what the
`runway_ends` schema needs.

Coverage for Canada (verified from the live data):

- **1,490 real airports** (large/medium/small), incl. every major (CYYZ, CYVR,
  CYUL, CYOW) and the border fields (CYQG/Windsor, CYHM, CYXX…).
- `runways.csv`: **643 CA runways** with both threshold coords directly; ~85 more
  synthesizable from airport-ref + true-heading + length; the rest are minor
  strips/heliports without geometry.

The only NASR-only fields it lacks are runway markings / approach-light codes /
TDZE — cosmetic details that the SVS only draws on close final, so Canadian
airports render correct runway shapes, orientations, elevations, and identifiers,
just without painted surface markings. **This gap is unavoidable for Canada**
regardless of source (NAV CANADA aside).

## Prototype (proven)

`work/prototype_ca_airports.py` (scratch) downloads OurAirports, filters
`iso_country == CA`, normalizes surface codes to NASR style, and writes a sqlite
in the exact schema `NASRAirportDB` reads. Verified through the same query the
SVS uses (`airports_in_range`):

```
built airports-ca.sqlite: 1490 CA airports, 728 runways (direct=643, synth=85)  352 KB
  Windsor/Detroit border:   CLM2, CYCK, CYPT, CYQG, CYZR
  Niagara/Buffalo border:   CNQ3, CNZ8, CPF6, CYHM, CYOO, CYSN, CYTZ, CYYZ, CZBA
  Vancouver/Seattle border: CYCD, CYCW, CYPK, CYVR, CYXX, CYYJ, CZBB
```

All of Canada is **352 KB** — negligible next to the existing packs.

## Production plan

1. **Importer** — add an OurAirports mode to `tools/build_airport_db.py` (or a
   sibling) that maps `airports.csv` + `runways.csv` (filtered to `CA`, or any
   `iso_country`) into the existing `airports` / `runways` / `runway_ends`
   tables: ICAO ident as `site_no`, direct thresholds where present, synthesized
   thresholds from ref+heading+length otherwise, NASR-only fields left null,
   surface codes normalized (ASP→ASPH, CON→CONC, …).
2. **Merge** — append the CA records into the **same `airports.sqlite`** the NASR
   build produces. Keys don't collide (ICAO idents vs NASR numeric site_no). The
   US path is untouched — purely additive.
3. **Pipeline** — wire it into the makerplane-data cyclical build, bump the
   airports pack edition, rebuild + OTA-deploy.

Effort: ~half a day for the importer + a pack rebuild. Generalizes to worldwide
airports trivially (drop the `CA` filter).

## Caveats / follow-ups

- **No markings / approach lighting / TDZE** for Canadian airports (see above) —
  acceptable; cosmetic on close final only.
- `NASRAirportDB.airports_in_range` skips airports with **no drawable runway**, so
  the ~768 point-only CA fields won't show even as a flag. ~560 airports (all the
  significant ones) get runways. A small enhancement to yield runway-less airports
  as flags is a separate, optional improvement.
- OurAirports is community-maintained; identifier/location quality is good, runway
  geometry is good for towered/paved fields and thinner for minor strips.

## Acceptance

A merged `airports.sqlite` shows Canadian airports in the SVS near the border in
X-Plane (e.g. CYQG, CYYZ, CYVR) with correct runway shapes/orientations and
identifiers; the US NASR airports are unchanged.
