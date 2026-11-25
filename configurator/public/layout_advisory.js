// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Bill Mallard
//
// The per-layout advisory (BACKLOG LAYADV1, CAP-119): a list of rules run over
// a resolved panel layout, each emitting plain-language findings that tell
// the builder what this panel does and does not cover for crew alerts. It
// advises and never refuses a save (annunciate, never restrict).
//
// The input is the native pyEfis document the editor saves (toPyefisDoc:
// `screens: {NAME: {layout, instruments}}`, element groups already expanded,
// tab_section pages under `tabs`), so a hand-written YAML is judged the same
// way as an editor layout.
//
// The rules check a stated requirement, not free-standing advice: pyEfis wiki
// "Designing Your Panel", section "Installation requirement: crew alerts in
// your primary field of view" (CASDOC1). Rule set agreed with Safety and
// Compliance on AER-6355 (2026-11-23):
//   rule 1  CAS is in use and a screen carrying an attitude instrument has no
//           CAS window or marker: warn, and list the alerts with no other cue
//           on that screen. Other screens without one get the conditional
//           form, since the requirement covers any screen up in flight.
//   rule 2  a CAS window or marker that runs past its screen's grid, or is
//           disabled (a disabled one never counts as shown).
//   rule 3  no CAS window or marker anywhere: say what that means.
// Plus the requirements that apply: the field-of-view rule when a CAS
// surface or annunciator lamp is placed, and one sense only (no tone).
//
// Pure, no imports, never throws.

// The instruments that show crew alerts (pyEfis cas_window, cas_marker).
export const CAS_SURFACE_TYPES = ["cas_window", "cas_marker"];

// "A screen you fly from", as rule 1 reads it from the layout: one carrying an
// attitude instrument (Safety's proposal on AER-6355, checked against the
// schema registry: these are the two types that draw attitude; the rest of the
// schema's "attitude" category, ap_annunciator and turn_coordinator, do not).
// pyEfis spells the first one this way.
export const ATTITUDE_TYPES = ["atitude_indicator", "virtual_vfr"];

// The alerts fix-gateway ships (dev 9f67beb, config/connections/cas.yaml and
// plugins/cas.py:280-288), and the instruments that give each one a cue of its
// own in pyEfis (dev b30b785). An instrument counts only when it is always on
// the screen, never on a tab page. A value_text bound to HEAD is not counted:
// whether it flags depends on its binding, so the advisory errs towards
// naming the alert.
export const CAS_ALERTS = [
  // autopilot/__init__.py NO_DATA_TEXT "NO AUTOPILOT DATA";
  // autopilot/fma.py: "NO AP DATA" when a state key goes old.
  { id: "APLINK", text: "AP LINK LOST", cues: ["autopilot_panel", "ap_annunciator"] },
  // hsi/__init__.py _hdg_flag_shown (red HDG flag); HeadingDisplay "XXX" on
  // fail; DG_Tape follows HEAD old/bad/fail.
  { id: "HDGFAIL", text: "HDG FAIL", cues: ["horizontal_situation_indicator", "heading_display", "heading_tape"] },
  // hsi/__init__.py _nav_flag_shown (NAV flag when the CDI bar is removed).
  { id: "NAVFAIL", text: "NAV FAIL", cues: ["horizontal_situation_indicator"] },
  // A fuel sender that never reports: "the Total bar just reads low, with no
  // flag" (CAS1 brief section 1). Raised only for the tanks the gateway lists.
  { id: "FUELNODATA", text: "FUEL QTY <n> NO DATA", cues: [] },
];

function list(v) { return Array.isArray(v) ? v : []; }
function num(v, d) { return typeof v === "number" && Number.isFinite(v) ? v : d; }
function typeName(t) { return t === "cas_window" ? "CAS window" : "CAS marker"; }
// A screen name is builder text; keep it on one line.
function quote(name) { return '"' + String(name).replace(/[\r\n]+/g, " ") + '"'; }

// Every instrument on a screen with where it sits: visible (top level) or on a
// tab page, and the grid it is placed against.
function walk(instruments, layout, onTab, out) {
  for (const inst of list(instruments)) {
    if (!inst || typeof inst !== "object") continue;
    out.push({ inst, layout, onTab });
    for (const tab of list(inst.tabs)) {
      if (!tab || typeof tab !== "object") continue;
      walk(tab.instruments, tab.layout || { rows: 110, columns: 200 }, true, out);
    }
  }
  return out;
}

// How a `disabled` key hides an instrument, in words, or null when it does not.
// pyEfis reads it top level and under options (screenbuilder.py:254, :221,
// :200; screenbuilder_config.is_disabled): `true` hides it, a string names a
// device preference (`not X` inverts it) the configurator cannot see, so the
// advisory takes it as possibly hidden. AER-6547, Safety's requested fix.
function disabledHow(inst) {
  for (const v of [inst.disabled, inst.options && typeof inst.options === "object" ? inst.options.disabled : undefined]) {
    if (v === true) return "is disabled (disabled: true), so it never shows";
    if (typeof v === "string" && v.trim())
      return `is disabled by the device preference "${v.trim().replace(/[\r\n]+/g, " ")}", so it may not show`;
  }
  return null;
}

// The part of a rect outside its grid, in words, or null when it fits.
function offGrid(inst, layout) {
  const rows = num(layout && layout.rows, 110), cols = num(layout && layout.columns, 200);
  const r = num(inst.row, 0), c = num(inst.column, 0);
  const span = inst.span || {};
  const h = num(span.rows, 0), w = num(span.columns, 0);
  if (h <= 0 || w <= 0) return "has no size";
  const out = [];
  if (r < 0 || r + h > rows) out.push(`rows ${r} to ${r + h} of ${rows}`);
  if (c < 0 || c + w > cols) out.push(`columns ${c} to ${c + w} of ${cols}`);
  return out.length ? "runs past the edge of the screen (" + out.join(", ") + ")" : null;
}

/**
 * Run the rule set over a native pyEfis panel document.
 * `casAvailable`: whether the schema carries a CAS surface type at all; with
 * false the advisory is empty, so it cannot advise before pyEfis can comply.
 * Returns { findings: [{rule, level, screen, text}], requirements: [text] };
 * level is "warning" or "note".
 */
export function layoutAdvisory(doc, casAvailable = true) {
  const findings = [], requirements = [];
  const screensObj = doc && typeof doc === "object" && doc.screens && typeof doc.screens === "object" ? doc.screens : {};
  if (!casAvailable) return { findings, requirements };
  const screens = Object.keys(screensObj).map((name) => {
    const sc = screensObj[name] || {};
    return { name, items: walk(sc.instruments, sc.layout || { rows: 110, columns: 200 }, false, []) };
  });
  const isSurface = (it) => CAS_SURFACE_TYPES.includes(it.inst.type);
  const casInUse = screens.some((s) => s.items.some(isSurface));
  const lamps = screens.filter((s) => s.items.some((it) => it.inst.type === "annunciator"));

  for (const s of screens) {
    const shown = s.items.some((it) => isSurface(it) && !it.onTab && !disabledHow(it.inst));
    if (!casInUse || shown) continue;
    const flying = s.items.some((it) => ATTITUDE_TYPES.includes(it.inst.type));
    const visible = new Set(s.items.filter((it) => !it.onTab && !disabledHow(it.inst)).map((it) => it.inst.type));
    const silent = CAS_ALERTS.filter((a) => !a.cues.some((t) => visible.has(t))).map((a) => a.text);
    const tail = silent.length
      ? " These alerts have no other cue on that screen: " + silent.join(", ") + "."
      : " Each alert fix-gateway ships keeps its own cue there, but its message does not show.";
    if (flying) {
      findings.push({
        rule: 1, level: "warning", screen: s.name,
        text: `Screen ${quote(s.name)} carries an attitude instrument but no CAS window or CAS marker,`
          + " so crew alerts do not show while it is up." + tail
          + " This breaks the installation requirement below.",
      });
    } else {
      findings.push({
        rule: 1, level: "warning", screen: s.name,
        text: `Screen ${quote(s.name)} has no CAS window or CAS marker. If it is ever up in front of you`
          + " in flight, crew alerts do not show while it is, and it breaks the installation"
          + " requirement below." + tail,
      });
    }
  }

  for (const s of screens) {
    for (const it of s.items) {
      if (!isSurface(it)) continue;
      const where = `The ${typeName(it.inst.type)} on screen ${quote(s.name)}${it.onTab ? " (on a tab page)" : ""}`;
      const off = disabledHow(it.inst);
      if (off) {
        findings.push({
          rule: 2, level: "warning", screen: s.name,
          text: `${where} ${off}. It does not count as showing crew alerts on that screen.`,
        });
      }
      const why = offGrid(it.inst, it.layout);
      if (!why) continue;
      findings.push({
        rule: 2, level: "warning", screen: s.name,
        text: `${where} ${why}, so it may be clipped off the glass. pyEfis does not detect this.`,
      });
    }
  }

  if (!casInUse) {
    findings.push({
      rule: 3, level: "note", screen: null,
      text: "This panel has no CAS window or CAS marker. Failures reach you only through each"
        + " instrument's own flags, and a fuel sender that never reports has no cue at all: its"
        + " bar just reads low. If fix-gateway runs crew alerting anyway, pyEfis shows CAS NOT SHOWN.",
    });
  }

  if (casInUse || lamps.length) {
    requirements.push("Installation requirement (pyEfis wiki, Designing Your Panel): on every screen"
      + " you fly from, a CAS marker or the CAS window must sit in your primary field of view, and so"
      + " must any annunciator lamp that can show a warning or a caution. A screen you fly from is any"
      + " screen up in front of you in flight, including one you switch to. Primary field of view:"
      + " within 15 degrees of your normal line of sight, about 8 inches from the centre of the basic T"
      + " with the panel 30 inches away. The configurator cannot check this: only you know where each"
      + " display is mounted.");
    if (lamps.length) {
      requirements.push("Annunciator lamps are placed on " + lamps.map((s) => quote(s.name)).join(", ")
        + ". Any of them that can show a warning or a caution falls under the requirement above.");
    }
    requirements.push("One sense only: pyEfis has no alert tone yet. Warnings and cautions are visual,"
      + " so where the marker, window or lamp sits is the only thing that draws your eye to a new alert.");
  }
  return { findings, requirements };
}

/** The advisory as plain-language lines, findings first. */
export function advisoryLines(adv) {
  const out = [];
  for (const f of list(adv && adv.findings)) out.push((f.level === "warning" ? "WARNING: " : "NOTE: ") + f.text);
  for (const r of list(adv && adv.requirements)) out.push(r);
  return out;
}

/**
 * The advisory as a YAML comment block for the head of the saved layout, so it
 * travels with the file to the device and to a download. "" when empty.
 */
export function advisoryComment(adv, width = 96) {
  const lines = advisoryLines(adv);
  if (!lines.length) return "";
  const out = ["# Layout advisory, written by the configurator when this layout was saved.",
    "# It advises; it does not block. Rules: makerplane-data configurator/public/layout_advisory.js"];
  for (const line of lines) {
    let cur = "#  -";
    for (const word of line.split(/\s+/).filter(Boolean)) {
      if (cur.length + 1 + word.length > width && cur.trim() !== "#" && cur !== "#  -") {
        out.push(cur); cur = "#   ";
      }
      cur += " " + word;
    }
    out.push(cur);
  }
  return out.join("\n") + "\n";
}
