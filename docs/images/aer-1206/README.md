<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AER-1206 nested-instrument twin smoke test

Live-browser render of `buildContainerTwin()`/`buildInstrumentTwin()`
(`configurator/public/editor.html`) showing a real nested instrument -- a
`value_text` dropped into the "Engine" tab of a `tab_section` -- rendered
inside the container at full fidelity, for `tab_position=top` and
`tab_position=left`. This is the half of Phase B (AER-345) that
[docs/images/aer-665](../aer-665/README.md) explicitly did not cover: that
smoke test stubs `buildInstrumentTwin()` to a no-op so it stays a pure
geometry/colour check (its own header comment says so). This one runs the real
per-type dispatch, unstubbed, so the nested `value_text` is drawn by the same
code path a top-level instrument gets.

- `tab_section_nested.png` — two panels side by side: `top` and `left`
  `tab_position`, each with the "Engine" tab active and a `value_text`
  (`dbkey: OILT`, `fg_color: #7ee787`) nested inside, at the position and
  green colour override a real drop + property edit would produce. The small
  green square on each instrument is the real resize handle `buildContainerTwin`
  attaches to every nested child -- not a screenshot artifact.

## What this does and does not prove

**Proves:** the nested instrument renders inside the container's own
coordinate space (not the screen's), clipped to the tab's content area net of
whichever edge the tab bar occupies, using the *exact* `buildInstrumentTwin()`
dispatch a top-level instrument gets -- same function, called recursively,
per `docs/tab_section_configurator.md` §4f. The `state.schema.instruments`
map driving the `tab_section`/`value_text` `meta` lookups
(`meta.containers`, `dbkey` etc.) is fetched from the **live**
`pyefis-dev.aerocommons.org/assets/editor/schema.json` at generation time
(`tools/tab_section_nested_proto.mjs` does this with a plain `fetch()`, no
hand-written fixture) -- confirmed `schema_version: 3`,
`generated_from: pyefis.screens.screenbuilder_factory`.

**Does not prove:** the actual `canvas.addEventListener("drop", ...)` mouse
interaction (this proto constructs the post-drop instrument tree directly,
the same shape `instrumentForDrop()` + the container-scoped drop-target branch
would have produced -- see `editor.html`'s drop handler around the
`selMeta?.containers?.length` check). The drop-target's *coordinate math*
(tab-local, net of the tab bar) has its own non-visual regression test,
`configurator/test/tab_section_roundtrip.test.mjs`, which is a permanent CI
gate; this PNG is a one-time visual sanity check for the same code paths, not
a substitute for it.

## Regenerating

Same no-root headless-Chromium recipe as
[docs/images/aer-665](../aer-665/README.md), reproduced here since a few more
packages are needed for `chrome-headless-shell` on a bare Debian trixie
sandbox (the exact set found missing via iterating on
`error while loading shared libraries` until launch succeeded):

```bash
mkdir -p /tmp/qtlibs && cd /tmp/qtlibs
# Packages beyond the AER-665 list: libatk1.0-0 + libatk-bridge2.0-0 +
# libatspi2.0-0 pinned to the SAME source version (2.46.0-5) -- mixing a
# newer libatk-bridge2.0-0 against an older libatk1.0-0 fails at load with
# "undefined symbol: atk_object_get_help_text" even though both satisfy the
# nominal SONAME. libcairo2, libcups2, libgtk-3-0, libpango-1.0-0, libudev1,
# libvulkan1, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxrandr2,
# libexpat1, libasound2, libxrender1, libdrm2, libxi6, libxres1 round out
# chrome-headless-shell's `deb.deps` manifest (shipped alongside the binary
# in ~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/).
for spec in \
  "a/at-spi2-core libatk1.0-0_2.46.0-5_amd64.deb" \
  "a/at-spi2-core libatk-bridge2.0-0_2.46.0-5_amd64.deb" \
  "a/at-spi2-core libatspi2.0-0_2.46.0-5_amd64.deb" \
  "c/cairo libcairo2" "c/cups libcups2" "m/mesa libgbm1" \
  "g/gtk+3.0 libgtk-3-0" "p/pango1.0 libpango-1.0-0" "s/systemd libudev1" \
  "v/vulkan-loader libvulkan1" "libx/libxcomposite libxcomposite1" \
  "libx/libxdamage libxdamage1" "libx/libxext libxext6" \
  "libx/libxfixes libxfixes3" "libx/libxrandr libxrandr2" "e/expat libexpat1" \
  "a/alsa-lib libasound2" "libx/libxrender libxrender1" "libd/libdrm libdrm2" \
  "libx/libxi libxi6" "libx/libxres libxres1" \
  "n/nss libnss3" "n/nspr libnspr4"
do
  set -- $spec; dir=$1; shift
  file=$(curl -s "https://deb.debian.org/debian/pool/main/$dir/" | grep -oE "$1[^\"]*amd64\.deb" | sort -V | tail -1 || echo "$1")
  curl -sL -o "$(echo "$file" | cut -d_ -f1).deb" "https://deb.debian.org/debian/pool/main/$dir/$file"
done
for f in *.deb; do dpkg-deb -x "$f" extract; done   # ignore the doc-dir chmod warning, .so files still land

# Fonts (the box otherwise renders blank text):
curl -sL -o fonts-liberation2.deb \
  "https://deb.debian.org/debian/pool/main/f/fonts-liberation2/fonts-liberation2_2.1.5-1_all.deb"
dpkg-deb -x fonts-liberation2.deb extract
mkdir -p /tmp/fontconfig/cache
cat > /tmp/fontconfig/fonts.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>/tmp/qtlibs/extract/usr/share/fonts</dir>
  <cachedir>/tmp/fontconfig/cache</cachedir>
</fontconfig>
EOF

cd configurator
node tools/tab_section_nested_proto.mjs --out /tmp/nested_proto.html

export LD_LIBRARY_PATH=/tmp/qtlibs/extract/usr/lib/x86_64-linux-gnu
export FONTCONFIG_FILE=/tmp/fontconfig/fonts.conf
CHROME=~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell
$CHROME --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=900,340 --force-device-scale-factor=3 \
  --screenshot=docs/images/aer-1206/tab_section_nested.png \
  file:///tmp/nested_proto.html
```
