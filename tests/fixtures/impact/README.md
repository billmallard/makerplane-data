<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# PR impact report fixtures (TV9, AER-4572)

`roads_states.json` is the highways input for `packtools.impact`. It is
**synthetic**: no OpenStreetMap data is used.

- `states` has one entry per Geofabrik slug in `make_roads.CONUS_STATES`,
  giving an approximate state centre as `[lat, lon]`. California appears
  as `california/norcal` and `california/socal`, matching the real list.
- `roads` is the road set written for every state, as `[fclass, count]`
  pairs. `residential` and `track` are included so the builder's class
  filter has something to drop.
- `half_width_deg` is the half-size of the box each state's roads sit in.
  The report assigns a road to the nearest state centre within three
  half-widths. The closest pair of centres (DC and Maryland) is 0.46
  degrees apart, so no road can be counted for the wrong state.

`scripts/impact_build.py` turns this file into one Geofabrik-shaped
`*-free.shp.zip` per state, using pyshp. The output is deterministic.

When `CONUS_STATES` gains a slug, add it here too: `make_roads` asks for
every slug, and a slug this file lacks gets the HTML page Geofabrik serves
for an unknown slug, so the build fails.
