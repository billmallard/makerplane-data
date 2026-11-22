<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Published holds from the NASR holding-pattern file (HPF)

Status: **source verified 2026-11-22** (HOLDFIX3, AER-6323). The fetch and the
schema-guard contract are in makerplane-data. The `holds` tables in the navaids
pack and the "As published" editor item are not built yet. See
[Rollout order](#rollout-order).

FAA CIFP has no enroute holding records. Holds in CIFP exist only as legs
inside procedures. NASR's holding-pattern file is the only FAA source for
charted enroute and arrival holds, so this note checks whether it is usable
and what it carries.

## Is HPF in the CSV subscription? Yes

- `https://nfdc.faa.gov/webContent/28DaySub/extra/<DD_Mon_YYYY>_HPF_CSV.zip`,
  the same URL family as the NAV, FIX and AWY zips the navaids pack already
  uses. Live for `29_Oct_2026` (AIRAC 2611) and `26_Nov_2026` (2612), checked
  2026-11-22. The 2611 zip is 932,481 bytes, sha256 `dc3e0ba1...8e9e00d`.
- Members: `HPF_BASE.csv` (one row per hold), `HPF_SPD_ALT.csv` (speed and
  altitude blocks), `HPF_CHRT.csv` (the charts that show the hold),
  `HPF_RMK.csv` (146 free-text remarks, all "CHART 210K ICON" and the like),
  plus the FAA's `HPF_CSV_DATA_STRUCTURE.csv` and layout PDF.
- The `HPF_CSV_DATA_STRUCTURE.csv` of 2611 and 2612 are byte-identical.
- The legacy fixed-width `HPF.txt` is still in the full 28-day subscription
  zip. It holds the same 15,467 holds and adds nothing that matters here (see
  leg length below).
- Holds are keyed by `(HP_NAME, HP_NO)`. `HP_NAME` is
  `FIX_NAME FIX_TYPE*STATE*ICAO_REGION` or `NAVAID_NAME FACILITY_TYPE*STATE`.

## Fields, against what the HOLDFIX1 editor needs

All counts are AIRAC 2611 and come from `scripts/hpf_survey.py` (command
below).

| Editor needs | HPF field | Fill and values (2611) |
|---|---|---|
| The hold point | `FIX_ID` + `ICAO_REGION_CODE`, or `NAV_ID` + `NAV_TYPE` + `STATE_CODE` + `COUNTRY_CODE` when the hold is at a navaid | 13,802 at a fix, 1,665 at a navaid. No coordinates: they come from FIX_BASE / NAV_BASE of the same cycle |
| Inbound course | `COURSE_INBOUND_DEG` (integer, magnetic) | Filled on all 15,467 |
| Direction from the fix | `HOLD_DIRECTION` | N, NE ... NW; 8 rows empty |
| Radial / course / bearing / track | `HOLD_DEG_OR_CRS` with `AZIMUTH` | `AZIMUTH` is RNAV 10,590, RAD 3,921, CRS 663, BRG 293. 32 rows have a non-numeric `HOLD_DEG_OR_CRS` |
| Turn | `TURN_DIRECTION` | R 12,083, L 3,384 |
| Leg length | `LEG_LENGTH_DIST` (integer NM, "leg length outbound DME") | 11,475 filled (1 to 25 NM, mostly 4), 3,992 empty |
| Leg time | **none** | see below |
| Altitudes | `HPF_SPD_ALT.ALTITUDE` per `SPEED_RANGE` | 17,807 rows; 1 to 5 rows per hold |
| Speed | `HPF_SPD_ALT.SPEED_RANGE` | a speed-class label: 200 (9,212), 230, 310, 265, 170/175, 175, 200/230, 210, 100, 280, ALL ... |
| Enroute or approach | `HPF_CHRT.CHARTING_TYPE_DESC` | IAP 9,996, MILITARY IAP, STAR 1,371, NOT REQUIRED, SPECIAL IAP, ENROUTE LOW 375, ENROUTE HIGH 275, SID ... 19 holds have no chart row |

### Leg time is never published

The legacy `HPF.txt` layout defines the outbound leg as two subfields,
`TIME(MINS)/DME(NM)`. In all 15,467 records of 2611 the time subfield is blank.
The CSV has no time column at all, and its `LEG_LENGTH_DIST` equals the legacy
DME subfield on every record (0 mismatches). So an empty `LEG_LENGTH_DIST`
means a timed hold whose time NASR does not carry.

CIFP of the same cycle gives a time on its timed hold legs. Where CIFP has a
time at a fix that also has an HPF hold, the HPF distance is empty in 1,730 of
1,757. CIFP's time is 1.0 minute in 1,752 of those 1,757 and 1.5 minutes in 5.
So the standard time (AIM 5-3-8: 1 minute at or below 14,000 ft MSL, 1.5 above)
is what applies to nearly every timed hold. A non-standard time printed on a
chart ("2 MIN") cannot be recovered from NASR, and nothing in the file marks
which holds have one.

### Altitudes are MIN/MAX in hundreds of feet

17,464 of 17,807 `ALTITUDE` values are `MIN/MAX`, for example `30/60`. The
layout does not give the unit. Hundreds of feet is the only reading that fits
the data: holds charted only on ENROUTE HIGH have a median minimum of 180
(FL180), and 95% are at or above 180. Holds charted only on ENROUTE LOW have
a median minimum of 40 (4,000 ft), and all are below 180.

The other 343 rows:
- 318 are `20-100` with a dash. All are in the Gulf offshore area (`*OG*`),
  speed class `170/175`.
- 25 have a single number. Several have speed and altitude swapped
  (`SPEED_RANGE` `20/60`, `ALTITUDE` `200`), and one is `085140`. These are
  source defects. The builder keeps the raw text and leaves the parsed
  altitudes empty for any value that is not `MIN/MAX`.

`SPEED_RANGE` is a label for a speed class, not a parsed number. It is
carried as text.

### Joins

15,329 of 15,467 holds join to a point of the same cycle: a fix by
`(FIX_ID, ICAO_REGION_CODE)` or a navaid by
`(NAV_ID, NAV_TYPE, STATE_CODE, COUNTRY_CODE)`. The 138 misses are all at ILS
components that are not in NAV_BASE (132 compass locators `LO`, 5 `LS`,
1 `LD`). These are approach holds.

The navaid key needs the state and country. In 2611, `(NAV_ID, NAV_TYPE)`
repeats across states for 33 NDBs, and six of them carry a hold (`AA`, `JN`,
`MF`, `PI`, `SB`, `VV`). On the pair alone, the `AA` NDB hold in Georgia would
be placed at the `AA` NDB in North Dakota. Found by AVIONICS on AER-6328. The
four-part key still repeats once (`MB` NDB, Michigan: two different beacons,
neither with a hold). The builder must treat a key that matches more than one
point as unresolved, never take the first match. `(FIX_ID, ICAO_REGION_CODE)`
is unique in FIX_BASE.

### The enroute and arrival subset

1,881 holds are charted on ENROUTE LOW, ENROUTE HIGH, STAR, AREA, SPECIAL
ENROUTE or MILITARY STAR. 1,362 have a distance and 519 are timed. They sit at
1,685 distinct points, and 173 of those points have more than one such hold.
The editor therefore has to offer a choice when a fix has several holds.

## Cross-check against CIFP (whole population)

For every distinct CIFP hold leg (HM, HA, HF) in 2611 at a fix that also has
an HPF hold, the closest HPF hold by inbound course was compared.

| Check | Result |
|---|---|
| Distinct CIFP holds | 8,945 (67 at a fix with no HPF hold) |
| Inbound course within 2 degrees | 8,873 of 8,878 |
| Turn direction agrees | 8,853 of 8,873 |
| CIFP distance, HPF distance the same | 6,547 |
| CIFP distance, HPF distance differs | 50 |
| CIFP distance, HPF empty | 519 |
| CIFP time 1.0 / 1.5 min, HPF empty | 1,726 / 4 |
| CIFP time, HPF has a distance | 27 |

CIFP and NASR come from the same agency, so this shows the two products are
consistent. It does not show that either is right: an error made upstream of
both passes this check. The 20 turn disagreements and 50 distance disagreements
are not yet differenced against the charts. The data is unverified until that is
done for a sample.

## Proposed pack contract

These are additive tables in the existing `navaids` pack, built by pyEfis
`tools/build_navaid_db.py` (the GPL builder, which makerplane-data runs by path
and does not vendor). There is no new pack kind, so the new-kind merge-order
rule does not apply. A device whose pyEfis does not read the tables ignores
them.

```
holds(hp_name, hp_no,             -- NASR key
      point_id, point_icao,       -- FIX_ID + ICAO_REGION_CODE, or NAV_ID (icao NULL)
      point_state, point_country, -- STATE_CODE, COUNTRY_CODE: part of the navaid key
      point_type,                 -- 'FIX' or the NAV_TYPE of a navaid hold point
      lat, lon,                   -- joined from fixes / navaids of the same build
      nav_id, nav_type,           -- the facility giving the radial or course
      hold_dir,                   -- N .. NW, or NULL
      hold_ref_deg, azimuth,      -- HOLD_DEG_OR_CRS (NULL if not numeric); RAD|CRS|BRG|RNAV
      inbound_crs,                -- COURSE_INBOUND_DEG, magnetic
      turn,                       -- 'L' or 'R'
      leg_nm,                     -- LEG_LENGTH_DIST; NULL = timed, time not published
      charts)                     -- CHARTING_TYPE_DESC values joined with '|'
hold_limits(hp_name, hp_no, speed_range, alt_raw,
            alt_min_ft, alt_max_ft)  -- feet; NULL unless alt_raw is MIN/MAX
```

The builder drops a hold whose point does not resolve, or resolves to more
than one point, and prints the count,
as it does for SP airways. It refuses to run when an HPF CSV is missing,
because a navaids pack built for holds that silently has none is the failure
to avoid.

## What the schema guard checks now

`packtools/schema_guard.py` (navaids contract), against the live download
before anything is built:

- `HPF_BASE`, `HPF_SPD_ALT` and `HPF_CHRT` structure must match
  `packtools/schemas/HPF_structure.csv` (seeded from 2611).
- Every HPF column listed in the contract above must be present.
- `TURN_DIRECTION` must be L or R, `AZIMUTH` one of RAD, CRS, BRG or RNAV, and
  `HOLD_DIRECTION` one of the eight directions. A new value changes what the
  course or turn means, so it stops the build.

There is no output floor for `holds` yet: a floor only makes sense once the
builder produces the table.

## Rollout order

1. **makerplane-data, this change.** Fetch the HPF zip with the other three and
   guard it. It must reach `main` first, because the scheduled `cyclical.yml`
   runs from `main` and checks out pyEfis `dev` for the builder. Until the
   builder reads HPF, the extra zip is downloaded and ignored (the builder
   finds its inputs by file name).
2. **pyEfis `tools/build_navaid_db.py`** (AVIONICS). Build `holds` and
   `hold_limits` as above. Tests: a two-hold fixture, a hold at a navaid, a
   timed hold (`leg_nm` NULL), each odd altitude shape, an unresolved point.
3. **makerplane-data follow-up.** Add an output floor for `holds` (50% of the
   first real build, about 7,600), and the table to `packtools/report.py`.
4. **pyEfis editor** (AVIONICS, HOLDFIX1's editor). Offer "As published" when
   the fix has a row in `holds`, with a choice when it has several. How a
   timed hold's leg is shown (the time is not published) and what the item
   looks like go to Bill.

## Reproduce

```bash
D=https://nfdc.faa.gov/webContent/28DaySub/extra/29_Oct_2026
for f in HPF FIX NAV; do curl -sO "${D}_${f}_CSV.zip"; done
curl -sO https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip  # FAACIFP18 inside
python3 scripts/hpf_survey.py --hpf 29_Oct_2026_HPF_CSV.zip \
    --fix 29_Oct_2026_FIX_CSV.zip --nav 29_Oct_2026_NAV_CSV.zip --cifp FAACIFP18
```

The legacy-layout check (time subfield blank on every record) reads `HPF.txt`
from `28DaySubscription_Effective_2026-10-29.zip`, the outbound-leg field at
columns 285-292 (`Layout_Data/hpf_rf.txt`).
