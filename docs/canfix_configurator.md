# Aircraft Parameters (CAN-FIX) Configurator — spec / plan

Status: PROPOSED (2026-07-04). Companion to `system_designer.md` (product
vision) and the panel editor. Lives in the same Worker at
pyefis.aerocommons.org.

## 1. What this is

The panel editor deliberately excludes aircraft data: V-speeds and gauge
warn/alarm bands are FIX-database values, not layout options (pyEfis
issue #64). That boundary needs a home. This feature gives every
**aircraft** (the existing project / "aircraft design" entity) a managed
**Aircraft Parameter Profile**: the airframe- and engine-specific numbers
that drive instrument arcs, bands and alarms — airspeed white/green/
yellow arcs, redline, engine gauge ranges, fuel capacities, electrical
limits — edited as forms, validated, versioned, and delivered to the
aircraft through the same signed config-pack pipeline as panel layouts.

"CAN-FIX configurator" because these values belong to the CAN-FIX /
FIX-database layer of the stack (the flight-data contract), not to any
one display. Phase A/B deliver them through fix-gateway (how every
bench and X-Plane aircraft runs today); Phase D extends the same profile
to real CAN-FIX node configuration.

## 2. Ground truth today (verified in the repos)

- **fix-gateway defines the keys and their aux slots.**
  `src/fixgw/config/database/*.yaml`, e.g. IAS carries
  `aux: [Min,Max,V1,V2,Vne,Vfe,Vmc,Va,Vno,Vs,Vs0,Vx,Vy]`; quantitative
  keys carry `[Min,Max,lowWarn,highWarn,lowAlarm,highAlarm]`.
- **Aircraft-specific values live in layered INI files.**
  `default.yaml -> initialization files:` lists
  `init_data/default.ini` plus optional per-airplane
  (`init_data/airplanes/cessna/c170b.ini`) and per-engine
  (`init_data/engines/rotax/582.ini`) overlays. Syntax:
  `IAS.Vne = 160`, `OILP1.lowAlarm = 25`, plain `KEY = v` for initial
  values. Later files override earlier ones.
- **pyEfis consumes aux, never defines it.** The airspeed tape/dial read
  `Vs/Vs0/Vno/Vne/Vfe` aux for the arcs; `AbstractGauge` colors bands
  from `Min/Max/lowWarn/highWarn/lowAlarm/highAlarm`. An unset aux =
  no band (already handled).
- **The Pi's fixgw runtime config** (`~/makerplane/fixgw/config/`) is
  the only un-versioned deployed state in the stack today — exactly the
  gap this feature closes.
- **CAN-FIX** (canfix-spec) assigns parameter IDs to the same
  quantities; the .ods spec is the schema master. Real CAN nodes can
  hold configuration in EEPROM via the node-configuration protocol —
  out of scope until Phase D, but the profile schema records the
  CAN-FIX parameter ID alongside each FIX key so the same profile can
  drive node config later.

## 3. What the profile manages (value inventory)

Grouped as the UI will present them. Every entry = FIX key + aux slots,
with units and validation. Initial values (plain `KEY =`) are included
only where they are genuinely configuration (fuel quantity is NOT — it
is live data; its *capacity* is).

| Group | Keys | Aux managed | Notes |
|---|---|---|---|
| **Airspeeds** | IAS | Vs, Vs0, Vfe, Vno, Vne, Va, Vx, Vy, Vmc, V1, V2, Min, Max | Arcs derive: white = Vs0..Vfe, green = Vs..Vno, yellow = Vno..Vne, red >= Vne. Va/Vx/Vy shown as bugs where supported. Vmc/V1/V2 hidden behind a "twin/turbine" toggle. |
| **Engine (per engine)** | TACHx, MAPx, OILPx, OILTx | Min, Max, lowWarn, highWarn, lowAlarm, highAlarm | Repeated group per engine count (1..2 to start). |
| **Cylinders (per engine)** | CHTxy, EGTxy, CHTMAXx | Min, Max, highWarn, highAlarm | Repeated per cylinder count (1..6); one shared band set applied to all cylinders, with per-cylinder override escape hatch later. |
| **Fuel** | FUELQx (capacity = Max, lowWarn, lowAlarm), FUELFx, FUELPx | Min, Max, warns/alarms | Tank count 1..4. FUELQT derives (sum) — display only. |
| **Electrical** | VOLT, CURRNT | Min, Max, lowWarn, highWarn, lowAlarm, highAlarm | 12 V / 24 V presets. |
| **Air / misc** | OAT, CAT, AOA | bands; AOA calibration refs (Min/Max/0g/warn/stall as defined in database.yaml) | AOA numbers come from flight-test calibration — the form stores what the OnSpeed/AOA source needs displayed. |

Explicitly OUT of scope: live flight values, panel layout (panel
editor), fix-gateway connection topology (which source plugins are
enabled — a future "connections" page, not this one), navdata currency
(makerplane-data), weight & balance (its own future feature; noted
because builders will ask).

## 4. Schema: fix-gateway is the source of truth

Mirror the instrument-schema pattern (pyEfis generates
`schema.json` -> R2 -> editor renders from it):

- New exporter in **fix-gateway** (`fixgw/tools/export_param_schema.py`
  or run from CI): reads `database/*.yaml` and emits
  `aircraft_params_schema.json` — for each managed key: description,
  units, type, min/max sanity range, aux slots, engine/cylinder/tank
  multiplicity pattern, and the CAN-FIX parameter ID where one exists.
- Uploaded to R2 `assets/editor/aircraft_params_schema.json`; the
  Worker UI renders forms from it. The form code never hardcodes keys,
  so adding a key in fix-gateway flows through by regenerating.
- The curated grouping/labels/ordering (section 3) live in a small
  curation table in the exporter, like `editor/schema.py` curates
  instrument metadata.

## 5. Data model & API (Worker)

- `aircraft_profiles` D1 table: `project_id` (FK — profile belongs to
  the AIRCRAFT, shared by all its devices), `version`, `json`,
  `created_at`, `created_by`, `active` flag. Append-only versions;
  one active per project. JSON = `{schema_version, values: {"IAS.Vne":
  160, ...}, meta: {engines: 1, cylinders: 4, tanks: 2, electrical:
  "12V"}}`.
- API: `GET/PUT /api/projects/:id/aircraft` (active profile),
  `GET .../aircraft/versions`, `POST .../aircraft/activate/:v`.
  Ownership-scoped through user_id like everything else.
- Starter templates: ship a few read-only presets (the c170b numbers,
  a generic O-320 single, Rotax 912) + "import .ini" (paste an existing
  init_data file — parse the exact fixgw dialect) so nobody starts from
  a blank form.

## 6. UI

New **"Aircraft"** tab in the project view (sibling of the device/panel
list — the user already picks aircraft -> devices; this hangs the
profile on the aircraft):

- Grouped forms per section 3, engine/cylinder/tank counts first (they
  shape the repeated groups).
- **Airspeed arc preview strip**: a live horizontal tape rendering the
  white/green/yellow/red arcs from the current field values — instant
  visual sanity (this is the twin fidelity rule applied to data).
- Validation, hard where physics demands, soft otherwise:
  - ordering: `Vs0 <= Vs < Vno < Vne <= Max`, `Vfe` within
    `[Vs0, Vno]`, warns inside alarms inside Min/Max;
  - unit display from schema (kt, degF/degC, psi, gal) — stored in the
    FIX database's native units, shown with the unit label; no
    conversion editing in v1;
  - blank = "leave unset" (pyEfis already renders no band for unset
    aux) — explicitly allowed.
- **Panel-preview tie-in**: the editor's airspeed tape/dial/arc-gauge
  twins currently draw sample bands. When the project has a profile,
  the twins read it so the panel preview shows the real arcs. (Small,
  high-delight, keeps both features honest.)

## 7. Compile & delivery (reuses the #65 pipeline)

Compile step (Worker, on publish): profile JSON ->
**`aircraft.ini`** in the exact fixgw init_data dialect, with a
generated header (aircraft name, profile version, timestamp, sha of the
profile JSON).

Delivery rides the device config-pull pipeline being built for panel
YAML (claim-code pairing -> device token -> signed pack ->
`pyefis_data/config_pull.py` fetch + atomic swap):

- The config pack gains a second member: panel screens go to
  `~/makerplane/pyefis/config/…` (existing plan), `aircraft.ini` goes
  to `~/makerplane/fixgw/config/init_data/aircraft.ini`.
- One-time enrolment edit (done by the pull tool, idempotent): append
  `"{CONFIG}/init_data/aircraft.ini"` LAST in `default.yaml ->
  initialization files` — last wins, so the managed profile overrides
  the shipped defaults without touching them.
- Apply = restart `fixgw.service` (init files are read at startup;
  fixgw restart is seconds and pyEfis reconnects tolerantly). The pull
  tool restarts fixgw only when the ini actually changed, and only
  on-ground guarded the same way panel applies will be (open question
  below).
- Trust chain identical to navdata packs: manifest-signed, sha-verified,
  staged, atomic move — a bad download never disturbs the flying
  config.

This makes the last un-versioned deployed state (fixgw config's
aircraft numbers) versioned, signed and reproducible.

## 8. CAN-FIX proper (Phase D, exploratory)

On an aircraft with real CAN-FIX nodes (EFIS gauges, engine monitors),
some of these values live in node EEPROM. Two integration paths, in
order of likelihood:

1. **Gateway remains authority** (recommended default): fixgw holds the
   profile and publishes aux to consumers; nodes that need a limit
   (e.g. a standalone CHT alarm) are configured via
2. **Node configuration writes**: a fixgw utility (or configurator
   button when the device link exists) issues CAN-FIX
   node-configuration set/query commands per canfix-spec, sourcing the
   same profile. Needs per-node-type mapping tables — spec'd only when
   real hardware shows up.

The profile schema carrying CAN-FIX parameter IDs from day one is what
keeps this door open without rework.

## 9. Phases

- **Phase A — profile editing (no delivery).** Schema exporter in
  fix-gateway + R2 upload; D1 table + API; Aircraft tab UI with groups,
  validation, arc preview strip; templates + ini import. *Editable and
  saveable, downloadable as aircraft.ini for manual install (useful
  immediately on the bench).* ~2-3 sessions.
- **Phase B — panel-preview tie-in.** Airspeed/gauge twins read the
  active profile. ~half session, can ride with A.
- **Phase C — delivery.** Depends on #65 device pairing shipping for
  panels; adds the second pack member, enrolment edit, fixgw restart
  handling, on-Pi end-to-end proof. ~1-2 sessions after #65.
- **Phase D — CAN-FIX node config.** Hardware-gated exploration.

## 10. Open questions (for Bill)

1. **Units**: store/edit in FIX native units only (v1 proposal), or
   support kt/mph toggle for the airspeed group? (C170B POH is mph…)
2. **Min/Max ownership**: instrument display ranges (`Min`/`Max`) are
   half display-taste, half airframe. Proposal: keep them in the
   profile (they are FIX aux), but pre-fill from templates and
   de-emphasize in the UI.
3. **Apply semantics**: is auto-restart of fixgw on config pull
   acceptable, or should application require a confirm on the device
   (DataStatus-screen style)? Proposal: device confirms, same UX as
   panel apply will use.
4. **Multi-engine/cylinder counts**: 1 engine / 4 cylinders / 2 tanks
   covers the fleet today — cap v1 there, or build the repeated groups
   generic from the start? (Generic is cheap if the schema drives it.)
5. **Which template aircraft** should ship first? (c170b exists; an
   RV-ish O-320 profile would fit MakerPlane's audience.)
6. Does **AOA calibration** belong here or with the OnSpeed
   integration when it lands? (Placeholder group in v1, values TBD.)
