<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The tab-section container in the configurator — spec / plan

Status: **AER-172 approved; §4g contract agreed; implementation waits on
pyEfis#132 merging** (updated 2026-08-16). Companion to
[pyEfis#131](https://github.com/billmallard/pyEfis/issues/131) (the engine-side
design; read that first — this doc assumes it) and
[panel_config_format.md](panel_config_format.md) (the wire format this feature
extends). Tracked as AER-173, child of AER-172.

Board approval on AER-172 landed, and Bill resolved this doc's open questions
1–3 directly on pyEfis#131 (comments, 2026-08-16) — see §4g and §7 below,
updated to match. The engine-side implementation is now a real PR,
[pyEfis#132](https://github.com/billmallard/pyEfis/pull/132), open for review
and not yet merged; its own description says explicitly that AVIONICS-DATA
needs it landed to build against a real schema. This side's exporter reads
`schema.json` generated from pyEfis's live `REGISTRY`, so starting Phase A
against an unmerged branch means building against a shape that can still move
in review. **Implementation remains on hold until pyEfis#132 merges** — that is
now the only remaining gate.

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
by reading the dataclass, not inferred. Section 4g below records the contract
this gap resolved to, now agreed and implemented in pyEfis#132.

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

**In (this round, once pyEfis#132 merges):**
- Palette entry for the container.
- A drop-target scoped to the container's active tab, distinct from the
  screen-tab bar.
- Tab add/remove/rename/reorder authoring.
- Twin rendering of the active tab's nested instruments inside the container's
  box.
- Save/load round-trip of the nested `tabs:` structure (no flattening).

**Out / later (matches pyEfis#131 non-goals, both now confirmed — §7 Q1/Q2):**
- Nesting a tab_section inside a tab_section.
- Hardware-button tab switching in the UI — resolved out of scope entirely
  (touch/click only), not deferred; see §6 Phase D.
- Any authored persistence of *last-selected* tab across a device reload —
  resolved: none. Separate from an authored *default* tab, which is in scope
  as a plain scalar Prop (§4d).

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

`default_tab` is **resolved as a plain scalar `Prop`** (Bill, pyEfis#131
comment, 2026-08-16 — see §7 Q2): selects the tab shown on
load, always resets there on reload/power-up, never remembers the
last-selected tab. No device-writable persistence needed — `activeTab` above
stays purely an editor-session UI concern, never round-tripped through
`default_tab`.

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

### 4g. Exporter contract — agreed

**Resolved** (Bill, [pyEfis#131 comment](https://github.com/billmallard/pyEfis/issues/131),
2026-08-16), matching this section's original proposal: a new `InstrumentSpec`
field `containers: list[ContainerSlot]`, parallel to
`properties`/`fix_values`/`preview` — not a `Prop`/`PROP_KINDS` addition.
`Prop.__post_init__`'s scalar-default invariant stays intact; a subtree is a
fourth, distinct input category.

```python
@dataclass
class ContainerSlot:
    name: str            # e.g. "tabs" -- the YAML key
    label: str = ""
    help: str = ""
```

`schema.py`'s `_entry_from_spec`/`_entry_from_curation` both grow a
`"containers": [...]` key (empty on the curation branch, same uniform-shape
rule `options`/`fix_values`/`preview` already follow) — this is what
pyEfis#132 implements. Once that PR merges and `schema.json` regenerates to
R2, the editor's exporter-consuming code should branch on a non-empty
`containers` entry to render a drop-target instead of a properties field,
exactly as this section originally proposed. `tests/editor/test_schema.py`
grows a matching "every container slot has a name + label" assertion on the
pyEfis side.

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

Sequencing, updated: (b) the §4g exporter contract is now agreed, and board
approval on AER-172 has landed. The one remaining gate is (a) —
[pyEfis#132](https://github.com/billmallard/pyEfis/pull/132) (the
`InstrumentSpec`/recursion implementation) merging, since there is nothing
real to build against until `schema.json` regenerates from a merged
`REGISTRY` and ships to R2.

## 6. Phases (once pyEfis#132 merges)

- **Phase A — Palette + empty container + tab CRUD, no nested rendering.**
  Container places, tabs can be added/renamed/removed/reordered, saves/loads a
  `tabs: [{label, instruments: []}]` skeleton. Proves the state model and
  save/load recursion without touching `renderCanvas()`.
- **Phase B — Drop-target + flat nested twin.** Instruments can be dragged
  into the active tab and render inside the container's box at the fidelity
  the rest of the editor already has for top-level instruments.
- **Phase C — Polish.** Element groups inside a tab, layers-panel nesting
  indication, drag an existing top-level instrument into a tab (and back out).
- **Phase D — dropped.** Resolved (§7 Q1): touch/click only for v1, hardware-
  button switching is an explicit non-goal on the pyEfis side, not a deferred
  phase on this one. Revisit only if pyEfis reopens that decision.

## 7. Open questions

Questions 1–3 are resolved (Bill, pyEfis#131 comments, 2026-08-16); kept here
for the record rather than deleted, since they shaped §4d/§4g/§6 above.

1. ~~Hardware-button tab switching — in v1 or later?~~ **Resolved: touch-only
   for v1**; not a fast-follow phase on the pyEfis side either, so Phase D is
   dropped rather than deferred.
2. ~~Active-tab persistence on reload — reset to configured default, or
   remember last-selected?~~ **Resolved: always resets to `default_tab`.** No
   runtime persistence, no device-writable value — §4d's `activeTab` stays
   editor-session-only, confirming the framing that question posed.
3. ~~Is the §4g `containers` field proposal the right shape?~~ **Resolved:
   yes**, adopted as proposed — see §4g for the concrete `ContainerSlot`
   dataclass, implemented in pyEfis#132.
4. Do element groups genuinely work unmodified when dropped inside a tab once
   the tab's local coordinate space is threaded through, or does
   `expandGroups()` need a container-aware variant? Still open — needs
   pyEfis#132's actual recursion behavior to check against, not something to
   resolve freehand.
5. Should the palette simply suppress the tab_section tile while a tab
   drop-target is focused (reusing the existing `hidden` mechanism, §2d), or
   should the drop-target itself reject a dropped tab_section with a message?
   Still open; only matters given Q1's confirmed "no nested tab_section" rule.
