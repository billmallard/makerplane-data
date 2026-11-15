<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Fuel tanks as a provider: labelled tanks, gauges bound by tank (design brief)

Status: **ACCEPTED** (Bill, 2026-11-09, on AER-5180: all five
recommendations in section 10). Written 2026-11-08 as FUEL1. No code. The
build rows in section 9 are drafted to the backlog, each still waiting on
its own promotion. It extends
`canfix_configurator.md` section 6b, which stays the record of the role and
feeds model.

Direction (Bill, 2026-11-07): "This needs to work well with the
configurator (main, aux, tip, header, transfer) and should be able to
achieve any reasonable fuel tank configuration. I'm shying away from using
words like "Left","Center","Right" to correspond to tank numbers. They
should, instead, be label values for the tanks from the aircraft
configuration page. Then from the pyEfis Configurator we should be able to
drop a gauge into the screen and bind it to the tank key. It's a provider
model for fuel tanks?"

Yes, and it is the same provider pattern as engines (6a). The aircraft page
is the provider: it declares the tanks. The screen configurator is a
consumer: a gauge holds a reference to a tank, never a number. The reference
is resolved to a FIX key when the config is served to the device.

Sources read for this brief, all on 2026-11-08: makerplane-data `dev`
e86e8fb, pyEfis `dev` b5eb3dd, fix-gateway `dev` 541e41e, and canfix-spec
`master` 2ea77ef (the fork, `src/canfix.json` version 0.7).

## 1. What exists today

| Piece | Where | State |
|---|---|---|
| Tank count | `aircraft.html:338` `opt("tanks", 8, ...)` | 1 to 8 in the UI. The docs say 1 to 4 (`canfix_configurator.md:64`). The server checks nothing. |
| Role + feeds | `aircraft.html:356-383`, `meta.fuel` | Positional array: entry `t-1` is tank `t`. `{role, feeds}`, where `feeds` is one string `""`, `"E<n>"` or `"T<n>"`. No id, no label. |
| Capacity, bands | `values["FUELQn.Max/lowWarn/lowAlarm"]` | Per-tank FIX aux. These are compiled into `aircraft.ini` by the browser only (`buildIni`, `aircraft.html:528-547`). |
| Validation | `src/aircraft_profile.ts:54-86` | Band ordering only. `meta.fuel` passes unchecked. The 6b checks ("every engine fed", "transfer cycles flagged") are not built. |
| Delivery | `src/device_prefs.ts`, `/device/config` | Only `meta.baro_unit` reaches the device. `aircraft.ini` is not delivered: Phase C (spec section 7) is unbuilt, and `config_pull.py` fetches one YAML document. |
| fix-gateway keys | `database/misc.yaml:49-67`, `variables.yaml:10` | `FUELQt` expands to `FUELQ1..FUELQ<t>`, `t: 3`, with no maximum. Aux is `[Min,Max,lowWarn,lowAlarm]`. `FUELQT` is a separate key. |
| `FUELQT` | `connections/compute.yaml:18-20` | `sum` over a hard-coded `["FUELQ1","FUELQ2","FUELQ3"]`. `sumFunction` (`plugins/compute.py:277-316`) publishes nothing until every input has been written once. |
| CAN-FIX ingest | `canfix/map.yaml:72-73` | Only `0x226`->`FUELQ1` and `0x227`->`FUELQ2`, index 0. Tanks 3 and 4 have no mapping. |
| pyEfis gauges | `preferences.yaml` BAR4-6, BAR19-21 | Hard-coded names. CERT2b (AER-5179) is making them neutral "Tank N". The label never comes from the FIX description. |
| A bad `dbkey` | `abstract.py:212` `setDbkey` | `fix.db.get_item` raises `KeyError`. Nothing catches it, so pyEfis does not start. |
| Reference binding | nowhere | The checklist plan names `checklist_ref` (`briefs/checklist_instrument_plan.md` section 3). It is not built. |

Three things in that table matter for the design. They are real defects, and
section 9 turns each one into a row:

- **Removing a tank in the middle renumbers the tanks after it.** `meta.fuel`
  is positional, so `feeds: "T3"` silently points at a different tank. A
  gauge reference keyed by number would move the same way. Tanks need a
  stable identity before anything can bind to them.
- **`FUELQT` is never written on a 2-tank CAN install.** CAN feeds only
  `FUELQ1` and `FUELQ2`, and the compute waits for `FUELQ3`. What the stock
  "Total" bar then shows depends on the key's initial-state flags, which I did
  not verify on the bench.
- **A served config that names a missing key stops pyEfis at boot.** So the
  resolver must never emit a key the device lacks. Where a tank is gone, it
  emits an explicit unbound state (section 5).

## 2. Profile schema per tank

`meta.fuel` becomes a list of tank objects with stable ids. It is still
ordered, but order only decides how tanks are listed on the page.

```json
"fuel": {
  "schema": 2,
  "tanks": [
    {"id": "tk_7f3a", "label": "L MAIN", "role": "main",
     "slot": 1, "feeds": [{"to": "engine", "n": 1}],
     "capacity": 40.0, "unusable": 1.0},
    {"id": "tk_19c2", "label": "L TIP", "role": "tip",
     "slot": 3, "feeds": [{"to": "tank", "id": "tk_7f3a"}],
     "capacity": 20.0, "unusable": 0.0}
  ]
}
```

| Field | Meaning | Rule |
|---|---|---|
| `id` | Stable identity, generated when the tank is created. Never shown, never reused, never changed. | What a gauge's reference stores. |
| `label` | What the pilot reads. Free text, typed by the builder. | Required, unique within the aircraft, at most N characters (N is measured, section 6). Nothing in a stock config maps Left/Center/Right to a number; the label is the only name a tank has. |
| `role` | `main`, `aux`, `tip`, `header`, `transfer` (6b). | Descriptive. Drives soft checks only: a `header` should feed an engine, and a `tip` or `transfer` tank should feed a tank. |
| `slot` | The `n` in `FUELQn`, which is where the sender's reading lands. This is wiring, not naming. | Unique, 1 to the tank ceiling. Assigned when the tank is created (lowest free slot) and editable, because a rewire is a real event. Removing another tank never changes it. |
| `feeds` | Directed edges. Zero or more of `{to: "engine", n}` or `{to: "tank", id}`. | A list, because a crossfeed lets one tank feed two engines. Edges point at tank **ids**. |
| `capacity` | Total tank capacity, in the FIX native unit (gal). | Positive number. |
| `unusable` | Unusable fuel, in gal. | 0 or more, and less than `capacity`. |

**Validation**, client and server alike. Server errors give a 422, as the
existing band checks do:
- error: duplicate `id`, duplicate `slot`, duplicate or empty `label`, a
  `slot` out of range, an edge to a missing tank or engine, a tank feeding
  itself, or `unusable >= capacity`;
- warning: an engine fed by no tank; a cycle in the tank-to-tank edges
  (spec 6b says "flagged", not refused); a role at odds with its edges.

**Capacity and the FIX bands.** The `FUELQn` aux keep their meaning, and
the profile now writes them from the tank: `FUELQn.Min = 0`, and
`FUELQn.Max = capacity - unusable`. `lowWarn` and `lowAlarm` stay
per-tank fields as today. This makes the gauge read usable fuel, which is
the certified convention: "Each fuel quantity indicator must be calibrated
to read 'zero' during level flight when the quantity of fuel remaining in
the tank is equal to the unusable fuel supply" (14 CFR 23.1337(b)(1), in the
pre-Amendment-64 Part 23). **This is a decision for Bill (section 10, D2)**,
because it fixes what a `FUELQn` value means. The sender calibration has to
agree with it, and that is installation work outside this repo.

`FUELQT` stays derived and display-only (guardrail). It is the sum of the
profile's tanks (row F7), and no tank and no reference can name it as a tank.

**Migrating a saved profile** (schema 1 to 2, on load, in the browser):
entry `t-1` becomes `{id: new, label: "Tank t", slot: t, role, feeds}`.
`"E<n>"` becomes an engine edge and `"T<n>"` becomes an edge to the new id
of tank n. `capacity` is read from `values["FUELQt.Max"]`, and `unusable`
starts at 0. That reproduces the saved `Max` exactly, so the compiled
`aircraft.ini` does not change. The default label is "Tank t", which matches
CERT2b. Nothing is mapped to Left/Center/Right.

## 3. The tank-count ceiling, and the path past four

It is three separate ceilings, and only one of them is real:

1. **The FIX database: none.** `FUELQt` expands to any `t`
   (`database.py:468-470` casts `int(value)`, no maximum). A fifth tank is
   `t: 5`. **No new FIX key is needed for any tank count.**
2. **CAN-FIX: not four.** The task framing says a fifth tank needs a spec
   extension. The fork's spec says otherwise. Fuel Quantity is ids 550-553
   *and* carries an index, named "Aux Tank" (`canfix.json`, id 550,
   `"count": 4, "index": "Aux Tank"`; `parameter_list.rst`, "Fuel
   Quantity"). The index byte runs 0-255 (`framedef.rst:53-57`), and
   fix-gateway's CAN map already routes by `(canid, index)`, as the CHT rows
   do (`map.yaml:56-59`). The spec does **not** say how ids and indexes
   combine into tank numbers. That gap is the only contract work.
3. **The configurator UI: 8 today.** That is a product choice, not a limit.

**Recommendation.** Define the tank encoding in the fork's canfix-spec, under
ORG-011, with no new parameter id:

- tank `n` (1-based) is sent on id `550 + ((n-1) mod 4)`, index
  `floor((n-1) / 4)`;
- so tanks 1-4 are ids 550-553 at index 0. That is exactly what every
  existing node and `map.yaml` already assume, so nothing deployed changes.
  Tanks 5-8 are index 1, and the encoding reaches 1024 tanks;
- record it in `canfix.json` remarks and in the generated
  `Parameter-Reference.md`, with an ORG-011 "Divergence from upstream"
  note. The precedent is canfix-spec#2, parameters 482/483.

The alternative, fix-gateway-only keys for tanks 5 and up, needs no spec
edit. But it leaves a 5-tank aircraft with a tank that a CAN fuel node cannot
report, and that is a hardware gap disguised as a software limit. The index
encoding costs one documentation PR and keeps every tank on the bus.

**The profile does not depend on either choice.** A tank binds to a `slot`
(`FUELQn`). How a sender reaches `FUELQn`, whether CAN index, analog input or
serial plugin, is the installation's fix-gateway connection config, which is
out of the profile's scope (spec section 3). Keep the UI ceiling at **8**
(Bill's Bonanza at 5, with headroom), stated as a number in one place, and
raise it on request.

Roadmap section 9 still applies: the encoding is a design-record block in
section 9.1, carried on Bill's card as AVIONICS does for new keys. It is a
CAN-FIX contract change even though no FIX key is minted.

## 4. How labels and topology reach the device

**Labels: resolved into the served config. No new device path.** This is the
checklist plan's delivery A (`checklist_instrument_plan.md:135-145`). When the
Worker serves `GET /device/config`, it resolves each reference against the
aircraft's active profile and writes plain pyEfis options. The device gets
the same `dbkey: FUELQ3` / `name: L TIP` it would get from a hand-written
config. `config_pull.py`, its validation and its rollback are unchanged.

Two consequences:
- **The ETag must cover the resolution.** Today it is `"v<n>"` or
  `"v<n>-p<hash>"` (`device_prefs.ts:58-75`). Renaming a tank changes no
  panel version, so the hash must take in the resolved bindings. Otherwise
  the device never sees the rename. One row (F3) carries both this and the
  preferences hash.
- **"Download YAML" and the editor preview use the same resolver**, so a
  hand-installed config and a pulled one cannot differ.

**Bands: through `aircraft.ini` (Phase C), not through the panel.**
`FUELQn.Max/lowWarn/lowAlarm` are FIX aux, and the gauge reads them from
fix-gateway (`abstract.py:315-327`). Until Phase C delivers `aircraft.ini`, the
device's bands come from whatever `init_data` the Pi has. Phase C also has to
keep `t` at or above the highest slot. A `FUELQ5.Max` line with `t: 3` raises
`KeyError` in the ini loader, and fix-gateway does not start
(`server.py:271-275`). So the pull tool sets `variables.yaml t` before it
installs the ini, and it never lowers `t` below a slot still used by a
served config (row F8).

**Topology: not delivered until something on the device reads it.** Nothing
on the Pi needs the feeds graph today. The first consumers will be a
totalizer, a fuel page and the 6c diagram. Its place is reserved: Phase C's
second pack member grows a `fuel_system.json` (the `meta.fuel` object
verbatim, schema-versioned) beside `aircraft.ini`, for fix-gateway, which
is where 8a puts "the aircraft's own interface". Delivering it before a
reader exists would be an unverified artifact on the box.

## 5. The shared reference-binding Prop kind

**Name: `aircraft_ref`.** One Prop kind with a `source` parameter,
replacing the proposed `checklist_ref` before it is built:

```python
# pyEfis screens/instrument_spec.py: PROP_KINDS gains "aircraft_ref"
Prop("tank", "aircraft_ref", source="aircraft.fuel.tanks",
     resolves={"dbkey": "slot_key", "name": "label"},
     label="Tank", help="Bind to a tank from the aircraft page")
Prop("checklists", "aircraft_ref", source="aircraft.checklists", multi=True)
```

- `source` names a provider: a collection on the aircraft profile. The
  editor fetches its items from one endpoint,
  `GET /api/projects/:id/aircraft/refs?source=...`, which returns
  `[{id, label, ...}]`. `schema.py` stays aircraft-agnostic, which is the
  checklist plan's reason for not using an enum.
- `resolves` says which device options the resolved item writes.
  `slot_key` means `FUELQ<slot>`. The checklist source writes the inlined
  bodies instead. The widget's internal interface is unchanged either way
  (`checklist_instrument_plan.md:158-162`).
- The stored panel holds only the id: `tank: tk_19c2`. The device never
  sees `tank:`, because the resolver removes it.

**Where it resolves:** in the Worker, at serve time, in one function
`resolveAircraftRefs(panelYaml, profile)`. It is used by `/device/config`,
the editor preview and the YAML download. Serve time, not save time: the
profile and the panel are versioned separately, and the device has to see
the pairing that is active now.

**What a gauge shows when its tank is removed from the profile.** The same
applies to a profile that has no fuel group, or no active profile at all.

- **On the device: the existing failed-sensor state, never zero
  gallons.** pyEfis already draws `fail` as value text `xxx`, a bar held at
  0, and the bad colour set (`abstract.py:151-176`, `:330-337`). The pilot
  already reads that as "no data", and it cannot be mistaken for an empty
  tank. The resolver emits the gauge with no `dbkey` and `unbound: true`,
  and keeps the last label it knew, so a pilot can tell which gauge lost its
  tank. pyEfis needs one small change (row F5): a gauge with `unbound: true`
  constructs in its fail state and never calls `fix.db.get_item`. That keeps
  construct-never-raises, so a stale reference cannot stop the EFIS booting.
  **The look is Bill's call (D3)**, since it is a safety cue.
- **In the configurator: before it ships, not after.** The editor marks an
  orphaned gauge in red, and the twin draws the same fail state. Activating
  a profile that orphans a binding on any published panel of that aircraft
  shows a warning that lists the panels. It is a warning, not a refusal,
  because removing a tank is legitimate. The device gets the honest failed
  gauge until the panel is fixed.

**What it does not do.** No reference names `FUELQT`; a "Total" gauge keeps a
plain `fixkey` binding. Fuel flow and pressure (`FUELFe`, `FUELPe`) are per
engine, and they would bind through a later `aircraft.engines` source. That
is out of scope here, but it is the reason the kind is general.

## 6. The configurator twin and the fidelity rule

The rule (`configurator/CLAUDE.md:188-198`): the preview reproduces what
pyEfis renders, and never invents.

- **The label.** The twin shows the resolved label, drawn the way the native
  bar draws `name` (`verticalBar.py:178-188`, `horizontalBar.py:72-83`).
  That includes what it does with a label too wide for the bar. That
  behaviour is measured, not assumed. Render the real widget offscreen at the
  stock widths with labels of increasing length, record what it draws (the
  recording-painter oracle), and set the profile's label limit N from it.
  The likely result is a short limit, because the stock bars are narrow.
- **Bands: only when the device has them.** The twin may draw the profile's
  `FUELQn` bands only once Phase C delivers `aircraft.ini` to that device.
  Before that, the device draws whatever its local `init_data` says, and a
  twin showing the profile's bands would be the twin inventing. Until then the
  twin keeps its sample bands, plus a visible "bands from the device's
  fix-gateway" note on the gauge.
- **The orphan state** is drawn from the same oracle as the device's fail
  state.
- **The picker** is the `aircraft_ref` field: a select of tank labels, each
  shown with its slot ("L TIP - FUELQ3"). The builder sees the wiring
  without having to choose by it.

## 7. Migrating the stock layouts' "Tank N" bars (CERT2b)

CERT2b (AER-5179) leaves every stock fuel bar as `name: "Tank N"`,
`dbkey: FUELQN`.

- **pyEfis stock configs stay that way permanently.** They are
  aircraft-agnostic and ship to people with no profile. That is the
  guardrail, and they need no change for FUEL1.
- **A configurator panel that started from a stock layout** keeps working
  unchanged: a plain `FUELQn` binding is still valid. The editor *offers* the
  migration and does not do it silently. When the aircraft has a tank in slot
  n, a bar bound to `FUELQn` with the name "Tank n" gets a one-click "Bind to
  L TIP", previewed in the twin. Accepting replaces `dbkey`/`name` with
  `tank: <id>`. The mapping is exact (slot n is `FUELQn`), but the pilot
  should see the label change before the glass does.
- **A bar whose name is not "Tank n"** (hand-edited) is not offered the
  migration. The builder chose that text, so it is left alone.
- **New gauges** dropped from the palette default to the `tank` reference
  when the aircraft has tanks.

## 8. Guardrails carried

- No Left, Center or Right is mapped to a tank number in any stock config,
  any migration default or any template. The c170b template's tanks get
  labels only if the builder types them.
- `FUELQT` stays a derived, display-only sum, and it is never a tank.
- No FIX key is added. The one contract change is the CAN-FIX index encoding
  (section 3), under ORG-011 and roadmap section 9.1.
- Verify-then-swap and construct-never-raises hold on the device: a stale
  reference fails one gauge, never the boot.

## 9. The build, as rows

Each row is one repo and one PR unless it says otherwise. They are listed in
dependency order. None of them is promoted until Bill has read this brief.

F1 is built (FUEL1a, AER-5271). Its label limit was provisionally 12
characters until F6 measured the native bar.

F6 is built (FUEL1f, AER-5547). The measured limit is a width, not a
character count: a label must fit every stock EMS2 tank bar
(`configurator/public/fuel_label.js`). The oracle,
`configurator/tools/fuel_gauge_oracle.py`, builds the stock screen at five
stock resolutions and reads six bar geometries off the real widget, from
44 px wide with a 13 px name font to 98 px with a 29 px font. Over those
bars, "L MAIN", "R MAIN", "L TIP", "AUX" and the default "Tank n" fit.
"HEADER" does not: a single word wider than the bar is centred and cut at
both edges. A two-word label that is too wide shows only its first wrapped
line ("R TIP AUX" shows "R TIP"). The 12-character ceiling stays on the text
box only. An unbound gauge's look, also from the oracle, is the bad colour
set, no bands, the value held at 0 and `xxx`.

F3 is built (FUEL1c, AER-5499). Its served form is in
`panel_config_format.md`, "Aircraft references". The refs endpoint returns
`{source, items, retired}`: `items` is the `[{id, label, ...}]` list of
section 5, and `retired` maps each removed id to its last label.

| Row | Size | Repo | What | Depends on |
|---|---|---|---|---|
| F1 | M | makerplane-data | Profile `meta.fuel` schema 2: ids, label, slot, edge list, capacity, unusable. Browser migration from schema 1. Client and server validation (section 2). `FUELQn.Min/Max` written from the tank. Tests, including one that fails on `dev`, that removing tank 2 of 3 keeps tank 3's edges. | D1, D2 |
| F2 | S | canfix-spec (fork) | Tank encoding for Fuel Quantity id + index (section 3) in `canfix.json` remarks and the generated reference, with the ORG-011 divergence note. The roadmap section 9.1 block goes on Bill's card. | D1 |
| F3 | M | makerplane-data | `resolveAircraftRefs` in the Worker, used by `/device/config`, the preview and the download. The `refs` endpoint. ETag covers the resolved bindings. Unbound emission. Tests for rename, then a changed ETag. | F1, F4 |
| F4 | S | pyEfis | `aircraft_ref` Prop kind in `instrument_spec.py` + `schema.py` mapping, and `tank` Props on the bar and arc gauges. `schema.json` for all three env prefixes. | D1 |
| F5 | S | pyEfis | A gauge with `unbound: true` constructs in its fail state without a FIX lookup. Test: a config with an unbound bar boots. | D3 |
| F6 | M | makerplane-data | Editor: the `aircraft_ref` picker, the orphan mark, the activation warning, the stock-bar migration offer, and the twin label from the oracle-measured limit. Oracle + fixture from the real widget. | F3, F4, F5 |
| F7 | S | fix-gateway | `FUELQT` sums `FUELQ1..FUELQ<t>` (following `t`), and publishes from the tanks it has rather than waiting for a tank no source writes. Test, failing on `dev`, with only `FUELQ1`/`FUELQ2` written. | D4 (it is a display change) |
| F8 | M | makerplane-data | Phase C hook: config pull sets `t` at or above the highest slot before installing `aircraft.ini`, and never lowers it under a served slot. Rides the Phase C row; it is not a separate delivery. | Phase C, F1 |
| F9 | S | fix-gateway | `map.yaml` rows for `FUELQ3`/`FUELQ4` (ids 552/553 index 0), and a commented example for tank 5 (id 550, index 1) per F2. | F2 |

The build is L in total, as the backlog said. F3/F4 are two repos in lockstep, promoted together dev to qa to main, as the
checklist plan's Phase 3 is. Once F4 lands, the checklist `aircraft_ref` work
(P1 Phase 3) reuses F3 and F4 and adds its own source, rather than building a
second kind.

## 10. Decisions (accepted by Bill, 2026-11-09)

Bill accepted each recommendation below as written.

- **D1:** `aircraft_ref` as one kind with a `source`, replacing
  `checklist_ref`, and stable tank ids with an editable `slot`. I recommend
  it, because a reference by number moves when a tank is removed.
- **D2:** `FUELQn` reads usable fuel (`Max = capacity - unusable`, per
  23.1337(b)(1)), and its senders are calibrated to match. I recommend it.
  The alternative, total fuel, makes "0" on the glass mean "unusable fuel
  left", which is not what a pilot reads.
- **D3:** how an unbound gauge looks. I recommend the existing failed-sensor
  look plus its last label. This is a safety cue, so it is your call.
- **D4:** `FUELQT` sums the tanks that exist and flags itself when one is
  failed or old, rather than staying unwritten. This is a display change on a
  shown key.
- **D5:** the CAN-FIX index encoding over fix-gateway-only keys for tanks 5
  and up (section 3).
