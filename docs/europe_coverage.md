# Europe coverage — research notes & TODO

**Status: not started. This is a parking lot for future Bill.** Everything
shipped today is North America (FAA + OSM/Geofabrik + Copernicus). This doc
captures what extending to Europe would and wouldn't be able to do, and why,
so the licensing traps are written down before anyone sinks time into it.

## The one-line summary

Europe does **not** publish airport/obstacle data the way the FAA does. The
FAA model — a single authority, **public domain**, clean machine-readable bulk
downloads, free — is the unusual case. Europe is fragmented across ~40 national
authorities, the data is generally **copyrighted** (redistribution restricted),
often PDF-format, and the central database is access-controlled. The blocker is
**licensing, not availability.**

## Why the FAA is the easy case

Three things line up for the FAA that do not line up in Europe:

- **One authority** publishes for the whole country (NASR, DOF, CIFP).
- **Public domain** — US Government works carry no copyright, so we can rebuild
  and redistribute freely. That is the entire legal basis for our packs.
- **Clean, free, machine-readable bulk downloads** on a predictable cycle.

## Europe's reality

- **Fragmented by country.** No single European NASR/DOF. Each state's ANSP
  publishes its own AIP — DFS (Germany), SIA/DGAC (France), NATS (UK), ENAIRE
  (Spain), and ~40 more. They *do* follow the same ICAO Annex 15 **AIRAC 28-day
  cycle**, so our `cycles.py` timing model carries over unchanged.
- **A central database exists but is gated.** Eurocontrol runs **EAD** (European
  AIS Database), which aggregates national data and can export **AIXM** (the XML
  aeronautical-data standard). EAD is access-controlled / registered — not an
  anonymous bulk download like `nfdc.faa.gov`.
- **Format is worse.** Much national publication is **eAIP = styled PDF**, not a
  queryable database. AIXM is the good path but lives behind EAD.
- **Copyright is the real blocker.** European aeronautical data is generally
  **copyrighted by the national authority**, with reuse/redistribution often
  restricted or charged. We cannot legally rebuild-and-redistribute most of it
  the way we do FAA NASR. That, not availability, is what would stop a
  `navdata-europe` pack.

## Obstacles — the hard gap

Europe's equivalent is **eTOD** (electronic Terrain & Obstacle Data, ICAO Annex
15), mandated but published **per-country and mostly restricted/commercial**.
There is **no free, public-domain, pan-European DOF equivalent.** A few states
expose open obstacle data via national open-data portals (some Nordic countries,
etc.), but it is piecemeal. Treat European obstacles as effectively unavailable
for free redistribution.

## The practical open path (what GA/EFB projects actually use)

Since the authoritative data is not freely redistributable, open projects lean
on community datasets — each with a license caveat that matters for us:

| Source | Covers | License — can we redistribute? |
|--------|--------|-------------------------------|
| **OurAirports** | Global airports / runways / freqs | **CC0 / public domain — yes, clean.** Best fit for an open airports pack. |
| **openAIP** | Europe airports, airspace, navaids, *some obstacles* | Historically **CC BY-NC-SA** — the **non-commercial** clause is a redistribution problem. **Verify current terms before relying on it.** |
| **open flightmaps** | Europe VFR charts | Free but **NC / share-alike** — same caveat. |
| **OpenStreetMap** | Some `aeroway` tagging | ODbL (already used for our water/roads), but not authoritative/complete for aviation. |

## What's actually feasible

- **Airports / runways in Europe: feasible** via **OurAirports (CC0)** — drops
  straight into the existing pipeline with no licensing issue. Quality is
  community-maintained (good, not official). This would naturally be a **global**
  airports source, not Europe-specific.
- **Obstacles in Europe: effectively blocked** for free redistribution — no DOF
  analog; eTOD is national/restricted.
- **Official AIP data: not redistributable** without per-state agreements;
  out of scope for an open pack.

## TODO for future Bill, if/when this is picked up

- [ ] Re-verify **openAIP** and **open flightmaps** license terms (they change);
      confirm whether the NC clause truly blocks our use (we distribute for free,
      but "non-commercial" can still bite an org-hosted mirror).
- [ ] Prototype an **OurAirports**-based global airports source in
      `packtools/sources.py` (CSV download → reuse `tools/build_airport_db.py`
      shape). Decide pack id/scope: a `navdata-world` airports pack vs. keeping
      FAA-built CONUS as the authoritative US source and OurAirports for the rest.
- [ ] Note the quality/trust difference on the web GUI transparency table:
      FAA/NASR is official; OurAirports is community CC0. Pilots should know.
- [ ] Confirm AIRAC cycle handling needs no change (it shouldn't —
      `cycles.py` is ICAO-standard 28-day).
- [ ] Decide if **terrain** should extend to Europe now — that part is *not*
      blocked: Copernicus GLO-30 is global and already redistributable, so
      European terrain packs are just more regions in the existing terrain
      pipeline whenever someone wants them.
- [ ] Park European **obstacles** as a known gap; revisit only if a specific
      country's open-data portal offers a redistributable, machine-readable feed.

## Related

- [data_manager_strategy.md](data_manager_strategy.md) — overall pack/manifest design
- [terrain.md](terrain.md) — terrain (Copernicus GLO-30 is already global)
- [water.md](water.md) / [roads.md](roads.md) — the OSM/Geofabrik (ODbL) pattern
