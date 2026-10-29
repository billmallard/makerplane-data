<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# AER-4150 (TV5) report-page renders

Rendered 2026-10-29 from the fixture records in `tests/fixtures/report_site/`
(written by `packtool report` on seven published packs, run locally with no
pyEfis checkout and no upstream inputs, so ACC-001, PACK-004 and the SG layers
are `not-run`). They show the pages, not the state of the live site.

- `catalog.png` -- `index.html` with the summary line per edition (1280x1000)
- `report-procedures-2611.png` -- one edition's page (1280x4200)
- `history-procedures.png` -- one pack's history page (1280x1500)
- `check.png` -- the check-it-yourself page (1280x1900)

To regenerate:

```bash
packtool report-site --from tests/fixtures/report_site --out /tmp/preview
python -m http.server 8777 --bind 127.0.0.1 -d /tmp/preview &
chrome-headless-shell --headless --no-sandbox --hide-scrollbars \
  --window-size=1280,4200 --screenshot=report.png \
  http://127.0.0.1:8777/reports/procedures-conus/2611/report.html
```
