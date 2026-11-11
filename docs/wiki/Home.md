# makerplane-data

**makerplane-data keeps an experimental aircraft's avionics current and
configured — from a laptop at the hangar.** It is the data-and-configuration
half of the [MakerPlane](https://www.makerplane.org/) open-source avionics
stack: it delivers signed navigation data, panel layouts, and aircraft
parameters to a [pyEfis](https://github.com/makerplane/pyEfis) EFIS and its
FIX-Gateway companion, over WiFi or a USB stick, and tells the pilot when
something is stale.

It does **not** carry live flight data. That is the runtime bus (FIX-Gateway /
CAN-FIX). makerplane-data carries the *reference and configuration* data the
avionics read — the things that change on a cycle, not in flight.

## The three things it does

| Area | What it delivers | Where it runs | Status |
|---|---|---|---|
| **[[Navigation Data Currency]]** | Signed terrain, airport, navaid, obstacle, water & road packs on a 28-day cycle | Build pipeline → [navdata.aerocommons.org](https://navdata.aerocommons.org) → on-Pi updater | **Live** |
| **[[The Configurator]]** | A web app to design EFIS panels visually and push them to a paired aircraft | [pyefis.aerocommons.org](https://pyefis.aerocommons.org) | **Live** (editor + device delivery) |
| **[[Aircraft Parameters]]** (CAN-FIX) | Per-aircraft V-speeds, gauge bands, engine & fuel limits for the FIX database | Same web app → `aircraft.ini` for FIX-Gateway | **Phase A** (edit + download; auto-delivery next) |

## The one idea that ties it together

Everything makerplane-data ships follows the same safety pattern: **a verified
artifact is placed atomically, and a bad download can never disturb what is
already running.** Navigation packs are signed and checked against a signed
catalog; panel configs and aircraft parameters ride the same download → verify →
atomic-swap plumbing on the aircraft. See **[[Architecture]]** for how that
works end to end.

## Start here

- **New to the project?** Read **[[Architecture]]** first — it explains the
  whole system in one page.
- **Keeping data current on your aircraft?** → **[[Navigation Data Currency]]**
- **Designing a panel?** → **[[The Configurator]]**
- **Setting up V-speeds and gauge limits?** → **[[Aircraft Parameters]]**

## The wider stack

makerplane-data is one repo in a larger avionics system:

```
  data sources ─▶ FIX-Gateway ─(FIX keys)─▶ pyEfis (the EFIS display)
  (GPS, ADS-B,          │                         │
   X-Plane, CAN)        │                         └─ reads reference data from
                        │                            /data/makerplane-data/…
  makerplane-data keeps that reference data current, ───┘
  and delivers panel layouts + aircraft parameters to both boxes.
```

- **[pyEfis](https://github.com/makerplane/pyEfis)** — the EFIS display that
  reads the packs and runs the panels this repo delivers.
- **FIX-Gateway** — the data hub; consumes the aircraft-parameter profile.
- **[MakerPlane](https://www.makerplane.org/)** — the open-source aircraft
  project this all serves.

## Licensing (open by design)

This is an open project with **no secret sauce** — the architecture is
deliberately published as prior art so it stays free to implement. Components
are licensed by role: the data tools and device updater are **Apache-2.0**
(build against them freely, with an express patent grant); the hosted
configurator service is **AGPL-3.0**; documentation is **CC BY 4.0**. Packaged
*data* keeps its own terms (FAA public domain, Copernicus, OpenStreetMap ODbL).
Full detail: [`docs/LICENSE-AUDIT.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/LICENSE-AUDIT.md).

> This wiki explains **how the system works**. For implementation detail, the
> `docs/` folder and per-package READMEs in the
> [repository](https://github.com/billmallard/makerplane-data) are the deep
> reference; links throughout point there.
