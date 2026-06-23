# SVS runway elevation vs terrain — investigation & decision

**Status: investigation complete; fix decision DEFERRED to real-flight validation
(Stratux, GPS MSL altitude). 2026-06-22.**

Why the pyEfis synthetic-vision (SVS) runway sometimes appears *above* the
aircraft on approach in X-Plane — and why that is largely an X-Plane artifact,
not a pyEfis error. Written so the reasoning survives; the SVS code lives in the
**pyEfis** repo (`src/pyefis/instruments/ai/`), the terrain data in
**makerplane-data**.

## The original observation (Bill)
On X-Plane approaches the pyEfis runway sits higher than the X-Plane runway, so
you descend below the pyEfis runway to land on the X-Plane one. **Not consistent**
across airports/runways. Initial read was "X-Plane problem, our data is correct."
This pass tested that properly.

## How pyEfis renders a runway
A runway is one **flat plane**: elevation linearly interpolated between the two
NASR threshold elevations (`thr1_elev → thr2_elev`) along the length, **level
across the width**, subdivided but with **no vertical profile (crown/sag) and no
terrain conforming** (`svs.py` runway-surface builder; `_emit_runway_marking_quads`
`rwy_point`). It is *not* horizontal-flat — it tilts to follow the thresholds (the
"chord") — but it ignores any mid-runway crown.

- Runway elevation source = **NASR survey** (`airport_db.py`, `runway_ends.elev_ft`
  / `tdz_elev_ft`, from the FAA `APT_RWY_END.csv`).
- Terrain elevation source = **GLO-30 DEM** (Copernicus 1-arc-sec).
- These are **different data sources** and they disagree by a few feet.

## Measurements (KGPM rwy 18/36; live X-Plane via RREF + GLO-30 sampling)
NASR: thr18 = 581.5 ft, thr36 = 572.8 ft, **both TDZ = 588.4 ft** → the runway
**crowns** ~7–15 ft in the middle and slopes down to both ends. Field elev 590.

Full centreline profile (GLO-30 ≈ the real/X-Plane surface; chord = what pyEfis draws):

| Position | Real surface (GLO-30 ≈ X-Plane) | pyEfis chord (NASR) | Chord error |
|---|---|---|---|
| 0% (thr 18) | 577 | 581.5 | **+4 (floats above)** |
| ~10% | 581 | 581 | match |
| 50% (center) | **584** | 577 | **−7 (sinks below)** |
| ~80% | 574 | 574 | match |
| 100% (thr 36) | 568 | 572.8 | **+5 (floats above)** |

Live X-Plane (aircraft parked on the runway, `elevation − y_agl`):
- thr 18: NASR chord 581.3 vs **X-Plane 576.9** → **+4.5 ft** (pyEfis above)
- 17%: 580.0 vs 580.2 → match (crossover)
- thr 36 (98%): 573.0 vs **575.4** → −2.4 (X-Plane above)

pyEfis's **own** terrain (GLO-30) at the same spots: thr18 577.4, center 584.2,
thr36 567.6 — i.e. the **terrain mesh agrees with X-Plane** (both DEM), and the
**runway (NASR survey) is the outlier**, ~4–5 ft above the terrain at the
thresholds and ~7 ft below it at the crown.

## Cross-checks
- **GLO-30 ≈ X-Plane** at the 18 end and mid (same DEM lineage), so GLO-30 is a
  usable proxy for X-Plane's surface — the profile can be mapped without taxiing.
  (X-Plane does its *own* runway flattening, so it diverges from raw DEM at some
  ends — KGPM thr36 X-Plane 575 vs GLO 569 — so X-Plane is **not** a reliable
  absolute datum reference.)
- **ForeFlight** (KGPM): runway at **survey ~580 ft**, sitting **flush** on its
  terrain (no float). GPS ALT ~580 on the threshold. So ForeFlight uses the same
  survey-class runway elevation pyEfis does, and **grades its terrain up to meet
  it**.
- **ForeFlight FLATTENS runways (KASE/Aspen):** Aspen drops 158 ft (1.97%) — real,
  and **X-Plane renders that slope clearly** — but **ForeFlight shows it "really
  flat."** So ForeFlight is a clean *situational-awareness* depiction, **not
  slope-accurate**. X-Plane's slope is the truthful geometry, and **pyEfis's chord
  already matches it.**

## Conclusion — pyEfis's runway is correct; the float is an X-Plane artifact
1. **Geometry/elevation are right.** pyEfis's sloped chord matches X-Plane's true
   slope and ForeFlight's survey elevation. pyEfis is doing the runway correctly.
2. **The "runway above me" is X-Plane's altitude, not pyEfis's runway.** In this
   rig the aircraft altitude comes from X-Plane, which references altitude to its
   **DEM mesh** (~577 ft on the KGPM runway) — ~4.5 ft below the FAA survey
   (581.5). pyEfis draws the runway at survey, so it sits ~4.5 ft above where
   X-Plane *thinks* you are.
3. **On real hardware there is no float.** A real altimeter — QNH-set baro, or GPS
   MSL — reads ≈ field/threshold (survey) over the runway. Survey runway + survey-
   referenced altitude agree → no float. The only real-world residue is cosmetic:
   the DEM terrain is ~4.5 ft low right at the pavement (its 30 m cell blurs in the
   lower overrun), so the runway looks slightly raised on its own terrain.
4. **X-Plane cannot validate this** — its altitude is the very thing that's off.
   **ForeFlight** (GPS altitude + survey runway) already agrees with pyEfis.

## Why the "obvious" fix backfires in the sim
Grading the terrain *up* to the survey runway (the natural fix, and what ForeFlight
effectively does) would make the **X-Plane picture worse**: pyEfis would lift the
terrain to 581.5 while X-Plane keeps reporting 576.9, so the aircraft renders ~4.5
ft *below* the terrain — underground, looking up at the runway. Same gap, worse
direction. It only looks right when the altitude source shares the survey datum
(real hardware / ForeFlight).

## Decision (Bill, 2026-06-22): validate in the real aircraft first
Defer any code change until flown on real hardware with a **Stratux** (GPS MSL
altitude) feeding pyEfis. Decide from there. Rationale: the fix direction depends
on whether, with a survey-datum altitude source, the runway sits correctly (expected)
— in which case the only worthwhile change is cosmetic terrain grading for real
flight — or whether a second contributor appears (the ~10 ft Bill perceived vs the
~4.5 ft measured).

**Real-flight validation checklist (when the Stratux arrives):**
1. Confirm pyEfis's SVS altitude is **MSL**, not GPS HAE — a Stratux/GDL90 HAE feed
   would add a geoid offset (~25–30 ft in CONUS) and swamp this whole effect. (Check
   the fix-gateway Stratux/GDL90 plugin altitude handling.)
2. Fly an approach to a known field (KGPM is the characterised one) and watch the
   runway on short final / over the threshold: with survey-datum altitude it should
   sit on/just under you, not float.
3. If it still floats by several feet, look for a second contributor (altitude
   datum, SVS eye-height offset) — the ~10 ft-perceived vs ~4.5 ft-measured gap.

## Candidate fixes (only if real flight shows a problem)
- **Cosmetic terrain grading (real-flight benefit):** grade the GLO-30 heightmap to
  the runway surface in a corridor (runway width + feathered shoulder) so the
  survey-accurate runway sits flush on its own terrain. Fixes the DEM-blur dip;
  keeps survey accuracy. Does **not** help the X-Plane rig (see above).
- **Crown profile (thr + TDZ control points):** only if a genuinely humped runway
  looks wrong; imperceptible at KGPM and ForeFlight ignores it. Likely unnecessary.
- **Conform runway to DEM:** would match X-Plane but put the runway ~4.5 ft below
  real-world truth — wrong for the actual aircraft. Not recommended.

## Tools built (reusable)
- `runway_datum_probe.py` — RREF-subscribes X-Plane (`10.110.10.167:49000`) for
  lat/lon/elevation/y_agl on its own UDP port (49055, doesn't disturb fix-gateway),
  projects the aircraft onto the nearest NASR runway centreline, prints the
  NASR-chord-vs-X-Plane-surface delta live. Run with `python3 -u`.
- `glo30_sample.py` / `rwy_profile.py` — sample pyEfis's GLO-30 terrain along a
  runway centreline (tile `>i2`, 3601², `row=(tlat+1−lat)·(n−1)`, `col=(lon−tlon)·(n−1)`).
  GLO-30 is a faithful X-Plane proxy for mapping the profile without taxiing.

(Probe currently has KGPM/X-Plane host hard-coded; generalise before reuse.)
