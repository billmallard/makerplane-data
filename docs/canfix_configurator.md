# Aircraft Parameters (CAN-FIX) Configurator — spec / plan

Status: APPROVED WITH DECISIONS (2026-07-04, see section 10). Companion
to `system_designer.md` (product vision) and the panel editor. Lives in
the same Worker at pyefis.aerocommons.org.

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
| **Engine (per engine)** | TACHx, MAPx, OILPx, OILTx | Min, Max, lowWarn, highWarn, lowAlarm, highAlarm | Repeated group per engine; the **engine-type provider** (section 6a) decides which parameters exist. |
| **Cylinders (per engine)** | CHTxy, EGTxy, CHTMAXx | Min, Max, highWarn, highAlarm | Cylinder count is free-form (4/6 common, 8 rare, radials odd counts up to 28); one shared band set applied to all cylinders, per-cylinder override escape hatch later. |
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
  - units: values are STORED in the FIX database's native units;
    the airspeed group offers a **kt / mph / kph entry selector**
    (default kt — near-universal) so a POH in mph (C170B) or kph
    (European types) can be typed as-published and converted on entry;
    the selector is remembered per profile. Other groups display the
    schema unit label only (no conversion editing in v1);
  - blank = "leave unset" (pyEfis already renders no band for unset
    aux) — explicitly allowed.
- **Panel-preview tie-in**: the editor's airspeed tape/dial/arc-gauge
  twins currently draw sample bands. When the project has a profile,
  the twins read it so the panel preview shows the real arcs. (Small,
  high-delight, keeps both features honest.)

## 6a. Engine-type providers (decided: build generic from day one)

The engine group is a **provider model**, the pattern proven by the
airport-data providers: an engine TYPE selects a parameter template,
and the profile holds one instantiated provider per engine position.

- `piston` (v1): TACH, MAP, OILP, OILT + cylinder group (CHT/EGT,
  free-form cylinder count — radials go to 28, odd counts legal).
- `turbine` (schema reserved, UI later): ITT, N1, N2, torque, fuel
  flow — parameters exist in CAN-FIX; wiring the group is cheap once
  someone needs it.
- `electric` / `hybrid` (schema reserved): motor temp, controller
  temp, pack voltage/current/SOC — CAN-FIX parameter mapping TBD;
  the provider seam is where they will land.

The `aircraft_params_schema.json` exporter emits parameter sets PER
PROVIDER TYPE, so new engine kinds are added in fix-gateway (the
schema authority) and flow to the UI without Worker changes. Engine
count is unbounded in the model; the UI lists engines 1..n with a
type picker each.

## 6b. Fuel-system provider model (added 2026-07-04)

Fuel tank configurations are effectively unbounded (Bill's Bonanza:
4 tanks going on 5; experimentals commonly run a HEADER tank that
feeds the engine while every other tank feeds the header; his tip
tanks transfer into the mains which feed the engine). So fuel is a
provider/topology model, not just a count:

- Each tank carries a **role** (`main`, `aux`, `tip`, `header`,
  `transfer`) and a **feeds** edge: -> engine N, or -> tank N (a
  transfer), or none. That directed graph IS the fuel system.
- Quantities/bands stay per-tank FIX aux (FUELQt Max/lowWarn/
  lowAlarm) as today; the topology lives in the profile's
  `meta.fuel` array — forward data for fuel totalizer logic, the
  in-EFIS fuel page, and the system diagram (6c). Nothing in
  fix-gateway consumes it yet; the profile is the canonical record.
- Validation: every engine should be fed by at least one tank
  (warning, not error); transfer cycles flagged.

## 6c. Future: aircraft system ("node") diagram

Direction (Bill, 2026-07-04): the configurator should eventually
render an IT-network-style **node diagram** of the aircraft — fuel
tanks/feed edges (6b), engines, buses/nodes on the CAN-FIX bus,
devices — generated from the profile + device data it already holds.
Pairs naturally with the aircraft-local web server vision (8a): the
same diagram is the aircraft home page's centerpiece. Not scheduled;
recorded so 6b stores topology as a proper graph from day one.

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

## 8a. Future state: the aircraft's own interface (recorded, not v1)

Direction from Bill (2026-07-04), recorded here so the seams are cut
in the right places:

- fix-gateway is where you "talk to the aircraft" — long-term it
  should run a **local web server on the plane**: the aircraft's "home
  page", reachable from a laptop or phone on the aircraft's network,
  showing everything about the machine (live FIX values, node status,
  data currency, this parameter profile, logs).
- That implies **robust debugging and flight-data capture** in
  fix-gateway itself: structured logging / recording of bus traffic
  and FIX values, retrievable from the home page. (fixgw already runs
  headless under systemd on the Pi; a UI has never been required —
  keep it that way, the web page IS the UI.)
- There will be **overlap between the cloud configurator and the
  aircraft-local server** (both render aircraft state and this
  profile). Design for it: keep the profile JSON + params schema as
  self-contained artifacts a local server could serve/edit and later
  sync, rather than burying them in Worker-only code.

None of this is a first-pass deliverable; it shapes naming and file
formats now (self-describing `aircraft.ini` header, profile JSON as
the canonical artifact, schema from fix-gateway) so the local-server
future does not require a migration.

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

## 10. Decisions (Bill, 2026-07-04)

1. **Units**: airspeed group gets a **kt / mph / kph** entry selector
   (European builders included), **default kt** — used almost
   universally. Values stored in FIX native units; selector converts
   on entry and is remembered per profile. (Folded into section 6.)
2. **Min/Max ownership**: keep in the profile, pre-filled from
   templates, de-emphasized in the UI — as proposed.
3. **Apply semantics**: **auto-restart of fixgw on config pull is
   acceptable** (it already runs headless in the background on the
   Pi). The larger direction this surfaced — fixgw as the aircraft's
   local web server with robust debugging and flight-data capture —
   is recorded in section 8a as future state, not first pass.
4. **Multiplicity**: build **generic from the start** — multiple
   engines, free-form cylinder counts (radials: odd counts, up to
   28), and an **engine-type provider model** (piston now; turbine /
   electric / hybrid seams reserved). Section 6a.
5. **Templates**: ship **Lycoming O-320** (RV-ish) and **O-360**
   profiles first, plus a **Continental O-470**; c170b already exists
   in-tree as reference data.
6. **AOA calibration**: tentatively lives here — **placeholder group
   in v1**, values firmed up when the OnSpeed/AOA integration lands.
