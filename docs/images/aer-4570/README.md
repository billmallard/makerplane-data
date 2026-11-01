# AER-4570 (TV6) pack inspector screenshots

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Taken 2026-11-01 in headless Chromium (Playwright's `chrome-headless-shell`,
build 1234), against the live signed catalog and live packs.

- `row.png`: a finding-style locator,
  `?pack=obstacles-conus&cycle=261001&sha256=a0a9c662...&table=obstacles&row_id=7`.
  It shows all four trust steps passing and the one row. Python's `sqlite3`
  reads the same row at rowid 7 in the downloaded pack (`01-020475`, Gulf
  Shores AL).
- `tamper.png`: the same page with one byte of the navaids pack flipped in
  transit (`TAMPER=pack`). The sha256 check refuses it and nothing is opened.

`serve.py` stands in for navdata.aerocommons.org. It serves `site/` from the
checkout and proxies `manifest.json`, its signature and `packs/` from the live
origin. `TAMPER=pack` or `TAMPER=manifest` flips one byte in the middle of the
pack or the manifest. The manifest's pack URLs are absolute, so `drive.mjs`
routes them back to the local server.

```bash
python3 serve.py 8771 ../../../site &                      # real bytes
TAMPER=pack     python3 serve.py 8772 ../../../site &
TAMPER=manifest python3 serve.py 8773 ../../../site &
export CHROME=<path to chrome-headless-shell> PLAYWRIGHT_CORE=<path to playwright-core>
node drive.mjs http://127.0.0.1:8771 \
  "/inspect.html?pack=obstacles-conus&cycle=261001&sha256=a0a9c6626423218519e7264a644b1f817ef17f940f1a4d76d3cdf1e2c2bbf190&table=obstacles&row_id=7" row.png
node drive.mjs http://127.0.0.1:8772 "/inspect.html?pack=navaids-conus&cycle=2612" tamper.png
node drive.mjs http://127.0.0.1:8773 "/inspect.html?pack=navaids-conus&cycle=2612"   # signature refused
node drive.mjs http://127.0.0.1:8771 "/inspect.html?pack=navaids-conus&cycle=2612" "" "DELETE FROM navaids"
```

The last command prints `attempt to write a readonly database`.
