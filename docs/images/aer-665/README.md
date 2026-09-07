<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-665 tab_position / tab-bar colour smoke test

Live-browser render of `buildContainerTwin()` (`configurator/public/editor.html`,
head `d290e84`) for all four `tab_position` values, confirming the geometry and
colour-override code renders as intended before merge. This is the check the
PR's own `Your call` line asked for and could not do in-sandbox (headless
Chromium had no launchable browser there — see below).

- `tab_position_overview.png` — all four edges (`top`, `bottom`, `left`,
  `right`) side by side, alternating default chip colours and a `fg_color`
  (`#ffcc00`) / `bg_color` (`#1f6feb`) override, three tabs each ("Engine",
  "Nav", "Radios", "Engine" active).
- `tab_position_left.png` / `tab_position_right.png` — 4x-scale crops of the
  vertical edges, to check label rotation direction: West (`left`) reads
  bottom-to-top, East (`right`) reads top-to-bottom, per
  `tab_section/__init__.py`'s `_TAB_BAR_SHAPE` and the code comment above
  `buildContainerTwin()`. Confirmed correct in both crops.

## What this does NOT cover

Compares the twin against itself (does the code render what it says it
renders), not against a live pyEfis widget capture — there is no
`tools/render_instrument.py`-style reference render for `tab_section` yet.
Structural fidelity (edge placement, rotation direction, uniform colour
application with no `:selected` clause) is read directly from
`tab_section/__init__.py`'s `_layout_pages`/`_apply_tab_bar_style`, per the
code comments already in `buildContainerTwin()`.

## Regenerating

The sandbox this was produced in has no root and ships neither a launchable
Chromium (`playwright`'s cached browser is missing several shared libraries)
nor any fonts or fontconfig. All of the below is done as a normal user with
`apt-get download` (no `apt-get install`, no root) plus `dpkg-deb -x`, which
just unpacks a `.deb` into a directory:

```bash
# 1. Missing shared libs for headless Chromium + fontconfig (adjust to taste;
#    this is the exact set found missing via `ldd` on this build's
#    chrome-headless-shell). Needs an apt-lists cache writable by a non-root
#    user, hence the -o overrides:
mkdir -p /tmp/apt/lists/partial /tmp/apt/cache/archives/partial /tmp/debs
apt-get -o Dir::State::Lists=/tmp/apt/lists/ -o Dir::Cache=/tmp/apt/cache/ update
apt-get -o Dir::State::Lists=/tmp/apt/lists/ -o Dir::Cache=/tmp/apt/cache/ \
  -o Dir::Cache::Archives=/tmp/debs/ download \
  libnspr4 libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 libxcomposite1 \
  libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
  libatspi2.0-0t64 libxrender1 libdrm2 libxi6 \
  fontconfig-config fonts-dejavu-core libexpat1 libuuid1 libbz2-1.0

mkdir -p /tmp/libroot
for f in /tmp/debs/*.deb; do dpkg-deb -x "$f" /tmp/libroot; done

# 2. Point fontconfig at the extracted DejaVu fonts (pyEfis's own instrument
#    font -- see editor.html's `font_family` defaults):
cat > /tmp/fonts.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/tmp/libroot/usr/share/fonts</dir>
  <cachedir>/tmp/fontcache</cachedir>
</fontconfig>
EOF

# 3. Build the standalone proto page: extracts the REAL buildContainerTwin()/
#    tabStripGeom()/tabContentGeom() etc. out of editor.html by brace matching
#    (same technique as configurator/tools/twin_proto.mjs), into 4 panels, one
#    per tab_position, alternating colour overrides:
cd configurator
node tools/tab_position_proto.mjs --out /tmp/proto.html

# 4. Screenshot with Playwright's chrome-headless-shell, libs from step 1/2
#    (needs `npx playwright` to have pulled a chromium/chromium_headless_shell
#    build into ~/.cache/ms-playwright at least once, even though it can't
#    launch un-patched):
export LD_LIBRARY_PATH=/tmp/libroot/usr/lib/x86_64-linux-gnu
export FONTCONFIG_FILE=/tmp/fonts.conf
export CHROME_PATH=~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell
node -e '
import("playwright").then(async ({chromium}) => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH, args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: {width:1300,height:260}, deviceScaleFactor: 3 });
  await page.goto("file:///tmp/proto.html");
  await page.screenshot({ path: "/tmp/out.png", fullPage: true });
  await browser.close();
});
'
```
