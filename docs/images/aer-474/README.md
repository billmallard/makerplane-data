# AER-474 evidence

`nav_source_tab_twin_before_after.png` -- before/after of the configurator's
nav-source tab twin (AER-474, lockstep with AER-473/pyEfis#147+#148).

This sandbox has no libGL/Chromium GUI stack to screenshot the live
configurator page (same limitation AER-473 hit rendering the widget side), so
the image is a synthetic PIL reproduction, not a live browser screenshot. The
colours and widths drawn ARE the real output of the ported
`darkenToContrast()`/`SOURCE_TAB_LABELS` code in
`configurator/public/editor.html`, evaluated with Node -- not hand-picked.

Regenerate from the repo root:

```bash
node docs/images/aer-474/compute_values.mjs
python3 docs/images/aer-474/render_evidence.py
```
