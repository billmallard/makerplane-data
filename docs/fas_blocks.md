<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# FAS data blocks in the procedures pack (`fas_blocks`)

AER-5623 / VNAV1. Parser: `arinc424.iter_path_points` and `arinc424.fas_crc`.
Builder: `_build_fas_blocks` in `packtools/build/procedures.py`. Pack
`procedures-conus`, `schema_version` 4.

## What it is

LP and LPV approaches are flown from the Final Approach Segment (FAS) data
block, not from the coded legs. The FAA CIFP carries one block per LP or LPV
final as an ARINC 424 Path Point record (`PP`, ARINC 424-17 Sec. 4.1.28): a
primary record holding everything the block's CRC protects, then one
continuation holding the orthometric heights, the approach type and the
WAAS channel.

| Cycle | `PP` blocks | LPV | LP | GLS |
|---|---:|---:|---:|---:|
| 2605 | 4,906 | 4,187 | 719 | 0 |
| 2611 | 4,917 | 4,199 | 718 | 0 |
| 2612 | 4,917 | 4,199 | 718 | 0 |

The FAA CIFP carries no GLS blocks. A text search for "GLS" in 2605's `PP`
lines finds 4 blocks, but those are Galveston's (KGLS) LPV and LP finals.

Every block in all three cycles re-computes to its published CRC.

## The table

One row per block. The column comments in the builder's `CREATE TABLE`
are the reference; they are also what the `/schema/` pages show. The points
that matter to a reader:

- **Attach.** `proc_id` is the approach in `procedures` (same `airport`
  and `ident`). In 2611 every block attaches. `runway` is the block's own
  runway (`RW28`), and `route_indicator` (`Y`, `Z`, ...) tells apart two
  finals to one runway. It is the same letter as the approach ident's
  suffix (`R08-Y`).
- **Service level.** `approach_type` is `LPV` or `LP`. An LP block has
  `val_m` 0: it has no vertical alert limit, because LP has no vertical
  guidance.
- **Units.** Heights and alert limits are metres. `tch` is in `tch_units`
  (`F` feet or `M` metres; every 2611 block is `F`). `glidepath_angle_deg`
  is degrees. `course_width_m` is the full-scale width at the LTP.
  `length_offset_m` runs from the stop end of the runway to the FPAP.
- **Positions.** `ltp_*` and `fpap_*` are WGS-84 degrees, decoded from the
  record's high-precision fields (0.0001 arc-second). The exact text is in
  `record`, which holds every primary field as read plus the continuation
  record.
- **`crc`.** The block's CRC-32Q as published. The build re-computed it
  from the record's fields and matched it before storing the row. A reader
  can repeat that check with `arinc424.fas_crc(json.loads(record)["fields"])`.

## The CRC rule: a failed block is dropped, never shipped

ARINC 424 defines the CRC-32Q polynomial (Sec. 6.10.2) and refers to RTCA
DO-229 for how the block's fields are packed into bits. The packing below
was derived and then proved on the live cycles: it reproduces all 4,917
published CRCs in 2611 and in 2612 and all 4,906 in 2605. Changing any one
of seven rules alone takes 2611 from 4,917 matches to 0: character order,
bit order within a field, byte bit order of the CRC, initial value, the
height offset, the FPAP delta and the 6-bit characters. The rounding rule
is the exception (see below).

- The block is 288 bits, field by field: operation type (4), SBAS provider
  (4), airport (32), runway number (6) and letter (2: none 0, R 1, C 2,
  L 3), approach performance designator (3), route indicator (5: blank 0,
  A 1 ...), reference path data selector (8), reference path ident (32),
  LTP latitude and longitude (32 each), LTP height (16), FPAP minus LTP
  latitude and longitude (24 each), TCH (15), TCH units (1), glidepath
  angle (16), course width (8), length offset (8), HAL (8), VAL (8).
- Every field goes least significant bit first.
- The airport and the reference path ident are four characters, each the
  low six bits of its ASCII code plus two zero bits. The rightmost
  character goes first.
- Latitudes and longitudes are two's complement, in 0.0005 arc-second
  steps. LTP height is (metres + 512) in 0.1 m. TCH is in 0.1 ft or
  0.05 m. Course width is (metres - 80) in 0.25 m. Length offset is in 8 m
  steps (255 if blank). HAL and VAL are in 0.2 m.
- The remainder is computed MSB-first over that bit stream, with no initial
  value and no final XOR. It is written as the 40-byte block is sent: four
  bytes, each byte's bits reversed against the register.

One record's coordinate is off the 0.0005 arc-second grid: PAMR R08-Y's LTP
longitude ends `...7784`. Only rounding it to `...7785` reproduces the FAA's
CRC, so the build rounds to the nearest step. That value is the only
off-grid one in the three cycles, and it is negative, so it cannot tell
"nearest" apart from "away from zero" or a signed floor. If the FAA's rule
differs on a later value, that block fails its CRC and is dropped.

**What happens on a failure.** A block whose CRC does not re-compute, or
whose fields do not encode, is not stored. The build prints
`WARN fas_blocks: dropped <airport> <approach> <runway>: CRC <published>,
computed <ours>`. The pack report's `PROC-FAS-001` counts the source's
`PP` records again, lists every dropped block by name and warns. A build
that stores no blocks at all is refused.

## What the CRC does and does not prove

A block that re-computes to the FAA's CRC was decoded, field for field,
into the same values the FAA's own source held when it computed the CRC.
That covers every field in the wrap. It does not cover:

- the continuation's fields (orthometric heights, approach type, channel),
  which are outside the wrap. Their layout is from ARINC 424-17 Sec.
  4.1.28.2. Two sources were checked by eye and agree with it: the FAA Form
  8260-3 for KUSE RWY 27 ("LTP ORTHOMETRIC HEIGHT +02347") and the 2611
  KSSF RNAV (GPS) RWY 28 plate ("WAAS CH 42747");
- the scaling of the decoded columns. The encoder reads the raw text, so a
  column decoded at the wrong scale would still pass. Scales follow ARINC
  424-17 Sec. 5. They were checked against the KUSE RWY 27 8260-3's FAS
  data block table (HAL 40.0, VAL 50.0, TCH 00055.0 F, GPA 03.10, course
  width 106.75, length offset 1568), and `PROC-FAS-002` re-checks the CRC
  from each stored record;
- a block that is wrong at the source but internally consistent.

## Pack report checks

| Check | What it measures |
|---|---|
| `PROC-FAS-001` | Source `PP` records = stored blocks + CRC drops, every drop named. Needs `--inputs`. Warns on any drop. |
| `PROC-FAS-002` | Every stored row's CRC re-computes from its own `record`. Runs on the pack bytes alone. |
| `PROC-FAS-003` | Blocks whose (airport, ident) names no approach in the pack. Warns. |

All three carry `pending_review` until QA reviews them, so for now none of
them can stop a publish.

## Compatibility

Additive. A full 2611 build from this branch and from `dev` gives
row-for-row identical `airways`, `airway_legs`, `procedures`,
`transitions`, `legs`, `msa`, `grid_mora_bands` and `grid_mora`, with
unchanged DDL. The only new objects are `fas_blocks` and its two indexes.
`schema_version` moves from 3 to 4 so a reader that wants the table can
tell from `pack_meta`. A reader that only knows version 3 is unaffected.

Editions already published (2611, 2612) do not carry the table. The
runner skips any edition already in the store, so the first edition with
FAS blocks is the first cycle built after this reaches `main`.
