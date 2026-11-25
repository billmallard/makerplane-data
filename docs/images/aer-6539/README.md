<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# LAYADV1 (AER-6539): the layout advisory in the editor

Captured from the real `configurator/public/editor.html` in headless Chromium,
with `/api/*` answered locally and `/assets/editor/*` proxied to the dev site
(whose `schema.json` carries `cas_window` and `cas_marker`). The Code pane is on,
so the header the save writes shows under the canvas.

- `editor_cas_off_pfd.png`, `advisory_cas_off_pfd.png`: the CAS window on its
  own RADIO screen. Rule 1 warns on PFD (attitude instrument, HSI covers HDG
  and NAV) and, conditionally, on EMS.
- `editor_no_cas_vfr.png`, `advisory_no_cas_vfr.png`: a VFR panel with no CAS
  at all. Rule 3's note only, no warning. Since Bill's ruling on AER-6553 the
  older amber per-screen CAS banner (AER-6348) no longer shows on such a panel;
  it shows only when a CAS window or marker is placed somewhere.

Regenerate (from `configurator/`; the library and font paths are this
container's, see the script header):

```bash
LD_LIBRARY_PATH=/paperclip/.cache/chromelibs/root/usr/lib/x86_64-linux-gnu \
FONTCONFIG_FILE=/path/to/fonts.conf \
PW=/app/node_modules/.pnpm/playwright-core@1.62.1/node_modules/playwright-core \
CHROME=/paperclip/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell \
node tools/layout_advisory_capture.mjs ../docs/images/aer-6539
```
