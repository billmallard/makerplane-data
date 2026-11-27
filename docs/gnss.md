<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The `gnss` pack: GPS almanac and NANUs for RAIM prediction

Status: **built and installed; not yet published** (RAIM1, AER-6755). The
builder, the JSON pack container and the device install are on `dev`. Nothing
writes a `gnss` pack to the live manifest yet: `build-pack --upload` refuses the
kind until the publish gate has gnss checks, and the first publish needs Bill's
approval after the kind reaches `main`.

## What it is for

RAIM prediction (fix-gateway `fixgw/raim/`, RAIM2; the flight-plan engine's
`FPLCMD RAIM`, RAIM3) has to place the GPS satellites at a future time and
leave out the ones the operators have said will be unusable. That needs two
inputs the receiver does not supply: a current **almanac** and the active
**NANUs** (Notice Advisory to Navstar Users). Both are free federal data from
the US Coast Guard Navigation Center (NAVCEN). Design: maos-workspace
`briefs/flight_plan_plan.md` section 3.3.2.

## Sources

| Data | URL | Notes |
|---|---|---|
| YUMA almanac | `https://navcen.uscg.gov/sites/default/files/gps/almanac/current_yuma.alm` | One record per PRN; the week is mod 1024 |
| Newest NANU | `https://navcen.uscg.gov/sites/default/files/gps/nanu/current_nanu.nnu` | One message only |
| Any NANU | `https://navcen.uscg.gov/sites/default/files/gps/nanu/<YYYY>/<YYYYNNN>.nnu` | Numbered densely from `<YYYY>001` |
| Each PRN's latest NANU | `https://navcen.uscg.gov/gps-constellation` | Only the `.nnu` links are read |

Use the `navcen.uscg.gov` host. `www.navcen.uscg.gov` answers some of these
paths with a 301 and others with a 403. NAVCEN data is a work of the US
federal government (17 U.S.C. 105). It carries no copyright in the US, and the
pack records it as `LicenseRef-US-Government-Work`.

The SEM almanac (`current_sem.al3`) has the same contents. The builder reads
YUMA only.

## How the NANUs are chosen

Walking back from the newest NANU by number, the builder stops at the first NANU
whose date-time group is more than 45 days old (`LOOKBACK_DAYS`). In January
the walk crosses into the previous year, and a binary search finds that year's
last number. A long-lived state that is older than the walk, such as an
UNUSUFN or a DECOM, comes from the constellation page's per-PRN links.

Superseding NANUs are resolved **by number, not by date-time group**. NAVCEN
has published an UNUSABLE (2026104, DTG 220629Z) whose DTG comes before the
UNUSUFN it closes (2026103, DTG 220937Z).

| Types | Effect on the outage list |
|---|---|
| FCSTDV, FCSTMX, FCSTUUFN, UNUSUFN, UNUNOREF, DECOM | adds its own window (no stop time if until further notice) |
| FCSTSUMM, FCSTRESCD, FCSTEXTD, UNUSABLE | replaces the window of the NANU it references |
| FCSTCANC | removes the window of the NANU it references |
| USABINIT | ends an open DECOM window on the same PRN with a different SVN (PRN reassigned) |
| GENERAL, LAUNCH, LEAPSEC | no window; the text is carried in `general` |

The type list is NAVCEN's "NANU Abbreviations and Descriptions" page, read
2026-11-27. A type outside it refuses the build. **GENERAL corrections are not
applied.** They are free text (2026001 corrects the dates of 2025090), so they
reach a person through `general` and never change `outages`.

## Fail-closed checks (the build, not the device)

A refused build publishes nothing. The last good pack keeps serving until it
expires. A build is refused for:

- an almanac record that has a missing, repeated or unknown field, or a value
  outside the range of a GPS orbit (for example sqrt(A) outside
  5100..5200 m^1/2, eccentricity above 0.03), a duplicate PRN, or records that
  disagree on the week or the TOA;
- fewer than 24 satellites;
- an almanac TOA more than 14 days before the build (NAVCEN's file is stale);
- a NANU whose header and NUMBER field disagree, whose JDAY does not match its
  calendar date, whose STOP is before its START, or whose type replaces or
  cancels another NANU without naming a reference;
- a gap in the NANU number sequence during the walk.

## The document (`mp-gnss/1`)

The pack is one JSON object, written with sorted keys and fixed separators so
the same inputs give the same bytes. `pack_meta` is a top-level key
(`packtools.packmeta.embed_json`).

```
schema        "mp-gnss/1"
generated     build time, UTC
almanac       format "yuma", week (full), week_mod1024, toa_s,
              toa_gps (ISO; GPS time, which is ahead of UTC by the leap seconds, 18 s since 2017)
satellites[]  prn, health, e, toa_s, i0, omega_dot, sqrt_a, omega0, w, m0, af0, af1
              (YUMA units: rad, rad/s, m^1/2, s, s/s)
nanu          latest, latest_dtg, considered[] (every NANU number read), lookback_days
outages[]     prn, svn, type, start, stop (null = until further notice), nanu, ref
general[]     nanu, type, dtg, text
sources[]     url, sha256, retrieved
license, license_url, attribution
pack_meta     id, kind "gnss", cycle, effective, expires, ...
```

`outages` keeps windows that are still open or that ended within the lookback.
A consumer filters by its own time window. A satellite with `health` other than
0 is unhealthy in the almanac whether or not a NANU names it.

## Currency

The cycle is the build date (`YYMMDD`). The pack expires 14 days later
(`gnss.VALID_DAYS`). The updater shows any pack as EXPIRES (white) within 7 days
of its expiry, and as EXPIRED (amber) after it. So a device that has gone a
week without a fresh pack shows white, and one that has gone two weeks shows
amber. It keeps using the pack either way. This is pack currency only. The
age limit for a RAIM prediction is RAIM3's setting (90 days by default, from
the GNX 375 guide).

## On the device

`pyefis-data` tracks the kind by default (`DEFAULT_TRACK_KINDS`) and installs
it like a single-file sqlite pack: verify the manifest signature, check the
sha256, move the file into `<root>/gnss/<cycle>/`, then flip the `current`
symlink. The path for fix-gateway to read is:

```
<root>/gnss/current/gnss.json          (/data/makerplane-data/gnss/current/gnss.json on the bench Pi)
```

fix-gateway has no pack reader yet (`briefs/architecture_audit.md` 2.2).
RAIM3 adds it, with this path as the default of its own config key.
`config_pull` injects nothing for this kind, because no fix-gateway setting
exists yet for it to fill in.

## Building one

```bash
packtool make-gnss --out work/gnss/gnss.json                         # live NAVCEN
packtool make-gnss --record tests/fixtures/gnss/<dir> --out ...      # live, and record every response
packtool make-gnss --fixture tests/fixtures/gnss/navcen-20261127 \
    --now 2026-11-27T16:00:00Z --out work/gnss/gnss.json             # replay a recording
packtool build-pack work/gnss/gnss.json --id gnss-global --kind gnss --sec keys/minisign.sec
```

`build-pack` sets the cycle, the expiry, the attribution and the licence for
this kind.

## Not done yet (RAIM1b)

- Publish-gate checks for a JSON pack. The gate's checks are written for sqlite
  and zip packs, and `report.previous_from` reads any JSON file as a test
  record.
- A scheduled daily workflow that runs `make-gnss`, then the gate, then the
  publish.
- The first publish, after the kind reaches `main` and Bill approves it.
