<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The Knob (Encoder) in the Configurator — spec / plan

Status: **DRAFT for review** (2026-07-07). Companion to
[soft_controls_configurator.md](soft_controls_configurator.md) (the Button — read
that first; this reuses its plumbing), [device_deployment.md](device_deployment.md),
and the pyEfis instrument-widget pipeline. Scope this round: **the knob** —
Eric Blevins' rotary-encoder interaction layer. This is the "Phase D — the
interaction layer" the button spec deferred (§6, §9 there).

The knob is one *control source* in the larger control layer:
[control_bindings.md](control_bindings.md) is the umbrella — the shared model for
how any control (button or knob) changes and reflects an instrument's live setting
through the FIX bus. The knob's per-instrument `encoder_set_key` (§2d) is already a
binding by another name; that umbrella is where it and the button meet.

## 1. What this is (and the gap it closes)

The **knob** is a physical **rotary encoder + push** that navigates and edits the
panel: turn it to step through the instruments on a screen, press to enter one,
turn to change its value (heading bug, baro, radio frequency, a preset list),
press to confirm. Eric wired it in
[screenbuilder_encoder.py](../../pyEfis/src/pyefis/screens/screenbuilder_encoder.py)
(`EncoderController`, GPLv2, © Eric Blevins 2026).

Today the knob is **only hand-authorable in YAML**. There is no configurator
support: you cannot pick which physical encoder drives a screen, mark which
instruments the knob can reach, set their navigation order, or configure how the
knob edits a gauge. The button just became a first-class, configurator-editable
control; the knob is the other half of the same interaction story and is still
YAML-only.

### The one thing to get straight first

**The knob is a physical device — there is nothing to draw.** The button is an
on-screen widget, so it has a live twin and the fidelity rule governs its
appearance. The knob has no on-screen representation at all. So this feature is
**not** about rendering a knob; it is about **binding, ordering, and edit
behaviour**:

- *binding* — which encoder/push FIX keys drive this screen;
- *ordering* — which instruments the knob can land on, and in what sequence;
- *edit behaviour* — for an editable instrument, what key it writes and how
  (step size, digit-by-digit, confirm, real-time).

The closest honest analog to a "twin" is a **canvas overlay** that shows the
navigation ring (numbered badges on the reachable instruments) and marks which
are knob-editable. That is the visual deliverable — not a picture of a knob.

## 2. Ground truth: the knob today (verified in the repo)

Three layers, all real:

### 2a. Screen-level binding

A screen names two FIX keys and a timeout. Defaults live in
[config/hmi/encoder_input.yaml](../../pyEfis/src/pyefis/config/hmi/encoder_input.yaml):

```yaml
encoder: ENC3          # rotation: fix key, valueWrite[int] carries the signed detent count
encoder_button: BTN3   # push: fix key, valueChanged[bool]
encoder_timeout: 10000 # ms of inactivity before the selection clears
```

The screen reads these in
[screenbuilder.py:191-194](../../pyEfis/src/pyefis/screens/screenbuilder.py#L191)
(`get_config_item("encoder")` etc.) and `EncoderController.configure_inputs`
subscribes: rotation → `encoderChanged`, push → `encoderButtonChanged`. **If a
screen has no `encoder`/`encoder_button`, the whole knob layer is inert** (the
controller early-returns on an empty list).

### 2b. Per-instrument enrolment: `encoder_order`

An instrument joins the knob's navigation ring by carrying one option:

```yaml
- type: numeric_display
  options:
    encoder_order: 600031     # any int; the ring is sorted ascending by this
```

[screenbuilder_options.py:99-102](../../pyEfis/src/pyefis/screens/screenbuilder_options.py#L99)
special-cases `encoder_order`: it appends `{inst: index, order: <value>}` to
`screen.encoder_list`. `EncoderController` sorts that list by `order`
([screenbuilder_encoder.py:30](../../pyEfis/src/pyefis/screens/screenbuilder_encoder.py#L30))
to get the turn sequence. Disabled instruments are skipped while cycling. Real
panels use big namespaced integers (`100011`, `200030`, `400028`, `600031`) —
region × 100000 + local — assigned by hand.

### 2c. The `enc_*` protocol (how a widget participates)

Each reachable widget implements five methods; `EncoderController` drives them:

| method | called when | contract |
|---|---|---|
| `enc_selectable()` | building the ring | `True` if the knob may land here |
| `enc_highlight(on)` | selection moves on/off it | show/clear the highlight (orange) |
| `enc_select()` | push while highlighted | enter edit mode; return `True` to hold control |
| `enc_changed(delta)` | turn while editing | apply the delta; return `True` to keep control |
| `enc_clicked()` | push while editing | confirm / advance; return `True` to keep control |

Implemented by:

- **Gauges** ([gauges/abstract.py:401-500](../../pyEfis/src/pyefis/instruments/gauges/abstract.py#L401))
  — the rich case: turning edits the gauge's value and writes a FIX key.
- **Listbox** ([listbox/__init__.py:112-139](../../pyEfis/src/pyefis/instruments/listbox/__init__.py#L112))
  — turning scrolls the preset rows, press selects the row.
- **Button** ([button/__init__.py:335-357](../../pyEfis/src/pyefis/instruments/button/__init__.py#L335))
  — `enc_select` fires the button's conditions as if tapped (the knob "clicks" it).

### 2d. Gauge edit options (the knob-tuning surface)

The gauge encoder editor ([abstract.py:63-89](../../pyEfis/src/pyefis/instruments/gauges/abstract.py#L63))
reads these per-instrument options (applied as raw YAML attrs):

| option | meaning |
|---|---|
| `encoder_set_key` | FIX key the edit writes (defaults to the gauge's own key) |
| `encoder_multiplier` | value change per detent (e.g. `0.01`, heading `0.008333…`) |
| `encoder_set_real_time` | write on every detent vs only on confirm |
| `encoder_revert` | restore the pre-edit value if the edit times out unconfirmed |
| `encoder_num_mask` | digit-by-digit mode, e.g. `"000.0000"` — select each digit |
| `encoder_num_require_confirm` | require a confirming press before committing |
| `encoder_num_8khz_channels`, `encoder_num_excluded`, `encoder_num_limits`, `encoder_num_mask_blank_character` | radio-tuning refinements (8.33 kHz spacing, forbidden freqs, per-digit limits) |
| clamping via the gauge's `lowRange`/`highRange` when `clipping` is set | bound the edited value |

The digit-mask path is exactly how a radio frequency is tuned by knob (see the
`TEXT1` "Radio Active Freq" styles in
[preferences.yaml:593-605](../../pyEfis/src/pyefis/config/preferences.yaml#L593)):
`encoder_num_require_confirm`, `encoder_multiplier: 0.008333…`,
`encoder_num_8khz_channels`, a `DSEG14` font. This ties the knob directly to the
deferred radio work.

## 3. Scope

**In (this round):**
- Screen-level binding: choose `encoder` / `encoder_button` keys + `encoder_timeout`.
- Navigation ring: a per-instrument "reachable by knob" toggle + drag-to-order,
  with the big `encoder_order` integers **auto-generated** (the user never types
  them). A canvas overlay showing the ring order.
- The simple gauge edit options (`encoder_set_key`, `encoder_multiplier`,
  `encoder_set_real_time`, `encoder_revert`, confirm) surfaced per editable type.

**Out / later:**
- The **digit-mask / radio-tuning** editor (`encoder_num_mask` and its family) —
  advanced, and best done alongside the radio composition work (§6 Phase C).
- Multi-encoder panels beyond the standard ENC3/BTN3 until the hardware story is
  settled (§7).
- Any on-screen knob graphic (there is none to be faithful to).

## 4. Design decisions to lock in

### 4a. The knob is not an instrument — it is a screen setting + instrument options

Two schema surfaces, both new:

1. **A screen-level HMI block.** The configurator has no per-screen HMI settings
   UI yet; add a small "Knob / encoder" panel (screen scope, not selection
   scope). It emits `encoder`, `encoder_button`, `encoder_timeout` into the
   `screens.<name>` block — which is exactly where `screenbuilder` already reads
   them, so no pyEfis change is needed for binding.
2. **Per-instrument encoder options in `schema.json`.** `encoder_order` and the
   editable-type edit options are currently raw YAML, invisible to the schema.
   Add them the same way the button's options were added — through the
   `InstrumentSpec`/registry so the editor learns them without drift.

### 4b. No twin; a navigation-order overlay instead

Because there is no widget to render, the deliverable is a **canvas overlay**:
numbered badges (1, 2, 3 …) on each knob-reachable instrument in ring order, and
a distinct mark for "knob-editable." Turning the knob in real life walks these in
order; the badge is the faithful representation of that sequence. This replaces
"build a twin" for the knob and is genuinely useful for laying out a reachable,
logically-ordered panel.

### 4c. Ordering UX: hide the integers

Users should never type `600031`. The editor:
- shows a **"Reachable by knob"** checkbox per instrument;
- shows an ordered **ring list** (like the existing layers panel) with drag /
  up-down to reorder, scoped to the current screen;
- **auto-assigns** `encoder_order` on save — spaced integers (e.g. 10, 20, 30 …
  or region-namespaced to match the hand-authored convention) so later inserts
  don't force a full renumber. The raw integer stays available as an advanced
  field for parity with existing hand-authored panels.

### 4d. An "encoder capability" catalogue, exported by pyEfis

Mirror the button's action catalogue: pyEfis exports, per instrument type, (a)
whether it is `enc_selectable`, (b) whether it is *editable* by the knob, and (c)
which `encoder_*` options it honours. Gauges → editable, full option set;
listbox → selectable, no edit options (it scrolls); button → selectable, no edit
options (the knob presses it). The editor renders the edit-option fields only for
types that support them. Source of truth = pyEfis (same anti-drift rule as the
action catalogue), verified by a CI test.

### 4e. ENC/BTN key selection (analogous to TSBTN allocation)

The button auto-allocates `TSBTN{id}<n>`. The knob's ENC/BTN keys come from the
hardware encoder plugin in fix-gateway, so the editor should **offer the encoder
keys the FIX catalogue advertises** (ENC1/ENC2/ENC3 …, BTN1/BTN2/BTN3 …) in a
dropdown and **default to ENC3/BTN3** (today's convention). It should not invent
keys. How many encoders exist and how they are named on real hardware is the main
open question (§7).

### 4f. Safety

Knob edits **write** FIX keys — legitimate (a tuning control's job), same posture
as the button (not the #64 layout-vs-FIX confusion). Two real safeties already in
the widget and worth surfacing in the UI:
- `encoder_num_require_confirm` — require a press to commit (radio uses it);
- `encoder_revert` — a timed-out edit restores the prior value.
`encoder_set_real_time` writes on every detent; the UI should flag it for keys
where mid-turn intermediate values matter.

## 5. Two repos move together

Same split as the button work:
- **pyEfis** (`display-changes`): add the per-instrument encoder options + the
  capability catalogue to the schema exporter; regenerate `schema.json` → R2.
- **makerplane-data** (`feat/accounts-auth`): the screen-level knob panel, the
  reachable/ordering UI + canvas badges, and the per-type edit-option fields in
  `editor.html`. Delivery rides the existing config-pull pipeline unchanged
  (everything lands in the screen YAML: `encoder*` at screen scope,
  `encoder_order` + edit options in each instrument's `options`).

## 6. Phases

- **Phase A — Binding + navigation ring.** Screen-level `encoder`/
  `encoder_button`/`encoder_timeout` panel; per-instrument "reachable" toggle +
  drag-to-order with auto-assigned `encoder_order`; canvas order-badge overlay.
  Makes the knob *configurable and visible* with no per-widget edit logic. This is
  the bulk of the value and mirrors button Phase A.
- **Phase B — Gauge edit options.** Expose `encoder_set_key`,
  `encoder_multiplier`, `encoder_set_real_time`, `encoder_revert`, and confirm
  for editable types, driven by the capability catalogue. The knob can now *tune*
  a gauge, not just navigate.
- **Phase C — Digit-mask / radio tuning + listbox presets.** The
  `encoder_num_mask` family and the preset `listbox` editor. Done with the radio
  composition work (both need the same MGL V16 context Eric is waiting on).
- **Phase D (later) — Multi-encoder / hardware provisioning.** More than one
  physical encoder per panel, once the hardware key story is fixed (§7).

## 7. Open questions (for Bill / Eric)

1. **Hardware encoders.** How many physical encoders exist on the target panels,
   and what FIX keys do they write (only ENC3/BTN3, or ENC1..N/BTN1..N)? Where
   does that plugin live (fix-gateway)? Drives 4e and Phase D.
2. **Order namespacing.** Keep the region×100000 convention for
   `encoder_order`, or let the configurator own a simpler spaced scheme
   (10/20/30…) since it now generates them? (Interop with hand-authored panels
   argues for keeping the convention or preserving raw values on import.)
3. **Editable-type coverage.** Beyond gauges/listbox/button, should any other
   type become knob-editable (e.g. a numeric text tuning a frequency directly)?
4. **Confirm-by-default.** Should `encoder_num_require_confirm` be the default in
   the editor for anything that writes a flight-relevant key?

## 8. What this reuses from the button work

- The two-repo schema→R2→editor pipeline and the fidelity discipline (here it
  means "faithful *behaviour*," since there is no appearance).
- The catalogue-exported-by-pyEfis pattern (action catalogue → encoder capability
  catalogue), with a CI anti-drift test.
- The config-pull delivery path (no new file handling; options ride in the screen
  YAML).
- The instrument-registry (`InstrumentSpec`) as the single source of truth for
  per-type options.
