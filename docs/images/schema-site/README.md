<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# /schema/ preview renders (SCH1)

Rendered 2026-10-31 from a local mirror of the live catalog: `manifest.json`
and its signature from navdata.aerocommons.org, the smallest pack of each
SQLite kind plus `water-na 2026q2r6`, and every terrain pack read over HTTP
ranges.

```bash
mkdir -p mirror/packs
curl -fsS -o mirror/manifest.json         https://navdata.aerocommons.org/manifest.json
curl -fsS -o mirror/manifest.json.minisig https://navdata.aerocommons.org/manifest.json.minisig
for f in airports-canada-2026.06 navaids-conus-2612 airports-conus-2612 \
         procedures-conus-2611 obstacles-conus-261001 highways-conus-2026q3r1 \
         water-na-2026q2r6; do
  curl -fsS -o mirror/packs/$f.pack https://navdata.aerocommons.org/packs/$f.pack
done
packtool schema-site --from mirror --out preview --bootstrap
for p in index terrain navdata; do
  chrome-headless-shell --no-sandbox --hide-scrollbars --window-size=1200,2400 \
    --screenshot=$p.png file://$PWD/preview/schema/$p.html
done
```

The screenshots are cropped to the 1200x2400 window, so the bottom of a long
page is cut off.
