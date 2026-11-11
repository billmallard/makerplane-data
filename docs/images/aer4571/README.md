# AER-4571 (TV7) evidence: per-state diff maps

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

- `diffmap-hwy.png`: the live `highways-conus` 2026q3r1 against 2026q2r2,
  OSM-002 (feature counts). Every state grew: q3r1 added road classes.
- `diffmap-noca.png`: the same 2026q3r1 pack with every feature binned to
  California deleted (381,917 rows), against 2026q2r2. A stand-in for the
  June highways pack that shipped with no California roads
  (makerplane-data#17). OSM-001, which bins by region, still passes on it
  (us-west +126.6%); OSM-002 and OSM-006 fail with US-CA at -100%.

Both are screenshots (1040 x 700, scale 1) of `packtool diffmap` output in
chrome-headless-shell. Map data (c) OpenStreetMap contributors, ODbL.
Boundaries: Natural Earth (public domain).

To regenerate:

```bash
base=https://navdata.aerocommons.org/packs
curl -A makerplane-data -O $base/highways-conus-2026q2r2.pack
curl -A makerplane-data -O $base/highways-conus-2026q3r1.pack
packtool report highways-conus-2026q2r2.pack --no-device -o q2r2.json
packtool report highways-conus-2026q3r1.pack --no-device --previous q2r2.json -o q3r1.json
packtool diffmap q3r1.json q2r2.json -o diffmap-hwy.html

# The California stand-in: delete the rows packtools.admin1 bins to US-CA.
cp highways-conus-2026q3r1.pack noca.pack
python3 - <<'PY'
import functools, sqlite3
from packtools import admin1
C = admin1.CELL_DEG
@functools.lru_cache(maxsize=None)
def st(cy, cx): return admin1.locate_near((cy + .5) * C - 90, (cx + .5) * C - 180)
con = sqlite3.connect("noca.pack"); con.create_function("st", 2, st)
con.execute(f"DELETE FROM highway_lines WHERE min_lon < -113 AND min_lat < 43 AND "
            f"st(CAST(((min_lat+max_lat)/2.0+90)/{C} AS INTEGER), "
            f"CAST(((min_lon+max_lon)/2.0+180)/{C} AS INTEGER)) = 'US-CA'")
con.execute("DELETE FROM highway_rtree WHERE id NOT IN (SELECT id FROM highway_lines)")
con.execute("UPDATE pack_meta SET value = '2026q3r1-noca-demo' WHERE key = 'cycle'")
con.commit()
PY
packtool report noca.pack --no-device --previous q2r2.json -o noca.json
packtool diffmap noca.json q2r2.json -o diffmap-noca.html

chrome-headless-shell --no-sandbox --hide-scrollbars --window-size=1040,700 \
  --screenshot=diffmap-noca.png file://$PWD/diffmap-noca.html
```
