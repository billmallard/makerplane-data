<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The configurator reads its instrument tables from `schema.json` (INS4)

AER-6587, BACKLOG `INS4`, from ARCH3 section 4.1 (D3-D5, D7). pyEfis's
`schema.json` is the spec of every instrument the editor can place. The
editor's twins restate some of it, and until INS4 only frozen fixtures
compared the two. INS4 does two things about that:

1. **What the configurator can read, it reads.** The aircraft reference table
   (which option on which type binds a tank, and what it resolves to) now
   comes from the schema's `aircraft_ref` fields, in the Worker and in the
   editor (`refsFromSchema` in `configurator/public/aircraft_refs.js`). A new
   gauge type that declares a `tank` option resolves its tank with no
   makerplane-data edit.
2. **What a twin has to restate, pyEfis CI checks.**
   `configurator/tools/schema_twin_check.mjs` compares a `schema.json` with the
   real `editor.html`. pyEfis runs it against the schema it has just built, so
   a pyEfis change that leaves a twin behind fails there, before it ships.

## The reference table

The Worker reads `assets/editor/schema.json` (with the environment's prefix,
`docs/environments.md`) from R2 for `GET /device/config`, the served
download, the orphan check and the refs API. It caches the result per
isolate for a minute. The editor derives the same table from the schema it
loads at boot. If the schema is missing or does not parse, both use
`FALLBACK_REFS`, the schema-v3 table, so a failed read serves exactly what
was served before.

Rules (`refsFromSchema`):

- Options are matched by name. If two types declare the same option with a
  different `source` or `resolves`, the first type in name order wins and the
  check reports an error.
- The resolver leaves three kinds of reference alone, and the check reports
  each one: a `multi` reference, a `source` the resolver has no items for
  (`REF_SOURCE_FIELDS`), and a `resolves` that names a field the source's
  items do not carry. The option reaches the device as it was stored.

## The check

```
node configurator/tools/schema_twin_check.mjs path/to/schema.json [--strict]
```

It uses Node built-ins only, so no `npm install` is needed. Exit codes: 0
when clean, 1 on an error (or on a warning with `--strict`), 2 when the
command line is wrong.

| Kind | What |
|---|---|
| error | `schema_version` missing, or newer than the editor's `SCHEMA_VERSION_MAX` |
| error | `fd_style` enum or default differs from `FD_STYLES` (`[0]` is the default) |
| error | `static_text.alignment` enum differs from `_ALIGN_JUSTIFY`'s keys, or its default previews differently from an unset one |
| error | an `autopilot_panel` / `ap_annunciator` colour default differs from `AP_COLORS` / `AP_STRIP_COLORS`, or either side has a colour the other lacks (case-insensitive) |
| error | a `value_text` default differs from `VALUE_TEXT_DEFAULTS` |
| error | a type declares `font_family` with a default that differs from its `TEXT_DEFAULTS` font |
| error | a declared type has no twin and is not on `PLACEHOLDER_ONLY`, or a type on the list has a twin |
| error | an `aircraft_ref` the resolver cannot resolve, or one declared two ways |
| warning | no `pyefis_commit` stamp |
| warning | a `TEXT_DEFAULTS` font differs from `common_options.font_family`, and the type states no font of its own |
| note | a type or option a twin restates is absent from this schema. Older branches (qa, main) lag dev, and the editor being ahead of them is not drift |

`PLACEHOLDER_ONLY` is in the tool: `about`, `checklist`, `data_status`,
`flight_plan`, `listbox`, `nav_status`, `weston`. These types draw their palette
picture instead of a twin. A new type has to get a twin or be added to this
list.

## What pyEfis needs to do (INS4's pyEfis half, AVIONICS)

The makerplane-data side merges first (AER-6212 order). Against today's dev
schema the check reports no errors and one warning, the missing stamp. These
pyEfis changes clear it, and then `--strict` passes:

1. **Stamp the build commit.** Add a top-level `"pyefis_commit": "<40-hex SHA>"`
   to `schema.json`: the commit the schema was generated from. The editor
   shows it in its header tooltip.
2. **Keep the common font default the screen builder's.** No per-type font
   is needed. `ValueDisplay`'s constructor says Open Sans, but
   `apply_preferences` passes "DejaVu Sans Condensed" whenever a config names
   no font, so every type, `value_text` included, draws DejaVu Sans Condensed
   on the glass. The editor's `TEXT_DEFAULTS` say the same (AER-6598).
   pyEfis pins `common_options.font_family`'s default to the screen builder's
   with `test_common_font_default_is_the_screen_builders`. If a widget ever
   draws a different font when none is set, emit a per-type
   `instruments.<type>.options.font_family` with it: the editor merges
   `{...common_options, ...options}`, and the check holds the twin to it.
3. **Run the check in CI**, after the schema is built, against makerplane-data
   **`dev`**. This follows the fix-gateway precedent (#224, `ref: dev`). Use
   `dev` rather than `main`: makerplane-data `main` lags `dev`, and its editor
   lacks twins for types that pyEfis `dev` already declares.
   `configurator/package.json` is in the list because it marks `.js` as ES
   modules. For example:

   ```yaml
   - name: Check out the configurator's twins
     uses: actions/checkout@v6
     with:
       repository: billmallard/makerplane-data
       ref: dev
       path: _mpd
       sparse-checkout: |
         configurator/package.json
         configurator/tools/schema_twin_check.mjs
         configurator/public/aircraft_refs.js
         configurator/public/editor.html
         configurator/test/support/extract.mjs
       sparse-checkout-cone-mode: false
   - uses: actions/setup-node@v4
     with: { node-version: 22 }
   - name: The configurator's twins agree with the schema just built
     run: node _mpd/configurator/tools/schema_twin_check.mjs <built schema.json> --strict
   ```

Definition of done, as each part is proven:

- A new gauge type with a `tank` option resolves its tank with no
  makerplane-data edit. Proven here:
  `test/aircraft_refs.test.mjs`, "a new gauge type with a tank option
  resolves its tank".
- A seeded change to a spec enum or colour default fails the check. Proven
  here: `test/schema_twin_check.test.mjs`. Failing pyEfis CI is proven on the
  pyEfis PR.
- A new type with no twin, not on the list, fails the check. Proven here.
  Failing pyEfis CI is proven on the pyEfis PR.

Not in INS4: editor-side validation on save (enum, range, required). That is
the natural second makerplane-data PR.
