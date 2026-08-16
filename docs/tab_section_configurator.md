<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The tab-section container in the configurator — spec / plan

Status: **DRAFT for review** (2026-08-16). Companion to
[pyEfis#131](https://github.com/billmallard/pyEfis/issues/131) (the engine-side
design; read that first — this doc assumes it) and
[panel_config_format.md](panel_config_format.md) (the wire format this feature
extends). Tracked as AER-173, child of AER-172. **Scoping only — implementation
is on hold pending board approval on AER-172** (per the issue). Nothing here is
committed; it is a proposal for what the configurator side would need once the
pyEfis-side schema lands.

## 1. What this is (and what it isn't)

A **container instrument**: one placed instrument on the grid, like any other,
that holds an ordered set of **named tabs**, each tab holding its own nested
list of real instruments laid out against the container's own box. Config
shape per pyEfis#131: `tabs: [{label, instruments: [...]}]`.

Two things it is explicitly **not**, both already present in this codebase and
easy to confuse with:

- **Not the screen-tab bar** (`editor.html` `.screens-bar`/`.screen-tab`,
  `state.screens[]`, `renderScreensBar()` — `editor.html:51-53, 291-314,
  3126-3140`, line numbers as of `dev`). That switches **whole authored
  screens** for pyEfis#72 (PFD/EMS/RADIO via nav buttons) — a device-level,
  `main.defaultScreen`-scale concept. tab_section is in-screen, one container
  among many instruments on a single screen. The two features are orthogonal
  and both stay; this feature must not reuse `.screen-tab` styling/state or a
  user will read the container as "another screen switcher."
- **Not an element group.** Groups (`state.groups`/`groupsById`,
  `expandGroups()`, `editor.html:2976-2996`) are the closest existing nesting
  precedent — a group instance stores a `def.grid {rows, columns}` and
  `def.instruments[]` positioned in **group-local** coordinates, the same shape
  a tab's contents will need. But groups are a fiction: pyEfis has no group
  concept, so `expandGroups()` **flattens** every child to absolute
  screen-coordinates at save time and the saved YAML never mentions the group.
  tab_section is the opposite: pyEfis#131 has the engine **recurse** into the
  container's own coordinate space at build time, so the nested `tabs:`
  structure **must survive into the saved YAML as-is** — this is the first
  configurator instrument type whose on-the-wire config is a tree, not a flat
  list.

## 2. Ground truth verified in the repos

### 2a. The editor's instrument model is flat, screen-scoped

`state.instruments` is a flat array aliased to `state.screens[active].instruments`
(`editor.html:291-314`). `renderCanvas()` branches once per type
(`editor.html` — `buildAttitude`/`buildHSI`/`buildTape`/… /fallback SVG) and
`toScreenInstrument()`/`toPyefisDoc()` (`editor.html:2999-3038`) walk that one
flat list per screen. Nothing in the canvas, selection, drag/resize, layers, or
save path expects an instrument to itself contain instruments. A tab_section's
nested list needs its own walk at every one of these points — this is a
cross-cutting change, not a new `build*()` branch.

### 2b. pyEfis's screen builder is a single flat loop today

`screenbuilder.py`: `for i in self.get_config_item("instruments")` calls
`screenbuilder_factory.create_instrument()` once per **top-level** instrument,
positioned via `screen.get_grid_coordinates()` against the **screen's** grid.
There is no recursion today. pyEfis#131 names this ("building tab contents
needs the same `create_instrument()` path to recurse using the tab section's
own width/height as the coordinate space") as the real engineering lift on
that side — confirmed by reading the current loop, which has nothing to
recurse into yet. The configurator side is blocked on that landing before a
saved tab_section config would actually build on a device.

### 2c. The `InstrumentSpec`/`Prop` model is scalar-only, by construction

`instrument_spec.py`: `PROP_KINDS = {"string", "number", "integer", "boolean",
"enum", "color", "fixkey"}`. `Prop.__post_init__` assumes a scalar `default`
and (for `enum`) a flat `enum` list. There is no kind for "a property whose
value is itself a list of instrument records." This is exactly the gap
pyEfis#131 and the AER-173 issue body both flag for coordination — confirmed
by reading the dataclass, not inferred. Section 4f below is a concrete
proposal for that contract, offered for discussion, not a decision (that
module is pyEfis's).

The live schema confirms the same shape from the exporter side: fetched
`schema.json` (schema_version 3, prod) gives each instrument a flat `options:
{name: {type, default, label, ...}}` dict — e.g. `airspeed_box.options` is
scalar Prop descriptors, nothing recursive anywhere in the file.

### 2d. Palette filtering already has a "don't offer this" hook

`InstrumentSpec.hidden` filters a real, buildable type out of the palette
without removing it from the schema (used for deprecated types). This is the
natural mechanism to keep tab_section out of its own palette while a tab
drop-target is focused, if pyEfis#131 keeps "no nesting a tab_section inside a
tab_section" as a v1 non-goal (it currently does) — see open question 5.

## 3. Scope

Mirrors pyEfis#131's v1 boundary — this doc does not expand it:

**In (this round, once schema lands and board approves):**
- Palette entry for the container.
- A drop-target scoped to the container's active tab, distinct from the
  screen-tab bar.
- Tab add/remove/rename/reorder authoring.
- Twin rendering of the active tab's nested instruments inside the container's
  box.
- Save/load round-trip of the nested `tabs:` structure (no flattening).

**Out / later (matches pyEfis#131 non-goals):**
- Nesting a tab_section inside a tab_section.
- Hardware-button tab switching in the UI — only if pyEfis#131 open question 1
  resolves to "in scope," and only after the engine side exposes it.
- Any authored persistence of *last-selected* tab across a device reload
  (runtime state, not a layout concern) — separate from an authored *default*
  tab, which is in scope as a plain scalar Prop (§4d, open question 6).

## 4. Design decisions (proposed — none of this is locked)

### 4a. Palette + placement

One new palette tile, placed on the grid exactly like any instrument
(`row`/`column`/`span`) — no new placement mechanism needed. Dropped fresh, it
starts as an empty container with one default tab ("Tab 1") rather than zero
tabs, so it's never in an unrenderable state.

### 4b. Drop-target: container-scoped, not a new global mode

When a tab_section is selected, its bounding box becomes an active drop zone
for the **currently active tab only**. Dragging a palette instrument (or an
existing top-level instrument) onto that box inserts it into
`tabSection.tabs[activeTab].instruments`, positioned in **tab-local**
coordinates — reusing the group's local-grid convention
(`def.grid`/child `row`/`column`/`span` relative to that grid,
`editor.html:2976-2996`) rather than inventing a new one. Unlike groups, this
local structure is **not** flattened away at save (§4e).

### 4c. Tab authoring UI

A small tab strip rendered inside the container's own box on canvas, plus an
inspector section when the container is selected: ordered tab list with
rename / reorder (drag or up-down) / delete / "+ Add tab". This needs its own
CSS class, not `.screen-tab` (§1) — note `editor.html` already has an unused
`.tabs` rule (`editor.html:60`) that predates this feature and should not be
assumed to be reusable without checking what it was for.

### 4d. State model

A tab_section instrument entry gets `tabs: [{label, instruments: [...]}]` plus
an editor-only `activeTab` index (UI state, never saved — canvas shows one
tab's contents at a time, same as `state.active` does for screens but scoped
to this one instrument). Nested instrument records keep the exact same shape
as top-level ones (`type`/`row`/`column`/`span`/`options`), just positioned
against the container's box instead of the screen's.

### 4e. Save/load path — recurse, don't flatten

`toPyefisDoc()`/`toScreenInstrument()` need a tab_section branch that
**recurses** the same expand-and-convert pipeline over each tab's instruments,
emitting the nested `tabs:` block as-is (in contrast to `expandGroups()`,
which absorbs `type: "group"` entirely). `fromStoredDoc()`/`applyCode()` need
the matching recursive parse so hand-edited YAML and the Code pane round-trip.
Element groups dropped *inside* a tab should fall out for free if the
drop-target threads the tab's local coordinate space through the existing
group-expansion math — flagged to verify explicitly, not assumed (open
question 4).

### 4f. Twin rendering — the big lift

`renderCanvas()`'s per-type branch model assumes one flat pass over
`state.instruments` against the full canvas. A tab_section branch needs to (1)
draw the tab strip, (2) recursively invoke the same per-type render logic for
the active tab's instruments, with `rect()` (`editor.html`'s grid→pixel
mapping) parameterized by the container's own sub-box instead of the full
canvas. That parameterization — threading a coordinate-space argument through
`rect()`/`renderCanvas()` instead of assuming the screen — is the largest
single chunk of work here, independent of the exporter question. Per
`configurator/CLAUDE.md`'s fidelity rule, once pyEfis actually renders nested
tabs, the twin should be checked against that, not designed freehand.

### 4g. Exporter contract — a proposal, pyEfis's call

Rather than forcing a `tabs`-like property into `Prop`/`PROP_KINDS` (deliberately
scalar-only — §2c), propose a **new `InstrumentSpec` field**, parallel to
`properties`/`fix_values`/`preview`, e.g. a `containers: list[ContainerSlot]`
where a `ContainerSlot` describes "this instrument has N named tabs, each
holding a list of instruments" rather than a leaf value. The schema exporter
would then emit a `container:` block per such instrument (distinct from its
flat `options:` block) so the editor gets an explicit signal to render a
drop-target instead of a properties-panel field. This keeps `Prop`'s
scalar-default invariant intact instead of special-casing it. Offered for
discussion with AVIONICS/pyEfis, since `instrument_spec.py` is pyEfis's
module and its own anti-drift test (`tests/editor/test_schema.py`) would need
to grow a matching assertion.

## 5. Two repos move together

Same split as every prior configurator feature (button, knob, groups):

- **pyEfis**: `tab_section` `InstrumentSpec` + recursive `create_instrument()`
  path (pyEfis#131, not this doc's concern); the exporter contract in §4g;
  `panel_config_format.md`'s `tab_section` schema entry; regenerate
  `schema.json` → R2.
- **makerplane-data**: palette entry, drop-target, tab CRUD UI, recursive
  save/load, recursive twin rendering — all in `configurator/public/editor.html`.
  Delivery still rides the existing config-pull pipeline unchanged (the nested
  structure lives inside the same screen YAML, nothing new to transport).

Sequencing: this side cannot start real implementation before (a) pyEfis#131's
`InstrumentSpec`/recursion lands, since there is nothing to build against, and
(b) the §4g exporter contract is agreed, since it decides whether the editor
reads a `container:` block or something else. Board approval on AER-172 gates
both repos starting implementation regardless.

## 6. Phases (once approved)

- **Phase A — Palette + empty container + tab CRUD, no nested rendering.**
  Container places, tabs can be added/renamed/removed/reordered, saves/loads a
  `tabs: [{label, instruments: []}]` skeleton. Proves the state model and
  save/load recursion without touching `renderCanvas()`.
- **Phase B — Drop-target + flat nested twin.** Instruments can be dragged
  into the active tab and render inside the container's box at the fidelity
  the rest of the editor already has for top-level instruments.
- **Phase C — Polish.** Element groups inside a tab, layers-panel nesting
  indication, drag an existing top-level instrument into a tab (and back out).
- **Phase D (later, contingent on pyEfis#131 open question 1)** — hardware-
  button switching authoring, if the engine adds it.

## 7. Open questions

1. (Carried from pyEfis#131) Hardware-button tab switching — in v1 or later?
   Determines whether Phase D exists at all.
2. (Carried from pyEfis#131) Active-tab persistence on reload — reset to a
   configured default, or remember last-selected? If "remember," is that
   engine-runtime-only (no editor concern) or does it need a device-writable
   value? Shapes whether §4d's `activeTab` ever needs to leave the editor.
3. Is the §4g `containers` field proposal the right shape, or does AVIONICS
   have a preferred contract already in mind for pyEfis#131's recursion work?
4. Do element groups genuinely work unmodified when dropped inside a tab once
   the tab's local coordinate space is threaded through, or does
   `expandGroups()` need a container-aware variant?
5. Should the palette simply suppress the tab_section tile while a tab
   drop-target is focused (reusing the existing `hidden` mechanism, §2d), or
   should the drop-target itself reject a dropped tab_section with a message?
   Only matters if "no nested tab_section" stays a hard rule.
6. Should `default_tab` be a plain scalar Prop (fits today's model, independent
   of the harder recursion questions) so a reload has a predictable landing
   tab regardless of how question 2 resolves?
