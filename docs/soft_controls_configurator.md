# Soft Controls in the Configurator — spec / plan

Status: **Phases A + B implemented and deployed** (2026-07-07; Phases C/D pending — see §6). Companion to
[device_deployment.md](device_deployment.md), [panel_config_format.md](panel_config_format.md),
and the pyEfis instrument-widget pipeline. Scope this round: the **Button** —
the reusable interaction primitive. Radios (MGL V16 and others) and the full
radio-screen composition are a **deliberate later add** (section 9).

## 1. What this is (and the gap it closes)

pyEfis has a rich **soft-control system** — Eric Blevins' `Button` instrument
plus the HMI encoder-navigation layer — that lets a panel *act*, not just
*display*: a touchscreen button (or a physical button, or a rotary encoder with
a switch) changes a radio frequency, cycles a nav source, flips a screen, adjusts
baro, and so on. Today **none of it is meaningfully representable in the
configurator.**

The configurator is currently a **layout tool for rendering instruments**. The
button system is an **interaction layer** — config-driven *behavior* plus
physical-control *wiring* — a different modeling problem the editor was never
built for. This spec brings the button into the configurator as a first-class,
editable, faithful instrument, and lays the seams for the interaction layer.

### Why it's not there today (verified in the repos)

- The `button` type **is** registered (`screenbuilder_factory.py`) and therefore
  in `schema.json`, so it appears in the palette — but:
  - Its **only** editor property is `config`, a **file-path string**
    (`Prop("config", "string", required=True, apply="special")`). The editor
    cannot see or edit the button's real definition.
  - It is `offscreen_renderable=false` + `builds_in_isolation=false`, so the
    editor draws it as a bare **placeholder box** (editor.html tags it
    `placeholder`) — no live twin.
- The button's actual definition lives in a **separate YAML** the editor never
  loads (text, colors, `dbkey`, `condition_keys`, and the `conditions`/`actions`
  block). That behavior system is entirely unmodeled.
- The encoder/physical-control layer (`hmi/`, `config/hmi/encoder_input.yaml`:
  `ENC3`/`BTN3`) is a separate input subsystem with no schema representation.

## 2. Ground truth: the Button today

Source: [`pyEfis src/pyefis/instruments/button/__init__.py`](https://github.com/makerplane/pyEfis)
(© Eric Blevins, 2023). A button is a `QPushButton` bound to a FIX key, defined
by a YAML file. Representative definition (`config/buttons/cdi-source.yaml`):

```yaml
type: simple                 # simple | toggle | repeat
text: "CDI\nSRC"
dbkey: TSBTN{id}33           # backing FIX key; {id} -> node id
bg_color: "#101418"
fg_color: "#00ffff"
condition_keys: [NAVSRC]     # FIX keys the conditions watch
conditions:
  - when: "CLICKED eq true"                       # pycond expression
    actions: [{change value wrap: "NAVSRC, 1"}]
  - when: "NAVSRC lt 0.5"
    actions: [{set bg color: "#1f6b1f"}]
  - when: "NAVSRC ge 0.5"
    actions: [{set bg color: "#1a4d80"}]
```

**Fields:** `type`, `text`, `dbkey`, `bg_color`, `fg_color`, `transparent`,
`hover_show`, `repeat_interval`/`repeat_delay` (repeat type), `condition_keys`,
`conditions`.

**Conditions** are [`pycond`](https://pypi.org/project/pycond/) expressions over
FIX keys, plus the special token **`CLICKED`** (true on the click event). Each
condition carries an `actions` list.

**Actions** — the authoritative catalog (`hmi/actionclass.py` + button-local
styling):

| Group | Actions |
|---|---|
| FIX value | `set value`, `change value`, `change value wrap`, `sync value`, `toggle bit` |
| Screen nav | `show screen`, `show next screen`, `show previous screen` |
| Modes/units | `set airspeed mode`, `set egt mode`, `set instrument units` |
| HMI menu | `activate menu`, `activate menu item`, `menu encoder`, `set menu focus` |
| Button style/state | `set bg color`, `set fg color`, `set text`, `toggle`, `hide` |
| Escape hatch | `exit`, **`evaluate`** (arbitrary Python `eval` — see §6f) |

**Touchscreen ↔ physical linkage.** A button is backed by a FIX key
(`TSBTN{node}{n}`, ranges from fix-gateway `database/variables.yaml`:
`n` nodes × `s` touchscreen buttons, default 5 × 40). A touchscreen tap, a
physical button (fix-gateway `rpi_button`), and an encoder-click all drive the
**same** key — that's what makes them interchangeable.

**Encoder navigation.** `config/hmi/encoder_input.yaml` names one encoder
(`ENC3` rotate = move focus between on-screen buttons; `BTN3` press = click).

## 3. Scope

**In scope (this spec):**
- The **Button** as a first-class, editable, rendered configurator instrument.
- A **conditions/actions editor** (form-based, not raw YAML).
- **Delivery**: buttons ride the existing device config-pull pipeline (their
  YAML emitted alongside the screen), including TSBTN key allocation.

**Out of scope (later — section 9):** MGL V16 and other **radios**; the full
**radio-screen composition** (`include:` directives like `RADIO_COMBINED`); a
visual **encoder/HMI wiring** editor; the **listbox** (frequency presets).

## 4. Design decisions to lock in

### 4a. Expose the button's real properties in the schema (pyEfis side)

Today the button schema is a single `config` path. Replace it with the button's
actual fields, so the editor can build and edit a real button:
`text`, `type` (enum), `dbkey`, `bg_color`, `fg_color`, `transparent`,
`hover_show`, `condition_keys`, `conditions`. This is the same
`screenbuilder_factory.py` → `editor/schema.py` → `schema.json` path every other
instrument uses. **The `conditions` block needs a structured Prop kind** (a list
of `{when, actions[]}`), which the Prop model does not have yet — see 4c.

Backward-compat: keep accepting a `config:` **path** (existing hand-authored
panels), but the editor authors the **inline** form. The device-side installer
writes whichever the design carries.

### 4b. A live Button twin (fidelity rule)

Per the configurator's HARD fidelity rule, the editor must render the button as
pyEfis draws it — a `QPushButton` with the configured text/colors, and, where
practical, the **condition-driven appearance** for the current (or a previewed)
FIX state (e.g. the CDI-source button showing green vs blue). Two options:

- **A (preferred): a JS twin** in `editor.html` — a styled `<button>` that
  evaluates the conditions against a small previewable FIX state. Buttons are
  simple CSS; this is cheap and interactive, and keeps the whole thing in the
  browser.
- B: make the pyEfis button `offscreen_renderable` and ship a palette SVG — but
  a static SVG can't show the condition-driven states, so A wins.

### 4c. The conditions/actions editor

The heart of the feature. A form, never raw YAML:

- **A condition** = a `when` expression builder (key · comparator · value, plus
  the `CLICKED` event and simple AND/OR) + an ordered **actions** list.
- **An action** = pick a verb from the §2 catalog (grouped), with an
  argument editor whose shape depends on the verb (a FIX key + value for
  `change value`; a screen picker for `show screen`; a colour for
  `set bg color`; …). The catalog is **data-driven from a schema** so pyEfis
  remains the source of truth for what actions exist (mirror how instrument
  options are curated).
- Show the generated YAML in the live code pane (like the panel editor already
  does) for power users / debugging.

### 4d. TSBTN key allocation

A touchscreen button must name a **registered** `TSBTN{node}{s}` key or it's a
fatal `KeyError` at screen-build time (the button does `get_item()` without
`create=True`). The device installer already learned this (it takes the top of
the range for injected switch buttons). The configurator should **allocate TSBTN
slots automatically** per device (tracking used slots), so the builder never
hand-picks a key. Surface the assigned key read-only.

### 4e. Delivery via the config-pull pipeline

Buttons reach the aircraft the same way panels do (device_deployment.md): the
editor's design compiles to native pyEfis config, and `pyefis_data/config_pull.py`
installs it. Concretely, each button becomes a `buttons/<name>.yaml` written
alongside the managed screen, and the screen's button instrument references it —
**exactly the pattern `config_pull` already uses** for its injected
`buttons/managed-next-*.yaml` switch buttons. So most of the device plumbing
exists; the new work is authoring the button YAML from the design.

### 4f. The `evaluate` action — safety

The action catalog includes `evaluate` → Python `eval`. The configurator emits
config that **runs on the device**. Per the team decision (§11) **all verbs are
exposed by default**, including `evaluate` — for a builder editing **their own**
aircraft this is their code on their airplane. Two guardrails remain: `evaluate`
(and any raw expression) carries a clear **in-UI warning** that it runs arbitrary
code on the device; and the pipeline's ownership-scoping must continue to
guarantee **one account's config can never land on another's device** (already
true — device-token-authed, per-user config).

## 5. Two repos move together

Same pipeline as every instrument (umbrella CLAUDE.md):

1. **pyEfis** — extend the `button` record in `screenbuilder_factory.py`
   (real properties), curate it in `editor/schema.py`, and **publish an actions
   schema** (a new small `aircraft`-params-style export listing the action
   catalog + argument shapes). Regenerate `schema.json` → R2.
2. **makerplane-data configurator** — build the Button twin + the
   conditions/actions editor in `editor.html`; teach the compile/emit step to
   write `buttons/*.yaml`.
3. **makerplane-data `pyefis_data`** — install the button YAMLs on pull (largely
   the existing switch-button path).

## 6. Phases

- **Phase A — Button as a first-class instrument (no logic yet). DONE.**
  Schema exposes `text`/`type`/`dbkey`/colors; the editor renders a live Button
  twin and edits those fields; TSBTN auto-allocation; emitted + installed via
  config-pull. A *static* button you can place, style, and deploy. *This is the
  bulk of the plumbing and immediately useful.* (pyEfis `display-changes` a58873b;
  makerplane-data `feat/accounts-auth`.)
- **Phase B — Conditions/actions editor. DONE (deployed 2026-07-07).** A "Behaviour"
  section on the Button properties panel: a guided `when`-builder (key/comparator/
  value clauses joined by AND/OR, plus a raw pycond escape hatch) and a
  per-condition actions list. Verbs + argument shapes come from `schema.actions`
  (the `_ACTIONS` catalogue, kept in lockstep with the HMI registry by a CI test);
  arg editors render per `arg.kind`; `evaluate` shows an in-UI warning.
  `condition_keys` is derived automatically from the clauses. The button becomes
  *interactive* (cycle NAVSRC, show screen, set colours by state). (pyEfis
  `display-changes` b004250 catalogue + schema.json→R2; makerplane-data
  `feat/accounts-auth` 071b670 editor UI.) Not yet exercised signed-in end-to-end
  on the live site (auth-gated); verified via a standalone proto built from the
  live CSS+JS+schema.
- **Phase C — Delivery hardening + key management.** Robust TSBTN allocation
  across multi-screen panels, dedupe, and the device install of `buttons/*.yaml`
  proven on the Pi end-to-end.
- **Phase D (later) — the interaction layer.** A representation of the
  encoder/physical-button bindings (`ENC3`/`BTN3`, physical↔TSBTN linkage) and
  the `listbox` (presets). Groundwork for radios.

## 7. What Phase A/B do NOT require

No change to the runtime button widget's *behavior* — it already reads its YAML.
The work is: (1) the schema exposing real fields, (2) the editor UI + twin, and
(3) emitting the YAML from the design. The device already knows how to install a
`buttons/*.yaml`.

## 8. Open questions — RESOLVED 2026-07-06 (see §11)

1. **Inline vs referenced button config.** → button options are carried
   **inline in the screen** (see §11); the legacy `config:` file-path stays
   supported for hand-authored panels.
2. **Action catalog exposure.** → **expose all verbs by default** (§11).
3. **Condition builder depth.** → **start simple** — single
   `key comparator value` (+ `CLICKED`) and a small AND/OR, with a raw-YAML
   escape hatch.
4. **Actions schema ownership.** → **pyEfis exports it** (source of truth).

## 9. Deferred: radios and the interaction layer

- **Radios (MGL V16 and others)** are a **longer-term add** (Bill, 2026-07-06).
  The radio "instrument" is a *screen composition* of buttons + a preset
  `listbox` + radio-specific displays wired to the MGL V16, and is explicitly
  **work-in-progress on the pyEfis side** (Eric hasn't the MGL V16 in hand yet).
  It rides on this button work but adds: the `include`/composition model, the
  listbox editor, and the radio key mapping.
- The **encoder/HMI navigation** editor (Phase D) is the other half of the
  interaction story and is likewise deferred.

## 10. Notes

- This extends Eric Blevins' button system; coordinate with Eric (time-
  constrained, so we can carry the configurator work). His widget stays the
  runtime source of truth and the twin tracks it (fidelity rule).
- The button legitimately **writes** FIX values — that is a control's job and is
  *not* the layout-vs-FIX confusion of issue #64 (which is about gauge bands /
  V-speeds masquerading as layout options). Conditions *reference* FIX keys as
  data binding; that's part of the button's definition.

## 11. Decisions (Bill, 2026-07-06)

1. **Inline button config (resolves §8.1 + reconciles §4a/§4e).** The button's
   fields live **inline as the instrument's options** in the screen YAML — the
   same as every other instrument — not as a separate `config:`-path file. This
   makes the button first-class: the schema exposes its real options, the editor
   edits them, and the screen carries everything (so the existing config-pull
   install path needs no new file handling). *pyEfis side:* the `Button` widget
   is extended to accept an **inline config dict** as an alternative to a config
   file; the screenbuilder passes the instrument's `options`. The legacy
   `config:` file-path form stays supported for existing hand-authored panels.
2. **Expose all verbs by default (resolves §8.2).** The action picker offers the
   whole catalog, `evaluate` included. `evaluate`/raw expressions carry an
   in-UI warning (§4f); ownership-scoping keeps config on its own device.
3. **Condition builder: start simple (resolves §8.3).** v1 = one
   `key comparator value` row (+ the `CLICKED` event) with a small AND/OR, and a
   raw-YAML escape hatch for anything richer. Full pycond modeling is a later
   refinement.
4. **pyEfis owns the actions schema (resolves §8.4).** A small export from
   pyEfis lists the action catalog + per-verb argument shapes; the configurator
   renders the picker from it, so pyEfis stays the single source of truth
   (mirrors instrument-option curation).

**Phase A, as reconciled:** extend the `Button` widget to accept inline options;
expose `text` / `type` / `dbkey` / `bg_color` / `fg_color` / `transparent` /
`hover_show` in `screenbuilder_factory.py` + `editor/schema.py`; regenerate
`schema.json`; build the live Button twin in `editor.html`; auto-allocate the
`TSBTN` `dbkey`. A static, styleable, deployable button — no conditions yet.
