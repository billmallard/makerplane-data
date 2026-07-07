<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Control Bindings — the runtime control layer (spec / architecture)

Status: **Decisions recorded** (Bill, 2026-07-07; see §11) — implementation
tracked in the Control Bindings epic (billmallard/makerplane-data#20), landing on
the `controls` branch. **Umbrella** over
[soft_controls_configurator.md](soft_controls_configurator.md) (the button) and
[knob_encoder_configurator.md](knob_encoder_configurator.md) (the knob). Those two
specs are *control sources*; this spec is the shared **target** they act on — the
model that lets a control change an instrument's live setting, and lets a control
show that setting's current state. Companion to
[device_deployment.md](device_deployment.md) and the pyEfis instrument-widget
pipeline.

> **This formalises intent that is already in the codebase.** It is not a new
> direction. The pieces are here; they were built one at a time and never named
> as one layer. This spec names it, makes it uniform, and gives the configurator
> a single place to author it. Where the code already gestures at this, it is
> cited below.

## 1. The gap (and the intent already in the tree)

We can place a button and make it *do* things (soft_controls Phase B), and we can
navigate/edit with a knob. Both ultimately want to change some **setting on an
instrument** — a map's range, its orientation, which layers are on. Today each
such wire is a bespoke, hand-authored, per-widget affair, and most of them do not
exist yet. There is no uniform way to say "this control changes that setting," and
no way for the configurator to author it.

The architecture already anticipated this in three places:

- **`InstrumentSpec` (Bill, 2026)** already separates an instrument's inputs into
  three categories and, for category 2 (values read from fix-gateway at runtime),
  the docstring lists *"range"* and says: *"Described here so a **future
  FIX-database editor** can drive them"*
  ([instrument_spec.py:35-41](../../pyEfis/src/pyefis/screens/instrument_spec.py#L35)).
  That future editor is part of what this spec describes.
- **The moving-map widget** ships `range_up()`/`range_down()`/`set_layer()` under
  a comment reading *"HMI hooks (bound to buttons/encoder in screen YAML
  **later**)"*
  ([map/__init__.py:166](../../pyEfis/src/pyefis/instruments/map/__init__.py#L166)).
  This spec is that "later," done as a system instead of a one-off.
- **The units button** already routes a command to named instruments that
  self-select (`setUnits`: `if self.dbkey in names or '*' in names …`,
  [abstract.py:381](../../pyEfis/src/pyefis/instruments/gauges/abstract.py#L385)),
  and **fix-gateway** already defines UI-input key namespaces (`TSBTN` touch
  buttons, `BTN`/`ENC` physical inputs) and ships a **persister** plugin. The
  primitives exist; they need unifying.

## 2. The core idea in one picture

Everything in this stack talks through the FIX database, never by direct call
(the umbrella rule: *"every box talks through the FIX database"*). A control
binding is that rule applied to **UI intent**:

```
   CONTROL                 FIX KEY                    INSTRUMENT
   (writer)                (the state)                (reader/deriver)
  ┌────────┐   writes    ┌───────────┐   valueChanged  ┌──────────────┐
  │ button │────────────▶│  MAPRANGE │────────────────▶│ Map: range_nm│
  │  knob  │             │  (index)  │                 │  = ladder[i] │
  └────────┘             └───────────┘                 └──────────────┘
       ▲                       │
       │      reads (condition)│
       └───────────────────────┘
        ANNUNCIATOR (button "green when active")
```

Four roles, all decoupled through one key:

1. **A bindable setting** on an instrument (range, orientation, a layer).
2. **A FIX key** that *is* that setting's live value.
3. **One or more controls** that write the key (a button action, an encoder).
4. **Optional annunciators** that read the key back and show state (a button
   condition, a text/lamp).

The control never touches the instrument. It writes a key. The instrument reads
the key. That indirection is the whole value.

## 3. Why bus-state, not direct calls

Two ways a control could change a setting: **write a FIX value** (declarative —
"this key *is* the range") or **call a method** ("run range_up now"). This spec
chooses bus-state for *settings*, and keeps the imperative path for *momentary
actions*. The reasons:

- **Readback for free.** Because the state lives on the bus, any widget can show
  it. A button annunciates "terrain: on" with a condition
  (`when MAPTERRAIN eq true → set bg color green`) — the exact pattern the
  `CDI\nSRC` button already uses for NAVSRC. A direct method call leaves nothing
  on the bus to read, so nothing can reflect current state.
- **Multi-source by construction.** A button, a knob, another screen's button,
  and a physical panel switch can all drive the same setting with zero extra
  wiring — they all just write the key.
- **Decoupling.** The control author and the instrument author never share a
  Python reference or a naming registry. They share a key name.
- **It is the stack's own rule.** Sensor data already flows this way; UI intent
  should too.

**The one nuance:** *"one key, one writer"* is a rule for **sensor sources** (two
AHRS must not both write `PITCH`). A **UI setting** key is deliberately the
opposite — many controls may write it, and last-writer-wins is *correct* (a button
and a knob that both mean "set the range" do not conflict). The spec must state
this so a bound UI key is never mistaken for a source-arbitration violation.

**Momentary actions stay on the bus too.** The rare press-and-do controls fit FIX
keys, so no imperative "call this method" verb is planned (§11.5): **Control Wheel
Steer** is a *held boolean* (True while held, False on release; the autopilot reads
it), and **transponder IDENT** is an *edge-trigger* key (write True; the consumer
pulses and resets). Only a genuinely stateless *and* edgeless command would need
the imperative path, and none is known.

## 4. Ground truth — the pieces to unify (all real today)

| Piece | Where | Role in this system |
|---|---|---|
| 3-category instrument model | [instrument_spec.py](../../pyEfis/src/pyefis/screens/instrument_spec.py) — `Prop` (config), `FixValue` (FIX-read), `preview` | We extend category 1 with a *bindable* flag; §5 |
| Button write verbs | `_ACTIONS`: `change value`, `change value wrap`, `set value`, `toggle bit` | The control's write side; drive bound keys unchanged |
| Button read (annunciation) | conditions over FIX keys (soft_controls Phase B) | The annunciator role |
| Encoder edit | `encoder_set_key` etc. ([abstract.py:63-89](../../pyEfis/src/pyefis/instruments/gauges/abstract.py#L63)) | The knob as a second control source |
| Self-select-by-name | `setUnits` ([abstract.py:381](../../pyEfis/src/pyefis/instruments/gauges/abstract.py#L381)) | The existing imperative command path (§10 Phase 4) |
| Read-derive-repaint | map subscribes `LAT/LONG/TRACKM/ALT` ([map:97](../../pyEfis/src/pyefis/instruments/map/__init__.py#L97)); every gauge | The instrument's reader side — already ubiquitous |
| UI-key namespaces | fix-gateway `TSBTN` (touch), `BTN`/`ENC` (physical), `HIDEBUTTON` | The key layer (§6) |
| Persister plugin | fix-gateway `persister` | Optional persistence of a setting across restarts (§6) |
| FIX item value/min/max/dtype | used by `changeValueWrap` ([functions.py:42](../../pyEfis/src/pyefis/hmi/functions.py#L42)) | Wrap/clamp semantics come from the key definition |

Nothing here is speculative. The system is the composition, not new primitives.

## 5. The model: bindable properties

A setting a control can change is a **config property that is also live-bindable**.
It keeps its category-1 identity (the designer can still set a default in the
editor) and gains a runtime channel (a FIX key can drive it).

### 5a. Spec extension (`Prop.bindable`)

Add to [`Prop`](../../pyEfis/src/pyefis/screens/instrument_spec.py#L74) an optional
binding descriptor:

```python
Prop(name="range_nm", kind="number", default=10.0,
     bindable=Binding(
        semantics="index_ladder",   # bool | index_ladder | enum | scalar
        key_option="range_key",     # the option naming the FIX key (default "<name>_key")
        ladder_option="range_ladder"))  # where the steps come from, for index_ladder
```

Semantics tell the configurator how to write the key and which verb to offer:

| semantics | key shape | control verb | example |
|---|---|---|---|
| `bool` | 0/1 | `toggle bit` | a map layer |
| `index_ladder` | int, min 0 / max N (step count) | `change value wrap` | map range (2,5,10,…) |
| `enum` | int index into an enum | `change value wrap` | orientation track/north |
| `scalar` | float, min/max/step | `change value` | a continuously-tuned setting |

### 5b. Widget behaviour (a `LiveProperty` mixin)

If a bindable property's `<prop>_key` option is set, the widget **subscribes to
that key's value** and derives the attribute; otherwise it uses the static value
exactly as today. One shared mixin does this — subscribe, honour the
fail/old/bad convention, derive-and-repaint — so ~43 widgets do not each
re-implement it. (The repo's own CLAUDE.md flags that FixItem boilerplate as the
obvious dedup; this is a natural place to start paying it down.)

The mixin owns the one real gotcha: **startup ordering.** The widget's config
default and the key's value on first connect must not clobber each other (this is
the same restore-clobber we already hit with the persister — the config default
overwrote the saved value before restore). Policy: **the config default seeds the
key** if the key has no persisted value; otherwise the key wins and the widget
adopts it on first `valueChanged`. Decided once, in the mixin, not per widget.

## 6. The FIX key layer — a UI/setting namespace

Bound keys live in a dedicated **UI/setting namespace**, the direct analog of the
existing `TSBTN` namespace for buttons:

- **Definition.** The keys are generated into the fix-gateway init data (a
  configurator-produced include), not hand-curated per aircraft. Same philosophy
  as `TSBTN{id}<n>` auto-allocation for buttons.
- **Shape from semantics.** `bool` → min 0 max 1; `index_ladder`/`enum` → min 0,
  **max N** (the *step count*, not N-1) so `change value wrap` cycles cleanly — its
  span is `max - min`, so max must equal the number of steps for the wrap to reach
  every index and roll over (the ladder is non-uniform, an index is not; the widget
  clamps a received value into `[0, N-1]`). `scalar` → min/max/step from the `Prop`.
  The wrap/clamp maths already live in `changeValueWrap`/`changeValue`.
- **Per-instance.** Where two of the same instrument can coexist (two maps, two
  screens), the key is templated per instance (`MAPRANGE{id}` or a
  configurator-assigned suffix) so they do not gang — same reason buttons use
  `{id}`.
- **Persistence (opt-in).** A setting that should survive a restart rides the
  fix-gateway **persister** plugin. Off by default; a per-binding choice in the
  editor.
- **Compute-friendly.** A bound key can also feed a fix-gateway *compute* (as
  NAVSRC feeds the `select` compute that produces COURSE/CDI). The binding does
  not preclude server-side logic behind the key.

## 7. The configurator — the binding surface (your "aircraft screen down the road")

This is where it becomes a capability instead of hand-authoring. The screen
editor is the place you wire controls to settings.

- **Authoring model.** Select a control (a placed button, or the screen's
  encoder). Pick a **Target**: `(instrument, bindable property, operation)` — e.g.
  *Map → Range → cycle*, *Map → Terrain → toggle*. The editor then, in one action:
  1. auto-allocates the FIX key (you never type a key name);
  2. sets the target instrument's `<prop>_key` option;
  3. writes the control's action (`change value wrap` / `toggle bit` / …) — the
     same actions the button editor already produces;
  4. optionally generates the annunciation condition ("green when active").
- **A Bindings view.** Per screen, a list of `(control → target)` mappings with
  validation: key collisions, targets whose instrument was deleted, a setting
  bound but no control writing it.
- **Canvas affordances.** Mark instruments that have live-bound settings; show the
  knob order-badges from the knob spec on the same canvas. The panel *shows* its
  control wiring.
- **Reuse.** The button conditions/actions editor is the write+annunciate UI; the
  knob spec supplies the encoder as a second source; `schema.actions` supplies the
  verbs; the instrument schema supplies the bindable-property list. This spec adds
  the **Target** concept and the key auto-allocation that ties them together.

## 8. Worked example — the moving map falls out of the system

Declare the map's settings bindable (in its `InstrumentSpec`):

| Setting | semantics | key | control action |
|---|---|---|---|
| range | index_ladder (over `range_ladder`) | `MAPRANGE{id}` | `change value wrap: "MAPRANGE,1"` |
| orientation | enum (track_up/north_up) | `MAPORIENT{id}` | `change value wrap: "MAPORIENT,1"` |
| terrain layer | bool | `MAPTERRAIN{id}` | `toggle bit: "MAPTERRAIN"` |
| roads / airports / navaids / … | bool each | `MAP<LAYER>{id}` | `toggle bit` |

The map already has the *doer* side of every one (`range_up/down`,
`set_layer`); the only widget change is to derive these from the keys via the
`LiveProperty` mixin instead of only from direct calls. Then:

- A button *"Range"* with `change value wrap: "MAPRANGE,1"` cycles the range and
  wraps at the ends.
- A button *"Terrain"* with `toggle bit: "MAPTERRAIN"` toggles the layer and turns
  green while on (annunciation condition).
- The encoder, assigned the same keys, does the same thing — no extra wiring.

The map feature we started from is now just the first instance of the system.

## 9. How it composes with the button and knob specs

- **The button** ([soft_controls_configurator.md](soft_controls_configurator.md))
  is a *control source* (writes keys via its actions) **and** an *annunciator*
  (reads keys via its conditions). Already shipped; needs nothing new to drive a
  bound key.
- **The knob** ([knob_encoder_configurator.md](knob_encoder_configurator.md)) is a
  second *control source*. Its per-instrument `encoder_set_key` is already a
  binding by another name; this spec's key layer is where it and the button meet.
- **This spec** owns the shared *target* model (bindable properties), the *key
  layer*, and the configurator's *binding surface*. The other two plug into it.

## 10. Phases

- **Phase 1 — the mechanism, proven on range.** `Prop.bindable` + the
  `LiveProperty` mixin in pyEfis; the map derives `range_nm` from `MAPRANGE`.
  Driven by the *existing* button verbs with a hand-defined key. No configurator
  UI yet. Smallest end-to-end slice; it is the range feature done right.
- **Phase 2 — the configurator binding surface.** The Target picker, key
  auto-allocation, and annunciation generation. Map range/orientation/layers
  become first-class in the editor.
- **Phase 3 — the key layer + persistence.** Formalise the UI/setting namespace in
  fix-gateway init-data generation, per-instance templating, and opt-in persister
  wiring. Configurator emits the fixgw include.
- **Phase 4 — breadth.** Mark settings bindable across settable instruments, in
  the coverage order below (§11.6). Momentary commands stay on the FIX bus
  (§11.5) — no new imperative verb is planned.

## 11. Decisions (Bill, 2026-07-07)

1. **Key naming — target-prefixed.** A key is named `<TARGET-ABBREV><PROPERTY>`
   (e.g. `MAP` + `RANGE` = `MAPRANGE`), per-subsystem rather than one flat
   namespace, with the per-instance suffix reusing the existing button `{id}`
   node scheme (`MAPRANGE{id}`). The name reads as "which instrument, which
   setting."
2. **Configurator-generated, in YAML.** The keys are generated by the
   configurator as a **fix-gateway YAML database include** — matching fixgw's own
   config format (`config/database/*.yaml`, where `TSBTN`/`HIDEBUTTON` already
   live), not XML. XML/CAN-ID mapping is a separate hardware-binding layer
   (CANFIX); UI/soft-setting keys need no CAN mapping and stay pure YAML.
3. **Startup + persistence — adopt the defaults, refine experientially.** Config
   default seeds the key when it has no persisted value; otherwise the key wins
   and the widget adopts it on first `valueChanged`. Persistence is opt-in per
   binding. These are starting defaults; we expect to refine them by testing and
   "getting bit" (the house methodology — don't converge early).
4. **Bindable ≠ category-2 (#64) — confirmed distinct.** Gauge bands / V-speeds
   are category-2 *read-only reference* values on a key's `min`/`max`/`aux`. A
   bindable **setting** is read-write on the key's *value* channel. The two stay
   firmly separate so the layout-vs-FIX boundary (#64) holds.
5. **Momentary commands stay on the bus — no imperative verb.** They are rare, and
   the known ones fit FIX keys, so we do **not** add a "call this method" verb:
   - **Control Wheel Steer** (autopilot disengaged while the button is held) is a
     *held boolean* — the button writes `True` on press / `False` on release; the
     autopilot reads the key. (Button hold/repeat type; a small guarantee that
     release writes `False` may be needed.)
   - **Transponder IDENT** (squawk ident ~18 s on one press) is an *edge-trigger*
     key — write `True`; the transponder/fixgw acts on the rising edge and resets.
   Both are ordinary bindings. The imperative path is dropped unless a genuinely
   stateless *and* edgeless command appears (none known).
6. **Coverage order — PFD first.** The **map** is the mechanism proof (its
   `range_up`/`set_layer` doers already exist, so it is the cheapest end-to-end
   slice). Then **PFD** is the first real application target. **EMS** follows and
   is expected to be lighter (not yet dug into). **Radios** are far down the road.

## 12. Reuse / non-goals

- **Reuses:** the `InstrumentSpec` registry (single source of truth), the two-repo
  schema→R2→editor pipeline, the button conditions/actions editor, the knob spec,
  the config-pull delivery path, and fix-gateway's existing UI-key + persister
  machinery.
- **Non-goals:** this is not a new scripting language on the device (conditions
  stay pycond, actions stay the catalogue); not source arbitration for sensors
  (that is a separate roadmap item); not a rendering concern (a bound setting has
  no appearance of its own — the *instrument* renders the result).
- **Related:** testing this layer against the configurator surfaced the
  single-prod-artifact problem (`schema.json` lives only in prod R2), addressed by
  [environments.md](environments.md) — DEV/QA/PROD envs so a dev schema can be
  tested without touching prod.
