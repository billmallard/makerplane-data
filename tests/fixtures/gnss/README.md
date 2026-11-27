<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# NAVCEN recordings for the gnss pack

`navcen-20261127/` is the response to every request `packtool make-gnss` made
against navcen.uscg.gov at about 15:45 UTC on 2026-11-27. The files are
unmodified. Their layout mirrors the URL paths. A path ending in `.404` would
mark a recorded 404, and this recording has none. The recording holds the YUMA
almanac (week 399, which is full week 2447) and `current_nanu.nnu` (2026105).
It holds the NANUs the walk read, 2026088..2026104, and the older per-PRN NANUs
the `gps-constellation` page links (2025002..2026085), and that page itself,
saved as `gps-constellation.page`. The data is US-government work (17 U.S.C. 105).

To make a new recording:

```bash
packtool make-gnss --record tests/fixtures/gnss/navcen-YYYYMMDD \
    --now YYYY-MM-DDTHH:MM:SSZ --out /tmp/gnss.json
```

To replay it, as `tests/test_gnss.py` does:

```bash
packtool make-gnss --fixture tests/fixtures/gnss/navcen-20261127 \
    --now 2026-11-27T16:00:00Z --out /tmp/gnss.json
```

The values the tests expect were read from these files by hand, for example
NANU 2026105 (`PRN 06 FCSTDV, 03 DEC 2026 1600Z - 04 DEC 2026 0400Z`) and
the first YUMA record.
