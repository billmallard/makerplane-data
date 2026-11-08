# AER-5147 (P3e) Ownship identity card

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Taken 2026-11-08 in headless Chromium (Playwright's `chrome-headless-shell`,
build 1234), from `configurator/public/aircraft.html` in demo mode
(`?demo=1`, nothing saved) with an empty parameter schema stubbed in, so only
the Configuration and Ownship identity cards draw. Each crop is the card plus
the page's error line under it, at 2x device scale.

- `1-derived.png`: `n825v` typed, no override. The page derives `AB4549`,
  which is N825V's MODE S CODE HEX in the FAA registry of 2026-11-06.
- `2-override.png`: N825V plus override `a1b2c3`. The override is used and
  the disagreement is flagged as a warning (Save stays enabled).
- `3-invalid.png`: `N0123` (leading zero). A hard error; Save is disabled,
  and the server refuses the same profile with 422.
- `4-non-us.png`: `C-GABC`, no override. Nothing is derived (only US
  N-numbers have a public mapping), and the page asks for the hex address.

```bash
export CHROME=<path to chrome-headless-shell> PLAYWRIGHT_CORE=<path to playwright-core>
node shot.mjs ../../../configurator .     # from this directory
```

`shot.mjs` serves the page and the stub schema through Playwright routes; no
server is needed. It prints the derived-address line, the error line and
whether Save is disabled for each state.
