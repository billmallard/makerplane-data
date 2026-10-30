# FAA d-TPP approach plates (PA11)

<!-- SPDX-License-Identifier: CC-BY-4.0 -->

How the FAA's d-TPP approach-plate PDFs get from the AeroNav server into
per-region signed packs, via the same pipeline as every other pack kind.
Answers `billmallard/makerplane-data#87` (PA11 of the procedures & airways
epic, `makerplane/briefs/procedures_and_airways_plan.md` §5) -- built on
PA10's decision brief (`docs/dtpp_plates_spike.md`), which Bill ruled on
2026-09-20. Read that document first; this one covers what PA10 explicitly
left to PA11 (§6): the build, the georeferencing derivation proven
end-to-end (not just designed), and what is genuinely not there yet.

**Status: built, tested, not yet published.** `plates` is registered in
`packtools/packmeta.py`'s `KINDS` and nothing else -- it is not in
`packtools/sources.py`'s `SOURCES`, so `packtool-run-cyclical` (the daily
cron) cannot reach it, and no `plates.yml` GitHub Actions workflow exists
yet. Per the repo rule (`CLAUDE.md` "Deploy / publish notes"), a new kind
must merge to `dev` -- the branch devices actually track -- before its
first R2 publish; that merge, and the separate decision to wire up
automated publishing, are both still ahead of this document.

## Pack format

One sqlite pack per `regions.yaml` region (`plates-us-west`,
`plates-us-east`, ...), like navdata/water/obstacles -- not the zip
container terrain/highways use. Three tables:

- **`plate_files`** -- every distinct PDF this region references, exactly
  once (`pdf_name` primary key, sha256, size, raw bytes as a BLOB). A
  shared regional booklet (see below) lands here once no matter how many
  airports reference it.
- **`plates`** -- one row per d-TPP catalog record (airport, chart type,
  chart name, amendment number/date, `pdf_name`); several rows can point at
  the same `plate_files` entry.
- **`plate_georef`** -- one row per PDF this build *attempted* to
  georeference (IAP charts only this pass -- see below), whether or not the
  attempt succeeded. `status` is one of `ok` / `insufficient_control_points`
  / `degenerate`; `ok` carries a measured `residual_nm` and the fitted
  transform. A PDF with **no** row here was never attempted (a different
  chart type this pass doesn't try, or georeferencing was skipped entirely
  for the whole build) -- that is a distinct, visible state from "attempted
  and failed."

Distinct-file storage (each PDF once, referenced by many `plates` rows) is
what actually delivers PA10's ~4.7 GB/cycle national measurement (§1.1,
§1.2's "distinct-file basis"): d-TPP is not one-PDF-per-airport. `MIN`
(takeoff/alternate minimums) in particular ships as ~74 shared regional
booklets covering up to ~200 airports each; a naive per-airport bundle
would duplicate those into every airport's package and inflate total bytes
toward PA10's "duplicated-basis upper bound" instead. `tests/test_dtpp.py`
and `tests/test_build_plates.py` pin this with two real examples pulled
live from cycle 2609: `AKTO.PDF` (shared by ADK/EIL) and `AKRAD.PDF`
(shared by EDF/FBK).

## Georeferencing -- proven end to end, with an honest result

PA10 designed the method (`docs/dtpp_plates_spike.md` §2) but explicitly
did not prove it end-to-end (§6: "No end-to-end georeferencing transform
was computed or validated against ground truth"). This pass does: fit a 2D
similarity transform (uniform scale + rotation + translation) from a
procedure's CIFP-parsed fix coordinates (PA1's already-built `procedures`
pack) to the same fix idents' text-layer positions on the plate's own PDF
page, hold at least one match back as an independent check, and measure the
reprojection error in nautical miles (`packtools/build/georef.py`).

**The real, measured result, not a synthetic one:** run against the live
ADK/PADK "ILS Y OR LOC Y RWY 23" plate (`01244IYLY23.PDF`, cycle 2609) and
its own real CIFP fix coordinates, only 2 of the 7 plan-view-relevant fixes
(`SALSE`, `LONOK`) print exactly once on the page -- `GIDKE` prints 3
times, `TICCU` 4, `GUISE` and `COMAT` twice each (plan view + profile +
missed-approach text block, confirmed by direct inspection, matching PA10's
"Label ambiguity" finding). This derivation deliberately uses **only**
unambiguous (single-occurrence) fixes, for both the fit and the held-out
check -- disambiguating a repeat by which occurrence best agrees with the
very transform being validated is circular, not independent verification.
2 unambiguous points is enough to fit a transform but not to also hold one
back, so **this real plate ships with no geo tag** under this pass's
method. `tests/test_georef.py` and `tests/test_build_plates.py` pin this
exact outcome as a regression, not a TODO.

This is the common case today, not a rare edge case -- most IAP plates
likely have too few unambiguous fixes to clear the bar (2 to fit + >=1
independent check = 3 minimum). **What is not attempted this pass:**
page-region clustering (using the plan-view's spatial boundaries, or the
plate's own vector graphics/symbols, to pick the plan-view occurrence of a
repeated fix ident) -- PA10 flagged this as needed and it is real future
work, not started here. Doing meaningfully better than "ships with no geo
tag most of the time" needs that clustering; this pass proves the pipe
works and ships the honest, conservative result rather than a method that
would look better on paper by disambiguating against itself.

Guardrail 5 ("no ownship on a plate that has not been georeferenced to a
measured error budget") is satisfied by construction: a plate either has a
`plate_georef` row with `status='ok'` and a real `residual_nm`, or it has
no usable geo tag at all. There is no third state where a transform exists
without a measured residual to gate on.

Georeferencing is attempted for `chart_code == "IAP"` records only --
those are what ownship gates on, they are one-PDF-per-procedure (unlike the
shared MIN/STR/HOT/LAH booklets, which are also multi-page-per-airport in
ways this pass has no page-to-airport mapping for), and the control-fix
pool is the *whole airport's* CIFP-known fix vocabulary (every fix any
procedure at that airport references, not just the one procedure the plate
depicts) -- deliberately, since matching a d-TPP `chart_name` string
("ILS Y OR LOC Y RWY 23") against a CIFP procedure ident ("I23-Y") is its
own unsolved matching problem this pass sidesteps entirely by not needing
the answer.

## A real regions.yaml gap, found along the way

Region assignment reuses OurAirports (already a source in this repo,
`packtools/ourairports.py`) joined against `regions.yaml` bboxes -- the
same method PA10 measured region sizes with (§1.3). Doing that join for
real surfaced a genuine gap: **Adak Island (PADK)**, a real FAA airport
with real d-TPP plates (the same one this document's georeferencing
example uses), sits at `-176.676°` longitude -- west of the `alaska`
region's `lon_min` of `-170`. It matches **no** plate region today,
alongside the 268 Canada/Mexico/Caribbean airports PA10 already flagged as
out of scope (§6) -- except Adak is a US airport, not a foreign-coverage
question. `tests/test_make_plates.py`'s
`test_airport_region_map_real_adak_falls_outside_the_alaska_region` pinned
this with Adak's real coordinates (this pass deliberately left the gap
unpatched; AER-1990 later widened `alaska` and replaced that pin with
`test_airport_region_map_real_adak_now_maps_to_alaska`).

Not fixed here: `regions.yaml` is shared by terrain and water, so widening
the `alaska` bbox is a bigger cross-cutting call than this pass should make
unilaterally -- flagging it as a finding for the board, not a silent patch.
See `docs/aer-1990-alaska-region-gap.md` for the measurement and fix.

## Build + upload

```bash
# 1. Build the procedures-conus pack first (PA1) if you want the IAP
#    georeferencing attempt -- omit --procedures-db to skip it (every plate
#    then ships with no geo tag, same as any non-IAP chart type).
packtool build-pack work/procedures-conus.pack --id procedures-conus \
    --kind procedures --cycle 2609 --out work

# 2. Fetch + build every region with a matched airport. --only limits to
#    specific regions (a CONUS pilot on an SD-card Pi installs one).
packtool make-plates --cycle 2609 --effective 2026-09-03 --expires 2026-10-01 \
    --procedures-db work/packs/procedures-conus-2609.pack \
    --work work/plates --out work
#   -> work/packs/plates-us-west-2609.pack, plates-us-east-2609.pack, ...

# 3. --upload pushes to R2 and updates the manifest -- do not run this
#    until the merge-before-publish rule above is satisfied.
packtool make-plates --cycle 2609 --effective 2026-09-03 --expires 2026-10-01 \
    --work work/plates --out work --upload
```

Every fetch step (the metafile, each PDF) is skip-if-present, like the rest
of this pipeline -- a resumed partial run never re-fetches what is already
on disk.

## What is explicitly still open (not this pass)

- **Page-region clustering** for the ambiguous-fix case above -- the single
  biggest lever on how often a plate actually gets a geo tag.
- **A `plates.yml` dispatch workflow** (or cyclical-cron wiring) -- today
  this is a manually-run CLI, same maturity stage highways/roads started at.
- **Canada/Mexico/Caribbean coverage** -- out of scope per PA10 §6, and
  Adak Island (above) -- a real US gap in `regions.yaml`, not a foreign-
  coverage question.
- **PA12 (the plate viewer)** -- single-page vector-PDF rendering plus an
  ownship overlay gated per-plate on `plate_georef.status == 'ok'` and
  `residual_nm` clearing whatever budget PA12 sets; this pass supplies the
  measured number, not the budget or the renderer.
