<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# `FAACIFP18` test fixture (AER-1600 / PA1)

156 real records extracted verbatim (no fields altered) from the live FAA
CIFP cycle **2609** (effective 2026-09-03), downloaded from
`https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_260903.zip` on 2026-09-19.
FAA CIFP data is a US Government work (public domain); see
`docs/LICENSE-AUDIT.md`.

Three more records (`CFDXF`/`CFDXG`/`CFDXH`) were added on 2026-09-19
(AER-1700) from the same cycle -- see "What is in it" below.

This is the brief's "golden-procedure fixture set" (`procedures_and_airways_plan.md`
Sec. 7): a handful of real procedures with a known-correct parsed form that
every downstream PA item can test against, so nobody re-derives them.

## What is in it

| Selection | Records | Why |
|---|---:|---|
| `HDR01`-`HDR05` | 5 | file-cycle header (`file_cycle()` reads `HDR04`) |
| KSBA `FLOUT5` SID | 14 | vector legs (`VA`/`CI`), the SID-with-vectors case |
| KSBA `PITBL2` STAR | 19 | a STAR with plain `TF`/`IF` legs |
| KSBA `I07` ILS approach | 15 | IAF transitions, `HM` hold, missed approach |
| KABQ `H21-Y` RNP approach | 18 | real `RF` (radius-to-fix) arc legs |
| `09J` `VOR-A` approach | 11 | `PI` (procedure turn) and `AF` (DME arc) legs |
| `A315`, `A509` airways (USA only) | 17 | Enroute Airways (`ER`), one with a foreign-region (Bahamas) fix |
| Resolving fix records | 54 | every VOR/NDB/waypoint/runway the above legs reference, so every fix resolves (`fix_lat`/`fix_lon` verified non-NULL for all 76 procedure legs and all 17 airway legs) |
| `CFDXF`/`CFDXG`/`CFDXH` centre-fix waypoints | 3 | AER-1700: the RF legs' *Center Fix* (cols 107-116) references these idents but never defines them -- without these records every RF arc's centre silently resolves to `(None, None)`; added so the golden RF-arc radius test has a real centre to resolve against |

KSBA (Santa Barbara) was chosen because it is also PA14's planned bench-validation
airport (SID -> airway -> STAR -> approach against X-Plane); reusing it here
means PA14 does not need a second extraction. KABQ and `09J` were pulled
in only for the leg types KSBA's own procedures don't happen to exercise
(no `RF`/`PI`/`AF` legs at KSBA in this cycle).

## Regenerating or extending

Not scripted (one-off extraction, `/tmp` throwaway during AER-1600) --
reproduce by downloading the current CIFP cycle and filtering
`FAACIFP18` by column position, e.g. for a procedure:

```python
lines = [l for l in open("FAACIFP18", encoding="latin-1") if len(l.rstrip("\n")) == 132]
ksba_flout5 = [l for l in lines if l[4]=='P' and l[12]=='D' and l[1:4]=='USA'
               and l[6:10]=='KSBA' and l[13:19]=='FLOUT5']
```

then collect every `(fix_id, fix_icao_or_airport, fix_section, fix_subsection)`
the selected legs reference (see `packtools/arinc424.py`'s module docstring
for the airport-scoped-vs-area-scoped distinction) and pull the matching
VOR/NDB/waypoint/runway record for each so no leg is left unresolved.
