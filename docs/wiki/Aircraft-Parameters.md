# Aircraft Parameters (CAN-FIX)

Every aircraft carries a set of numbers that make its instruments correct for
*that airframe and engine* — the airspeed arcs, the redline, the green/yellow
bands on the engine gauges, the fuel capacities, the electrical limits. This
feature gives each aircraft a managed **Aircraft Parameter Profile**: those
numbers, edited as validated forms, versioned, and delivered to the aircraft's
FIX-Gateway.

It lives in the same web app as the panel editor
([pyefis.aerocommons.org](https://pyefis.aerocommons.org)), under an **Aircraft**
tab on each project.

> **Status: Phase A is built.** You can create a profile, edit it with live
> validation, start from a template, import an existing file, and **download a
> ready-to-install `aircraft.ini`** — usable on the bench today. Automatic
> delivery to the aircraft over the device link is the next phase (Phase C).

## Why this is separate from the panel editor

The panel editor deliberately leaves these numbers out. A V-speed or a gauge
warning band is **not** a layout choice — it's a value in the aircraft's FIX
database (the flight-data layer), which the instruments read at runtime. The
same airspeed tape shows different arcs on a Cub and an RV because the *data*
differs, not the layout. (This is pyEfis issue #64.)

So the boundary is clean:

- **Panel editor** → where instruments sit and how they look. → **[[The Configurator]]**
- **Aircraft Parameters** → the airframe/engine numbers those instruments display.

It's called the "CAN-FIX configurator" because these values belong to the
CAN-FIX / FIX-database layer of the stack, not to any one display.

## How the numbers are modelled

In FIX-Gateway, each quantity is a **key** (e.g. `IAS` for indicated airspeed,
`OILP1` for oil pressure) with a set of named **aux slots** hanging off it.
V-speeds are aux slots on the airspeed key; warning/alarm bands are aux slots on
each gauge key:

- `IAS` carries `Vs`, `Vs0`, `Vfe`, `Vno`, `Vne`, `Va`, `Vx`, `Vy`, …
- a gauge like `OILP1` carries `lowWarn`, `highWarn`, `lowAlarm`, `highAlarm`,
  `Min`, `Max`.

The instruments derive their look from those: the airspeed tape draws
white = Vs0…Vfe, green = Vs…Vno, yellow = Vno…Vne, red ≥ Vne; a gauge colours its
bands from its warn/alarm slots. An unset slot simply means "no band" — leaving a
value blank is always allowed.

**The profile is just the set of those values for one aircraft**, saved as a
versioned record (append-only versions, one active at a time), belonging to the
aircraft so all its devices share it.

## What a profile covers

| Group | Examples | Notes |
|---|---|---|
| **Airspeeds** | Vs, Vs0, Vfe, Vno, Vne, Va, Vx, Vy | Live white/green/yellow/red **arc preview** as you type; enter in **kt / mph / kph** (a C170B POH is in mph, European types in kph) and it's stored in the database's native units. |
| **Engine** (per engine) | tach, manifold pressure, oil pressure/temp | An **engine-type provider** decides which parameters exist (see below). |
| **Cylinders** (per engine) | CHT, EGT bands | Free-form cylinder count (4 and 6 common; radials run to 28, odd counts legal); one shared band set, expanded to every cylinder at compile. |
| **Fuel** | tank capacities + low warnings, fuel flow/pressure | 1–4 tanks; each tank has a **role and feed** (see the fuel model below). |
| **Electrical** | volts, amps | 12 V / 24 V presets. |
| **Air / AOA** | OAT, CAT, angle-of-attack references | AOA is a placeholder in Phase A, firmed up when the AOA sensor integration lands. |

FIX-Gateway is the **authority** for what keys, aux slots, units, and sane ranges
exist: it generates the parameter schema, and the web forms render from it. Add a
key in FIX-Gateway, regenerate, and it appears in the UI — the form code hard-codes
nothing.

### The provider model (engines and fuel)

Two parts of an aircraft are too varied for a fixed form, so they're modelled as
**providers** — the same pattern used elsewhere in the stack:

- **Engine type** — `piston` today (tach, MAP, oil, plus the cylinder group);
  `turbine` and `electric`/`hybrid` are reserved seams for later. Engine count is
  unbounded; each engine picks its type.
- **Fuel system** — real fuel systems are a *topology*, not a count. Each tank
  carries a role (`main`, `aux`, `tip`, `header`, `transfer`) and a **feed edge**
  (→ an engine, or → another tank for a transfer). That directed graph *is* the
  fuel system, recorded in the profile so later features (a fuel totalizer, an
  in-EFIS fuel page, an aircraft system diagram) can use it.

### Validation

The forms mark V-speed and gauge-band ordering problems inline as you type, and
blank still means "leave unset."

Saving checks the whole profile on the server, against these rules:

- V-speeds: `Vs0 ≤ Vs < Vno < Vne ≤ Max`, and `Vs0 ≤ Vfe ≤ Vno`.
- Every other gauge: `Min ≤ lowAlarm ≤ lowWarn ≤ highWarn ≤ highAlarm ≤ Max`.
- Every value is a number; the speed unit is `kt`, `mph` or `kph`, and the
  altimeter unit `inHg` or `hPa`.

Ordering is checked across blanks, so a gap cannot hide an inversion. A profile
that breaks a rule is refused with HTTP 422, and the page shows `not saved:`
followed by each broken rule. A malformed ownship identity (below) blocks both
Save and Download until it is fixed.

### Ownship identity

The **Ownship identity** card takes the aircraft's **Registration** (up to 10
letters, digits and hyphens; one starting with `N` must be a valid US
N-number) and an optional **ICAO address override** (six hex digits, not
`000000`). It names the 24-bit address whose ADS-B reports are
hidden from the traffic display as your own aircraft: the override if set,
otherwise the address derived from a US N-number, otherwise none (only the
receiver's own ownship report is filtered). Registrations from other states do
not derive an address; enter the hex from the registration certificate or
transponder. The identity is saved with the profile but is not yet sent to the
aircraft: set the address the card shows as `ownship_address` in FIX-Gateway's
stratux connection.

## Templates and import

You don't start from a blank form. Phase A ships starter templates — **Lycoming
O-320 / O-360** and **Continental O-470** engine groups, and a full **Cessna
170B** example — and you can **import an existing `init_data` file** (paste it in;
it's parsed in FIX-Gateway's exact dialect).

## Delivery to the aircraft

When published, the profile compiles to an **`aircraft.ini`** in FIX-Gateway's
own `init_data` format, with a generated header (aircraft name, profile version,
timestamp, and a hash of the source). FIX-Gateway loads layered init files at
startup, later files overriding earlier ones, so the managed `aircraft.ini` is
appended **last** — it overrides the shipped defaults without touching them.

- **Today (Phase A):** *Download `aircraft.ini`* and drop it into
  `~/makerplane/fixgw/config/init_data/` yourself (install instructions are in the
  file header). Immediately useful on the bench.
- **Next (Phase C):** the file rides the **same device-pull pipeline** as panel
  layouts (pair → token → verified pull → atomic swap), and applying it is a quick
  FIX-Gateway restart on the ground. This closes the last un-versioned piece of an
  aircraft's setup — the FIX-Gateway numbers — making it versioned and
  reproducible.

## Looking ahead (recorded, not yet built)

- **On real CAN-FIX nodes**, some limits live in a node's own memory. The profile
  already records the CAN-FIX parameter ID next to each FIX key, so the same
  profile can drive node configuration later without rework — a hardware-gated
  exploration (Phase D).
- **An aircraft system diagram** — an IT-network-style view of tanks, feeds,
  engines, bus nodes, and devices — generated from the profile and device data
  the app already holds.

Design spec and decisions:
[`docs/canfix_configurator.md`](https://github.com/billmallard/makerplane-data/blob/main/docs/canfix_configurator.md).
