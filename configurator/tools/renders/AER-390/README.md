# AER-390 -- HSI nav-source tab twin, reconciled against AER-389

Final reconciliation of the configurator's HSI nav-source tab twin
(`navTab()` in `public/editor.html`) against AER-389's shipped widget
(pyEfis#143, merged to `dev` at `ca1bc62`). AER-389 is the source of truth
per the workspace fidelity rule; this records what was checked and what
changed as a result.

## What was reconciled

The draft PR (#49) flagged its horizontal padding / text-advance constants
as the twin's own estimate, not yet checked against AER-389's shipped
numbers (the twin has no real font-metrics API, unlike the Qt widget's
`QFontMetrics.horizontalAdvance`). With AER-389 merged, its exact formula
(`src/pyefis/instruments/hsi/__init__.py::_draw_source_tab`) is:

```python
rr = self.fontSize * helpers.READOUT_RADIUS_RATIO   # 0.35
th = ph - 2.0 * rr
tab_r = min(rr, th / 2.0)
pad = self.fontSize * 0.35                            # == rr, same token
tab_w = fm.horizontalAdvance(label) + 2.0 * pad
```

The load-bearing fact: **`pad` and `rr` are the same value** (both
`fontSize * 0.35`) -- not a coincidence, both keyed off
`READOUT_RADIUS_RATIO`. The twin's horizontal padding (`hpad`) was `1.6`,
independent of `PANEL_RR` (`1.4`, the twin's `rr` equivalent, matching
`segPanel`'s own `rx`). Changed `hpad` to `rr` directly, so the identity
holds in the twin the same way it holds in the widget, algebraically, not
by tuning two magic numbers to look similar.

The per-character width estimate (`label.length * 4.2`) was measured
against the actual rendered glyph advance (see Method) and found to
under-count -- the previous constant left "GPS" filling ~95% of the tab's
width, almost no visible padding, versus ~70% in the shipped widget render.
Bumped `4.2 -> 4.6` to close most of that gap (rendered ratio ~80% with the
fonts available in this sandbox -- see caveat below). This is still an
estimate for the twin's abstract per-character quantity (no font-metrics
API here either); the algebraic `hpad = rr` fix is the actual reconciled
constant, the coefficient bump is a best-effort improvement given the
tools available.

Also dropped `font-weight="600"` from the tab's text element: AER-389's
`_draw_source_tab` does not bold its `QFont` (only the panel's segment
*labels* -- "HDG"/"MAG"/"CRS" -- are bold in the widget), so the twin's tab
text should be regular weight to match, independent of `segPanel`'s own
bold labels which are unrelated to this reconciliation.

`PANEL_RR = 1.4` (the twin's `rr`), the flush right edge, the rounded-left/
square-right path construction, and the fill/text colour logic were already
correct against AER-389's geometry and needed no change.

## Renders

- `compare_gps.png` -- AER-389's shipped `gps_tab_fp05.png` (after/ground
  panel) vs the reconciled twin, both GPS/magenta.
- `compare_vloc.png` -- AER-389's shipped `vloc1_tab_fp05.png` (after/ground
  panel, "VLOC1" is the widest source string) vs the reconciled twin at the
  VLOC source colour. The twin's preview label is fixed at "GPS" text
  regardless of source colour (pre-existing design, unchanged by AER-390 --
  the twin has no live NAVSRC to cycle in a static preview); only the fill
  colour flips to green here, which is what the comparison is checking.

## Method (and its limits)

There is no headless browser in the sandbox this reconciliation ran in
(the usual `tools/twin_proto.mjs` + headless-browser-screenshot path
documented in that script requires msedge/chromium, both unavailable here).
Substituted `@resvg/resvg-js` (an npm-installable SVG rasterizer, not a
project dependency -- installed ad hoc in a scratch directory) to rasterize
the *actual* `buildHSI()` output extracted from `editor.html` via the same
brace-matching helper the test suite uses (`test/support/extract.mjs`), so
what's rendered is the real function, not a reimplementation.

```bash
# ad hoc, not added to package.json:
npm install @resvg/resvg-js
# then extract buildHSI (+ its dependencies) the same way
# test/hsi_nav_tab.test.mjs does, feed the returned SVG string to Resvg,
# rasterize with DejaVu Sans substituted for whatever font the real
# browser preview uses (font family isn't pinned in editor.html; it
# inherits the page's CSS font stack).
```

**Caveat:** DejaVu Sans's glyph metrics are not identical to whatever sans-
serif the configurator's actual browser render uses, so the measured ~80%
text-to-tab-width ratio (vs the widget's ~70%, measured the same way from
AER-389's own PNG evidence) is directionally reconciled, not pixel-exact.
The `hpad = rr` fix is exact (an algebraic identity, not a measurement);
the coefficient bump is the best available given the tooling.

Widget-side source images (`widget_gps_fp05.png` / `widget_vloc1_fp05.png`)
came from `docs/images/aer389_hsi_nav_source_tab/` on the merged AER-389
branch in `billmallard/pyEfis` -- not duplicated into this repo, just read
from for the crop/compare step above.
