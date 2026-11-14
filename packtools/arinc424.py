# SPDX-License-Identifier: Apache-2.0
"""A permissive ARINC 424-18 (CIFP) fixed-width record reader.

Retires the ``_build_cifp`` deferral (packtools/build/__init__.py): that
deferral existed because the only procedure-capable indexer in reach was
pyAvTools's ``CIFPObjects.py``, which is GPL-2.0 -- and it does not parse
procedures at all, only airports/runways/navaids. This module is an
independent implementation written directly against the published ARINC
424-17 field-position tables (Sec. 4, "Navigation Data - Record Layout"),
cross-checked field-by-field against a live FAACIFP18 cycle (2609) so the
column offsets below are verified, not merely transcribed. No GPL source
was read or consulted.

"Permissive" describes the licence of this code, not a promise to coerce
unrecognised data: every field is stored as read. Rejecting a procedure
because it contains a leg type the *flight-plan engine* cannot fly is a
downstream (fix-gateway) concern -- see brief guardrail 1 -- not this
module's job. This module's only obligation is not to lose or silently
alter what the source file says.

CIFP is a whole-region file: Section E/Subsection R (``ER``) carries US,
Canadian, Pacific and Latin American airway legs in one interleaved
stream, distinguished only by the Customer/Area Code (cols 2-4), and
Section P airport records span every ICAO region the file covers. Nothing
here filters by area code -- that is a build-time policy decision, not a
parsing one.

Record layout (0-indexed Python slices; the ARINC spec numbers columns
1-indexed and inclusive, i.e. spec "14 thru 18" is ``slice(13, 18)``):

Common to every record: Record Type ``[0:1]``, Customer/Area Code
``[1:4]``, Section Code ``[4:5]``.

Fix-bearing records (VOR/NDB navaids, enroute/terminal waypoints, runways)
share one convention, verified against KSBA/KABQ fixture data: identifier
at ``[13:18]``, latitude at ``[32:41]``, longitude at ``[41:51]``. Their
subsection code position differs by family (D-section: ``[5:6]``;
E-section: ``[5:6]``; P-section: ``[12:13]``).

Disambiguation differs by scope, and getting this wrong produces wrong
*positions*, not missing ones -- the dangerous failure mode. VOR/NDB
navaids and enroute waypoints are area-scoped: their ICAO region code
(``[19:21]``) is the right disambiguator (a region subdivides the US into
FAA-sized chunks specifically so idents don't collide within one).
Terminal waypoints and runways are airport-scoped, and the region code is
*not* enough -- 37 different California airports in region "K2" all have
a runway ident "RW07" (verified against the live file). A procedure leg
referencing "RW07"/"K2" only resolves correctly against the *procedure's
own airport* (cols ``[6:10]`` of the surrounding PD/PE/PF record), because
ARINC's own design guarantees a procedure only ever cites its own
airport's terminal waypoints/runways -- see ``build_fix_index`` and
``_resolve``.

Grid MORA (``AS``) records carry their own position (a band's SW corner)
and need no fix index -- see ``iter_grid_mora``.

Section U (``UC`` controlled / ``UR`` restrictive airspace) carries
coordinates on every record and references no fix, so it needs no index;
see ``iter_airspace_records``.

Three more fix families resolve since AER-4446 (MSA centres need all
three; procedure legs naming one used to be left unresolved). Offsets
verified against the live 2611 cycle:

- Airport Reference Points (``PA``): the airport is its own fix, ident and
  owning airport both ``[6:10]``, position at the shared ``[32:51]``
  (KSBA reads N34253429 W119502937, the published ARP). Airport-scoped.
- Terminal NDBs (``PN``): subsection at ``[5:6]`` like the D section, not
  ``[12:13]`` like the rest of P; ident ``[13:18]``, owning airport
  ``[6:10]``, position ``[32:51]``. Airport-scoped.
- DME-only navaids (TACAN, DME, ILS-DME): 1,266 of 2,458 D records carry
  no VOR position at ``[32:51]``, only a DME position at ``[55:74]``. For
  those the DME position is the facility's position, so it is used; a
  record that has both keeps the VOR position.

A fix that still does not resolve gets ``NULL`` coordinates, never a
guessed one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

RECORD_LENGTH = 132

# --- fix-source records (D/DB navaids, EA/PC waypoints, PG runways) ------
# Verified against real KSBA/KABQ records (VOR "GVO", waypoint "GOLET"/
# "FLOUT", runway "RW07"): ident and lat/lon sit at the same offsets
# regardless of which of these five record families produced them.
_FIX_IDENT = slice(13, 18)
_FIX_LAT = slice(32, 41)
_FIX_LON = slice(41, 51)
_FIX_ICAO_AREA_SCOPED = slice(19, 21)      # VOR/NDB navaids, enroute waypoints
_FIX_OWNING_AIRPORT = slice(6, 10)         # terminal waypoints, runways, ARPs, terminal NDBs
_DME_LAT = slice(55, 64)                   # D section: DME position, used when the
_DME_LON = slice(64, 74)                   # record carries no VOR position (DME-only)

# --- ER: Enroute Airways primary record (ARINC 424-17 Sec. 4.1.6.1) ------
_ER = dict(
    area=slice(1, 4),                  # Customer/Area Code -- USA/CAN/PAC/LAM
    route_ident=slice(13, 18),
    seq=slice(25, 29),
    fix_id=slice(29, 34),
    fix_icao=slice(34, 36),
    fix_section=slice(36, 37),
    fix_subsection=slice(37, 38),
    continuation=slice(38, 39),
    direction=slice(46, 47),           # Direction Restriction
    recd_navaid=slice(50, 54),
    theta=slice(62, 66),
    rho=slice(66, 70),
    course=slice(70, 74),
    dist=slice(74, 78),
    min_alt=slice(83, 88),
    min_alt2=slice(88, 93),            # MEA against record order (AER-5460)
    max_alt=slice(93, 98),
)

# --- PD/PE/PF: Airport SID/STAR/Approach primary record (Sec. 4.1.9.1) ---
_PROC = dict(
    airport=slice(6, 10),
    subsection=slice(12, 13),          # D=SID, E=STAR, F=Approach
    ident=slice(13, 19),
    route_type=slice(19, 20),
    transition=slice(20, 25),
    seq=slice(26, 29),
    fix_id=slice(29, 34),
    fix_icao=slice(34, 36),
    fix_section=slice(36, 37),
    fix_subsection=slice(37, 38),
    continuation=slice(38, 39),
    wdc=slice(39, 43),                 # Waypoint Description Code
    turn_dir=slice(43, 44),
    rnp=slice(44, 47),
    path_term=slice(47, 49),
    recd_navaid=slice(50, 54),
    theta=slice(62, 66),
    rho=slice(66, 70),
    course=slice(70, 74),
    dist=slice(74, 78),
    alt_desc=slice(82, 83),
    alt1=slice(84, 89),
    alt2=slice(89, 94),
    speed=slice(99, 102),
    vertical_angle=slice(102, 106),
    centre_fix=slice(106, 111),        # RF (radius-to-fix) arc centre (5.20)
    centre_icao=slice(112, 114),
    centre_section=slice(114, 115),
    centre_subsection=slice(115, 116),
)

_KIND_BY_SUBSECTION = {"D": "sid", "E": "star", "F": "approach"}

# --- PS: Airport MSA primary record (Sec. 4.1.20.1) ----------------------
# Offsets verified against every PS record in the live 2611 cycle (6,058
# records, all primaries): each one's sectors tile 360 degrees exactly, and
# KSBA's RW07, GVO and RZS records match the MSA circles printed on the
# KSBA RNAV (GPS) RWY 7, VOR RWY 25 and ILS OR LOC RWY 7 plates.
_MSA = dict(
    record_type=slice(0, 1),
    area=slice(1, 4),
    section=slice(4, 5),
    airport=slice(6, 10),
    airport_icao=slice(10, 12),
    subsection=slice(12, 13),
    centre_ident=slice(13, 18),
    centre_icao=slice(18, 20),
    centre_section=slice(20, 21),
    centre_subsection=slice(21, 22),
    multiple_code=slice(22, 23),       # tells apart two MSAs on one centre (5.130)
    continuation=slice(38, 39),
    sectors=slice(42, 119),            # 7 x 11 chars, see _MSA_SECTOR
    mag_true=slice(119, 120),          # M = magnetic, T = true (5.165)
    file_record_no=slice(123, 128),
    cycle=slice(128, 132),
)
#: One MSA sector inside ``_MSA["sectors"]``: bearing start and end (3+3
#: digits, whole degrees, clockwise), altitude (3 digits, hundreds of feet),
#: radius (2 digits, nautical miles). The bearings are bearings *to* the
#: centre fix -- the plates draw them as arrows pointing in at it -- and a
#: one-sector MSA codes the full circle as start == end ("180180").
_MSA_SECTOR_WIDTH = 11
_MSA_SECTORS = 7

# Waypoint Description Code (WDC) flag bits -- Sec. 5.17, Fig. 5-4.
FLAG_FLYOVER = 1 << 0
FLAG_IAF = 1 << 1
FLAG_FAF = 1 << 2
FLAG_MAP = 1 << 3
FLAG_HOLD = 1 << 4
FLAG_FIRST_MISSED_LEG = 1 << 5
#: Final Approach Course Fix (col 43 ``I``, or ``D`` = IAF with FACF): a fix
#: on the coded final approach path before the FAF (Sec. 2.2), e.g. the
#: vectors-to-final fix. It is not the FAF and never carries FLAG_FAF (AER-5555).
FLAG_FACF = 1 << 6

_HOLD_PATH_TERMS = frozenset({"HA", "HF", "HM"})


def read_records(path: str | Path) -> Iterator[str]:
    """Yield each fixed-length data record, header lines and short/blank
    trailing lines dropped. FAA CIFP text is Latin-1 (ARINC 424 predates
    Unicode); non-CIFP bytes have never been observed in an FAA cycle, but
    ``errors="replace"`` keeps a stray byte from taking down the whole
    build."""
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if len(line) == RECORD_LENGTH and line[0] in ("S", "T"):
                yield line


def file_cycle(path: str | Path) -> str | None:
    """The AIRAC cycle this CIFP file itself declares (HDR04's "VOLUME
    nnnn"), independent of any individual record's own last-revised cycle
    stamp (cols 129-132) -- those two are not the same thing. A record
    untouched since a prior cycle keeps its old stamp; the file-level
    volume number is the one that matters for pack currency."""
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        for _ in range(10):
            line = f.readline()
            if not line:
                break
            m = re.search(r"VOLUME\s+(\d{4})", line)
            if m:
                return m.group(1)
    return None


def _strip(s: str) -> str | None:
    s = s.strip()
    return s or None


def _parse_latlon(lat_raw: str, lon_raw: str) -> tuple[float, float] | None:
    """Decode the ARINC lat/lon pair: N/Sddmmss.ss (9 chars) and
    E/Wdddmmss.ss (10 chars), hemisphere + degrees + minutes + hundredths
    of a second, e.g. "N34165286" -> 34 deg 16' 52.86"N."""
    lat_raw, lon_raw = lat_raw.strip(), lon_raw.strip()
    if not lat_raw or not lon_raw or len(lat_raw) != 9 or len(lon_raw) != 10:
        return None
    try:
        lat_hemi, lat_deg, lat_min, lat_sec = (
            lat_raw[0], int(lat_raw[1:3]), int(lat_raw[3:5]), int(lat_raw[5:9]) / 100.0)
        lon_hemi, lon_deg, lon_min, lon_sec = (
            lon_raw[0], int(lon_raw[1:4]), int(lon_raw[4:6]), int(lon_raw[6:10]) / 100.0)
    except ValueError:
        return None
    lat = lat_deg + lat_min / 60.0 + lat_sec / 3600.0
    lon = lon_deg + lon_min / 60.0 + lon_sec / 3600.0
    if lat_hemi == "S":
        lat = -lat
    if lon_hemi == "W":
        lon = -lon
    return (round(lat, 8), round(lon, 8))


def _to_int(raw: str) -> int | None:
    """A handful of otherwise-numeric fields carry the literal "UNKNN"
    (confirmed in live Bahamas-region airway records) meaning exactly what
    it says -- no published value, not zero. Permissive means treating that
    as absent data, not raising on it."""
    try:
        return int(raw)
    except ValueError:
        return None


def _decode_altitude(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("FL"):
        n = _to_int(raw[2:])
        return n * 100 if n is not None else None
    return _to_int(raw)


def _decode_tenths(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    n = _to_int(raw)
    return n / 10.0 if n is not None else None


def _decode_dist_or_time(raw: str) -> tuple[float | None, float | None]:
    """Route Distance/Holding Distance-or-Time (5.27): a plain 4-digit
    field is nautical miles (1 implied decimal); a leading "T" flags a
    holding leg length given as time in minutes (1 implied decimal) instead
    of distance."""
    raw = raw.strip()
    if not raw:
        return (None, None)
    if raw[0].isalpha():
        n = _to_int(raw[1:])
        return (None, n / 10.0) if n is not None else (None, None)
    n = _to_int(raw)
    return (n / 10.0, None) if n is not None else (None, None)


def _decode_rnp(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    n = _to_int(raw)
    return n / 100.0 if n is not None else None


def _decode_wdc_flags(wdc: str, path_term: str) -> int:
    wdc = (wdc or "").ljust(4)
    flags = 0
    if wdc[1] in ("Y", "B"):
        flags |= FLAG_FLYOVER
    if wdc[3] in ("A", "C", "D"):
        flags |= FLAG_IAF
    if wdc[3] == "F":
        flags |= FLAG_FAF
    if wdc[3] in ("D", "I"):
        flags |= FLAG_FACF
    if wdc[3] == "M":
        flags |= FLAG_MAP
    if wdc[2] == "M":
        flags |= FLAG_FIRST_MISSED_LEG
    if path_term in _HOLD_PATH_TERMS:
        flags |= FLAG_HOLD
    return flags


def _fix_type(section: str | None, subsection: str | None) -> str | None:
    sub = (subsection or " ").strip()
    if section == "D" and sub == "":
        return "vor"
    if section == "D" and sub == "B":
        return "ndb"
    if section == "E" and sub == "A":
        return "waypoint"
    if section == "P" and sub == "C":
        return "waypoint"
    if section == "P" and sub == "G":
        return "runway"
    if section == "P" and sub == "N":
        return "ndb"
    if section == "P" and sub == "A":
        return "airport"
    return None


FixKey = tuple[str, str, str, str]  # (ident, scope, section, subsection)

#: (section, subsection) pairs whose ident is unique only per-airport, not
#: per-ICAO-region -- see module docstring.
_AIRPORT_SCOPED = frozenset({("P", "C"), ("P", "G"), ("P", "A"), ("P", "N")})


def build_fix_index(path: str | Path) -> dict[FixKey, tuple[float, float]]:
    """Index every VOR/NDB navaid, enroute/terminal waypoint and runway in
    the file so procedure and airway legs -- which reference a fix only by
    identifier plus region, never by coordinate -- can be resolved to a
    lat/lon. The index key's second element is the ICAO region code for
    area-scoped fixes but the *owning airport identifier* for
    airport-scoped ones (terminal waypoints, runways); see module
    docstring for why the region code alone under-disambiguates those.
    """
    index: dict[FixKey, tuple[float, float]] = {}
    for line in read_records(path):
        section = line[4:5]
        ident = _strip(line[_FIX_IDENT])
        if section == "D":
            subsection = line[5:6].strip()
            scope = _strip(line[_FIX_ICAO_AREA_SCOPED])
        elif section == "E":
            if line[5:6] != "A":
                continue
            subsection = "A"
            scope = _strip(line[_FIX_ICAO_AREA_SCOPED])
        elif section == "P":
            # PN carries its subsection where the D section does; every
            # other P record carries it at [12:13].
            subsection = "N" if line[5:6] == "N" else line[12:13]
            if (section, subsection) not in _AIRPORT_SCOPED:
                continue
            scope = _strip(line[_FIX_OWNING_AIRPORT])
            if subsection == "A":
                ident = scope
        else:
            continue

        if ident is None or scope is None:
            continue
        latlon = _parse_latlon(line[_FIX_LAT], line[_FIX_LON])
        if latlon is None and section == "D" and not line[_FIX_LAT].strip():
            latlon = _parse_latlon(line[_DME_LAT], line[_DME_LON])
        if latlon is None:
            continue
        index[(ident, scope, section, subsection)] = latlon
    return index


def _resolve(index: dict[FixKey, tuple[float, float]], current_airport: str | None,
             ident: str | None, icao: str | None, section: str | None,
             subsection: str | None) -> tuple[float | None, float | None]:
    """Resolve a leg's fix reference. ``current_airport`` is the airport of
    the procedure the leg belongs to (``None`` for airway legs, which have
    no owning airport) -- required to disambiguate airport-scoped fixes;
    see module docstring."""
    if not ident:
        return (None, None)
    subsection = (subsection or "").strip()
    section = section or ""
    scope = current_airport if (section, subsection) in _AIRPORT_SCOPED else icao
    if not scope:
        return (None, None)
    latlon = index.get((ident, scope, section, subsection))
    return latlon if latlon else (None, None)


@dataclass
class AirwayLegRecord:
    route_ident: str
    area: str | None
    seq: int
    fix_id: str
    fix_lat: float | None
    fix_lon: float | None
    fix_type: str | None
    min_alt_ft: int | None
    max_alt_ft: int | None
    direction: str | None
    #: Second Minimum Altitude (cols 89-93): the MEA for travel against
    #: record (sequence) order where it differs from ``min_alt_ft``; blank,
    #: so ``None``, on most legs (AER-5460).
    min_alt2_ft: int | None = None


def iter_airway_legs(path: str | Path,
                      fix_index: dict[FixKey, tuple[float, float]]
                      ) -> Iterator[AirwayLegRecord]:
    """Every Enroute Airways (``ER``) leg in the file, in source order, from
    every Customer/Area Code CIFP carries (US, Canada, Pacific, Latin
    America) -- see module docstring. Each leg carries its own ``area`` so a
    route ident that recurs under more than one area (confirmed live: 73 of
    1,504 idents do) can be told apart downstream -- filtering or re-keying
    by area is a build-time policy choice, not this iterator's job."""
    f = _ER
    for line in read_records(path):
        if not (line[4:5] == "E" and line[5:6] == "R"):
            continue
        if line[f["continuation"]] not in ("0", "1"):
            continue
        route_ident = _strip(line[f["route_ident"]])
        seq_raw = line[f["seq"]].strip()
        fix_id = _strip(line[f["fix_id"]])
        if not route_ident or not seq_raw or not fix_id:
            continue
        fix_icao = _strip(line[f["fix_icao"]])
        fix_section = _strip(line[f["fix_section"]])
        fix_subsection = _strip(line[f["fix_subsection"]])
        lat, lon = _resolve(fix_index, None, fix_id, fix_icao, fix_section, fix_subsection)
        yield AirwayLegRecord(
            route_ident=route_ident,
            area=_strip(line[f["area"]]),
            seq=int(seq_raw),
            fix_id=fix_id,
            fix_lat=lat,
            fix_lon=lon,
            fix_type=_fix_type(fix_section, fix_subsection),
            min_alt_ft=_decode_altitude(line[f["min_alt"]]),
            max_alt_ft=_decode_altitude(line[f["max_alt"]]),
            direction=_strip(line[f["direction"]]),
            min_alt2_ft=_decode_altitude(line[f["min_alt2"]]),
        )


@dataclass
class ProcedureLegRecord:
    airport: str
    kind: str                  # sid | star | approach
    proc_ident: str
    route_type: str
    transition: str | None
    seq: int
    path_term: str
    fix_id: str | None
    fix_lat: float | None
    fix_lon: float | None
    fix_type: str | None
    recd_navaid: str | None
    theta: float | None
    rho: float | None
    course: float | None
    dist_nm: float | None
    time_min: float | None
    alt_desc: str | None
    alt1_ft: int | None
    alt2_ft: int | None
    speed_kt: int | None
    turn_dir: str | None
    rnp: float | None
    flags: int
    centre_fix: str | None
    centre_lat: float | None
    centre_lon: float | None


def iter_procedure_legs(path: str | Path,
                         fix_index: dict[FixKey, tuple[float, float]]
                         ) -> Iterator[ProcedureLegRecord]:
    """Every SID/STAR/Approach (``PD``/``PE``/``PF``) leg, in source order.
    Continuation records (cols 128-132 minimums, notes, etc.) are skipped;
    every leg's own fields are complete on its primary record."""
    f = _PROC
    for line in read_records(path):
        if line[4:5] != "P":
            continue
        subsection = line[f["subsection"]]
        kind = _KIND_BY_SUBSECTION.get(subsection)
        if kind is None:
            continue
        if line[f["continuation"]] not in ("0", "1"):
            continue
        airport = _strip(line[f["airport"]])
        proc_ident = _strip(line[f["ident"]])
        seq_raw = line[f["seq"]].strip()
        path_term = _strip(line[f["path_term"]])
        if not airport or not proc_ident or not seq_raw or not path_term:
            continue
        fix_id = _strip(line[f["fix_id"]])
        fix_icao = _strip(line[f["fix_icao"]])
        fix_section = _strip(line[f["fix_section"]])
        fix_subsection = _strip(line[f["fix_subsection"]])
        lat, lon = _resolve(fix_index, airport, fix_id, fix_icao, fix_section, fix_subsection)
        centre_fix = _strip(line[f["centre_fix"]])
        centre_icao = _strip(line[f["centre_icao"]])
        centre_section = _strip(line[f["centre_section"]])
        centre_subsection = _strip(line[f["centre_subsection"]])
        centre_lat, centre_lon = _resolve(
            fix_index, airport, centre_fix, centre_icao, centre_section, centre_subsection)
        dist_nm, time_min = _decode_dist_or_time(line[f["dist"]])
        yield ProcedureLegRecord(
            airport=airport,
            kind=kind,
            proc_ident=proc_ident,
            route_type=_strip(line[f["route_type"]]) or "",
            transition=_strip(line[f["transition"]]),
            seq=int(seq_raw),
            path_term=path_term,
            fix_id=fix_id,
            fix_lat=lat,
            fix_lon=lon,
            fix_type=_fix_type(fix_section, fix_subsection),
            recd_navaid=_strip(line[f["recd_navaid"]]),
            theta=_decode_tenths(line[f["theta"]]),
            rho=_decode_tenths(line[f["rho"]]),
            course=_decode_tenths(line[f["course"]]),
            dist_nm=dist_nm,
            time_min=time_min,
            alt_desc=_strip(line[f["alt_desc"]]),
            alt1_ft=_decode_altitude(line[f["alt1"]]),
            alt2_ft=_decode_altitude(line[f["alt2"]]),
            speed_kt=_to_int(line[f["speed"]].strip()),
            turn_dir=_strip(line[f["turn_dir"]]),
            rnp=_decode_rnp(line[f["rnp"]]),
            flags=_decode_wdc_flags(line[f["wdc"]], path_term),
            centre_fix=centre_fix,
            centre_lat=centre_lat,
            centre_lon=centre_lon,
        )


# --- AS: Grid MORA record (ARINC 424-17 Sec. 4.1.18.1) --------------------
# Verified against all 241 AS records of live cycle 2611: every one is
# 132 chars with Customer/Area Code blank, a 3-char Starting Latitude
# ("N39") at [13:16], a 4-char Starting Longitude ("W120") at [16:20],
# blanks to col 30, then exactly 30 three-char MORA fields at [30:120]
# (hundreds of feet, or the literal "UNK"), a blank reserved field
# [120:123], the file record number [123:128] and the cycle [128:132].
_AS = dict(
    start_lat=slice(13, 16),
    start_lon=slice(16, 20),
    moras=slice(30, 120),
    cycle=slice(128, 132),
)
MORA_CELLS_PER_BAND = 30
MORA_UNKNOWN = "UNK"


@dataclass
class GridMoraBand:
    """One ``AS`` record: 30 one-degree cells running east along a
    latitude band. ``lat_sw``/``lon_sw`` are the signed south-west corner
    of the first cell; cell ``i`` covers ``[lat_sw, lat_sw+1)`` x
    ``[lon_sw+i, lon_sw+i+1)``. The SW-corner reading was verified on the
    live file against three summits (Whitney, Rainier, Denali), each of
    which lands in the band and column whose value is the highest of its
    neighbours. It is untested for the southern hemisphere: every S-band
    cell in 2611 is ``UNK``.

    ``raw_moras`` holds the 30 fields verbatim; ``moras`` is the decoded
    value in feet, ``None`` where the source says ``UNK``. Nothing here
    ever fills an ``UNK`` -- a MORA nobody published is not one we may
    invent."""
    lat_sw: int
    lon_sw: int
    raw_moras: list[str]
    moras: list[int | None]
    cycle: str | None
    raw: str

    def cells(self) -> Iterator[tuple[int, int, int | None, str]]:
        """``(lat_sw, lon_sw, mora_ft, raw)`` per cell, with longitude
        wrapped into [-180, 180). Defensive: no 2611 band crosses the
        antimeridian (E150 ends at 179, W180 starts at -180)."""
        for i, (ft, raw) in enumerate(zip(self.moras, self.raw_moras)):
            lon = self.lon_sw + i
            if lon >= 180:
                lon -= 360
            yield (self.lat_sw, lon, ft, raw)


def _decode_band_corner(lat_raw: str, lon_raw: str) -> tuple[int, int] | None:
    if (len(lat_raw) != 3 or len(lon_raw) != 4 or lat_raw[0] not in "NS"
            or lon_raw[0] not in "EW" or not lat_raw[1:].isdigit()
            or not lon_raw[1:].isdigit()):
        return None
    lat = int(lat_raw[1:]) * (1 if lat_raw[0] == "N" else -1)
    lon = int(lon_raw[1:]) * (1 if lon_raw[0] == "E" else -1)
    return (lat, lon)


def _decode_mora(raw: str) -> int | None:
    """Hundreds of feet ("130" -> 13,000 ft). ``UNK`` -- or anything else
    that is not three digits -- is ``None``: the gate reports the count,
    the table never guesses."""
    return int(raw) * 100 if raw.isdigit() else None


def iter_grid_mora(path: str | Path) -> Iterator[GridMoraBand]:
    """Every Grid MORA (``AS``) band in the file, in source order. A record
    whose corner does not decode is skipped rather than placed somewhere
    wrong; the publish gate's cell-count check (30 per band) is what would
    notice a band lost that way."""
    f = _AS
    for line in read_records(path):
        if line[4:6] != "AS":
            continue
        corner = _decode_band_corner(line[f["start_lat"]], line[f["start_lon"]])
        if corner is None:
            continue
        field = line[f["moras"]]
        raw_moras = [field[3 * i:3 * i + 3] for i in range(MORA_CELLS_PER_BAND)]
        yield GridMoraBand(
            lat_sw=corner[0],
            lon_sw=corner[1],
            raw_moras=raw_moras,
            moras=[_decode_mora(r) for r in raw_moras],
            cycle=_strip(line[f["cycle"]]),
            raw=line,
        )


# --- UC/UR: Controlled and Restrictive Airspace (Sec. 4.1.25 / 4.1.18) ----
# Offsets verified against FAACIFP18 cycle 2611 (KSBA Class C, KLAX Class B,
# R-2508, BIRCH MOA). The two sections share every position except cols
# 10-19: UC splits them into the airspace centre (10-14) plus the centre's
# own section/subsection (15-16) and the airspace classification (17); UR
# holds a single 10-char restrictive airspace designation there.
_ASP = dict(
    area=slice(1, 4),
    subsection=slice(5, 6),            # C = controlled, R = restrictive
    icao=slice(6, 8),
    airspace_type=slice(8, 9),         # UC: T/A/Z (Class B/C/D); UR: R/P/M/W/A/U...
    multiple_code=slice(19, 20),       # sector letter within one airspace
    seq=slice(20, 24),
    continuation=slice(24, 25),
    level=slice(25, 26),               # UR: B/H/L (all/high/low); blank on UC
    time_code=slice(26, 27),
    notam=slice(27, 28),
    boundary_via=slice(30, 32),        # G/H/L/R/C + E on the closing record
    lat=slice(32, 41),
    lon=slice(41, 51),
    arc_origin_lat=slice(51, 60),
    arc_origin_lon=slice(60, 70),
    arc_distance=slice(70, 74),        # NM, 1 implied decimal
    arc_bearing=slice(74, 78),         # true, origin -> this point, 1 implied decimal
    rnp=slice(78, 81),
    lower_limit=slice(81, 86),
    lower_unit=slice(86, 87),          # A = AGL, M = MSL
    upper_limit=slice(87, 92),
    upper_unit=slice(92, 93),
    name=slice(93, 123),
    file_record=slice(124, 128),
    cycle=slice(128, 132),
)
_UC_CENTRE = slice(9, 14)
_UC_CENTRE_SECTION = slice(14, 15)
_UC_CENTRE_SUBSECTION = slice(15, 16)
_UC_CLASS = slice(16, 17)
_UR_DESIGNATION = slice(9, 19)
# UR continuation record, application type "C" (call sign / controlling
# agency): the agency sits where the primary record carries the name. The
# only application type seen in 2611 (1,176 of 1,176 continuations).
_UR_CONT_APPLICATION = slice(25, 26)
_UR_CONT_AGENCY = slice(93, 123)


@dataclass
class AirspaceRecord:
    """One ``UC``/``UR`` primary record -- one boundary vertex of one
    sector -- with every field as read (blank fields as ``None``). The sector
    key is :attr:`sector_key`; a sector's records arrive contiguous and in
    sequence order, its first carrying the name and the vertical limits and
    its last carrying a boundary-via ending in ``E``."""
    section: str                # "UC" | "UR"
    area: str | None
    icao: str
    airspace_type: str
    designation: str            # UC: airspace centre; UR: restrictive designation
    centre_section: str | None  # UC only
    centre_subsection: str | None
    classification: str | None  # UC only: B/C/D
    multiple_code: str | None
    seq: int
    continuation: str
    level: str | None
    time_code: str | None
    notam: str | None
    boundary_via: str
    lat: float | None
    lon: float | None
    arc_origin_lat: float | None
    arc_origin_lon: float | None
    arc_distance_nm: float | None
    arc_bearing: float | None
    rnp: float | None
    lower_limit: str | None
    lower_unit: str | None
    upper_limit: str | None
    upper_unit: str | None
    name: str | None
    file_record: str | None
    cycle: str | None
    raw: str
    #: UR only: the controlling agency from the record's "C" continuation,
    #: attached to the primary it continues.
    controlling_agency: str | None = None

    @property
    def sector_key(self) -> tuple:
        return (self.section, self.icao, self.airspace_type, self.designation,
                self.multiple_code or "")


def iter_airspace_records(path: str | Path) -> Iterator[AirspaceRecord]:
    """Every Controlled (``UC``) and Restrictive (``UR``) airspace primary
    record, in source order, from every Customer/Area Code -- the FAA files
    Alaska under ``CAN``, Hawaii and Guam under ``PAC``, Puerto Rico and the
    USVI under ``LAM`` (2611). A ``UR`` "C" continuation is folded into the
    primary it follows as :attr:`AirspaceRecord.controlling_agency`; any other
    continuation application type is unknown here and raises rather than
    being dropped."""
    f = _ASP
    pending: AirspaceRecord | None = None
    for line in read_records(path):
        if line[4:5] != "U" or line[f["subsection"]] not in ("C", "R"):
            continue
        cont = line[f["continuation"]]
        if cont not in ("0", "1"):
            app = line[_UR_CONT_APPLICATION]
            if pending is None or app != "C":
                raise ValueError(f"unhandled airspace continuation (application type "
                                 f"{app!r}): {line!r}")
            pending.controlling_agency = _strip(line[_UR_CONT_AGENCY])
            continue
        if pending is not None:
            yield pending
        sub = line[f["subsection"]]
        if sub == "C":
            designation = line[_UC_CENTRE]
            centre_section = _strip(line[_UC_CENTRE_SECTION])
            centre_subsection = _strip(line[_UC_CENTRE_SUBSECTION])
            classification = _strip(line[_UC_CLASS])
        else:
            designation = line[_UR_DESIGNATION]
            centre_section = centre_subsection = classification = None
        latlon = _parse_latlon(line[f["lat"]], line[f["lon"]])
        origin = _parse_latlon(line[f["arc_origin_lat"]], line[f["arc_origin_lon"]])
        pending = AirspaceRecord(
            section="U" + sub,
            area=_strip(line[f["area"]]),
            icao=line[f["icao"]].strip(),
            airspace_type=line[f["airspace_type"]].strip(),
            designation=designation.strip(),
            centre_section=centre_section,
            centre_subsection=centre_subsection,
            classification=classification,
            multiple_code=_strip(line[f["multiple_code"]]),
            seq=int(line[f["seq"]]),
            continuation=cont,
            level=_strip(line[f["level"]]),
            time_code=_strip(line[f["time_code"]]),
            notam=_strip(line[f["notam"]]),
            boundary_via=line[f["boundary_via"]].strip(),
            lat=latlon[0] if latlon else None,
            lon=latlon[1] if latlon else None,
            arc_origin_lat=origin[0] if origin else None,
            arc_origin_lon=origin[1] if origin else None,
            arc_distance_nm=_decode_tenths(line[f["arc_distance"]]),
            arc_bearing=_decode_tenths(line[f["arc_bearing"]]),
            rnp=_decode_rnp(line[f["rnp"]]),
            lower_limit=_strip(line[f["lower_limit"]]),
            lower_unit=_strip(line[f["lower_unit"]]),
            upper_limit=_strip(line[f["upper_limit"]]),
            upper_unit=_strip(line[f["upper_unit"]]),
            name=_strip(line[f["name"]]),
            file_record=_strip(line[f["file_record"]]),
            cycle=_strip(line[f["cycle"]]),
            raw=line,
        )
    if pending is not None:
        yield pending


def transition_role(kind: str, route_type: str, transition: str | None) -> str:
    """Best-effort transition role classifier (brief Sec. 3.3's
    ``transitions.role``): ARINC's Route Type codes are approach-type
    dependent and only partially standardized across SID/STAR/Approach, so
    this leans on the one unambiguous structural signal -- whether the
    transition is runway-specific -- rather than decoding Route Type
    itself."""
    if transition and transition.upper().startswith("RW"):
        return "runway"
    if not transition:
        return "common"
    return "enroute"


@dataclass
class MsaSector:
    seq: int                   # 1-based position in the record
    bearing_start: int | None  # degrees, bearing to the centre, clockwise start
    bearing_end: int | None
    altitude_ft: int | None
    radius_nm: int | None
    raw: str                   # the 11 characters as read


@dataclass
class MsaRecord:
    area: str | None
    airport: str
    airport_icao: str | None
    centre_ident: str
    centre_icao: str | None
    centre_section: str | None
    centre_subsection: str | None
    centre_type: str | None
    centre_lat: float | None
    centre_lon: float | None
    multiple_code: str | None
    mag_true: str | None
    sectors: list[MsaSector]
    fields: dict[str, str]     # every named field as read, unstripped
    continuations: list[str]   # continuation records, verbatim


def _msa_sector(seq: int, raw: str) -> MsaSector:
    hundreds = _to_int(raw[6:9])
    return MsaSector(seq=seq, bearing_start=_to_int(raw[0:3]), bearing_end=_to_int(raw[3:6]),
                     altitude_ft=hundreds * 100 if hundreds is not None else None,
                     radius_nm=_to_int(raw[9:11]), raw=raw)


def iter_msa(path: str | Path, fix_index: dict[FixKey, tuple[float, float]]
             ) -> Iterator[MsaRecord]:
    """Every Airport MSA (``PS``) record, in source order, from every
    Customer/Area Code (Alaska is coded under ``CAN``, so filtering by area
    would drop it). The centre fix resolves against its own airport for the
    airport-scoped types (runway, terminal waypoint, airport, terminal NDB)
    and against its ICAO region otherwise -- the same rule as procedure
    legs. A centre that does not resolve keeps ``NULL`` coordinates.

    Continuation records (continuation number above 1) are kept verbatim on
    the primary they follow; the 2611 cycle has none, so nothing here
    decodes them."""
    f = _MSA
    current: MsaRecord | None = None
    for line in read_records(path):
        if not (line[4:5] == "P" and line[12:13] == "S"):
            continue
        if line[f["continuation"]] not in ("0", "1"):
            if current is not None:
                current.continuations.append(line)
            continue
        if current is not None:
            yield current
            current = None
        airport = _strip(line[f["airport"]])
        centre_ident = _strip(line[f["centre_ident"]])
        if not airport or not centre_ident:
            continue
        centre_icao = _strip(line[f["centre_icao"]])
        centre_section = _strip(line[f["centre_section"]])
        centre_subsection = _strip(line[f["centre_subsection"]])
        lat, lon = _resolve(fix_index, airport, centre_ident, centre_icao,
                            centre_section, centre_subsection)
        block = line[f["sectors"]]
        sectors = []
        for i in range(_MSA_SECTORS):
            raw = block[i * _MSA_SECTOR_WIDTH:(i + 1) * _MSA_SECTOR_WIDTH]
            if raw.strip():
                sectors.append(_msa_sector(i + 1, raw))
        current = MsaRecord(
            area=_strip(line[f["area"]]),
            airport=airport,
            airport_icao=_strip(line[f["airport_icao"]]),
            centre_ident=centre_ident,
            centre_icao=centre_icao,
            centre_section=centre_section,
            centre_subsection=centre_subsection,
            centre_type=_fix_type(centre_section, centre_subsection),
            centre_lat=lat,
            centre_lon=lon,
            multiple_code=_strip(line[f["multiple_code"]]),
            mag_true=_strip(line[f["mag_true"]]),
            sectors=sectors,
            fields={k: line[v] for k, v in f.items()},
            continuations=[],
        )
    if current is not None:
        yield current


# --- PP: Airport Path Point (FAS data block) records (Sec. 4.1.28) -------
# Every field below is checked against the live 2611 cycle, where all 4,917
# primaries re-compute to their published CRC (see ``fas_crc``), and against
# the FAA Form 8260-3 "FAS DATA BLOCK INFORMATION" table for KUSE RWY 27.
_PP = dict(
    area=slice(1, 4),
    airport=slice(6, 10),
    airport_icao=slice(10, 12),
    approach_ident=slice(13, 19),
    runway=slice(19, 24),              # "RW28 "; Note 3: "RW00" + blank letter for a helipad
    operation_type=slice(24, 26),      # 00 = straight-in (5.223)
    continuation=slice(26, 27),
    route_indicator=slice(27, 28),     # blank, or A-Z: one of several FAS to one runway (5.224)
    sbas_provider=slice(28, 30),       # 00 = WAAS (5.255)
    ref_path_data_selector=slice(30, 32),
    ref_path_ident=slice(32, 36),      # "W28A" (5.257)
    apd=slice(36, 37),                 # Approach Performance Designator (5.258)
    ltp_lat=slice(37, 48),             # high precision, ten-thousandths of a second (5.267)
    ltp_lon=slice(48, 60),
    ltp_ellipsoid_height=slice(60, 66),  # tenths of a metre, signed (5.225)
    glidepath_angle=slice(66, 70),     # hundredths of a degree (5.226)
    fpap_lat=slice(70, 81),
    fpap_lon=slice(81, 93),
    course_width=slice(93, 98),        # hundredths of a metre (5.228)
    length_offset=slice(98, 102),      # metres, 8 m resolution (5.259)
    tch=slice(102, 108),               # tenths of a foot or hundredths of a metre (5.265)
    tch_units=slice(108, 109),         # F or M (5.266)
    hal=slice(109, 112),               # tenths of a metre (5.263)
    val=slice(112, 115),               # tenths of a metre (5.264)
    crc=slice(115, 123),               # 8 hex digits (5.229)
    file_record_no=slice(123, 128),
    cycle=slice(128, 132),
)
#: The PP continuation (continuation number 2, Application Type ``E``).
#: Not in the CRC wrap. The FAA leaves the FPAP ellipsoid height blank.
_PP_CONT = dict(
    application_type=slice(27, 28),
    fpap_ellipsoid_height=slice(28, 34),
    fpap_orthometric_height=slice(34, 40),
    ltp_orthometric_height=slice(40, 46),
    approach_type_ident=slice(46, 56),   # "LPV", "LP", "GLS" (5.262)
    gnss_channel=slice(56, 61),          # 40000-99999 SBAS, 20001-39999 GBAS (5.244)
    helicopter_course=slice(71, 74),     # 5.269
)

#: CRC-32Q, ARINC 424 Sec. 6.10.2: x^32+x^31+x^24+x^22+x^16+x^14+x^8+x^7+x^5+x^3+x+1.
CRC32Q_POLY = 0x814141AB
_RUNWAY_LETTER_CODE = {" ": 0, "R": 1, "C": 2, "L": 3}


def _parse_hp_seconds(raw: str, deg_digits: int) -> int | None:
    """A High Precision Latitude/Longitude (5.267/5.268) as a signed count of
    ten-thousandths of an arc-second: "N2920080300" -> +(29 deg 20' 08.0300")."""
    raw = raw.strip()
    if len(raw) != 1 + deg_digits + 2 + 6 or raw[0] not in "NSEW":
        return None
    try:
        deg = int(raw[1:1 + deg_digits])
        mins = int(raw[1 + deg_digits:3 + deg_digits])
        frac = int(raw[3 + deg_digits:])
    except ValueError:
        return None
    v = (deg * 3600 + mins * 60) * 10000 + frac
    return -v if raw[0] in "SW" else v


def _hp_degrees(v: int | None) -> float | None:
    return None if v is None else round(v / 36_000_000.0, 10)


def _signed_tenths(raw: str) -> float | None:
    raw = raw.strip()
    if len(raw) < 2 or raw[0] not in "+-":
        return None
    n = _to_int(raw[1:])
    if n is None:
        return None
    return (-n if raw[0] == "-" else n) / 10.0


def _fas_step(v: int) -> int:
    """Ten-thousandths of an arc-second -> the FAS block's 0.0005 arc-second
    step, rounded to the nearest (a count of steps is a multiple of 0.2, so
    there is never a tie). The record's value is not always on that grid:
    PAMR R08-Y's LTP longitude (2611, 2612) ends ...7784, and only ...7785
    reproduces its published CRC. That is the one off-grid value in 2605,
    2611 and 2612, and it is west (negative), so it cannot tell "nearest"
    from "away from zero" or a signed floor. If the FAA's rule differs on a
    later value, that block fails its CRC and is dropped and named, never
    shipped."""
    q, r = divmod(v, 5)
    return q + (1 if r >= 3 else 0)


def _fas_block_bits(fields: dict[str, str]) -> list[tuple[int, int]] | None:
    """The 288-bit SBAS FAS data block (RTCA DO-229, Appendix D) the CRC
    wraps, as (value, width) pairs in transmission order; ``None`` when a
    field in the wrap does not parse. Rules, each proved by the 2611 cycle
    (every primary's CRC re-computes; change any one and none do):

    - every field goes least significant bit first;
    - Airport ID and Reference Path ID are four characters of six bits
      (IA-5 b1-b6) plus two zero bits, sent *rightmost character first*;
    - latitudes/longitudes are two's complement in 0.0005 arc-seconds,
      rounded to the nearest step (``_fas_step``); the FPAP goes as its
      difference from the LTP, in 24 bits;
    - LTP height is (height + 512 m) in 0.1 m; TCH is 0.1 ft or 0.05 m;
      course width is (width - 80 m) in 0.25 m; length offset is in 8 m,
      255 when the source leaves it blank; HAL and VAL are in 0.2 m.
    """
    try:
        def chars(s: str) -> list[tuple[int, int]]:
            return [(ord(c) & 0x3F, 8) for c in s[::-1]]

        rwy = fields["runway"]
        if not rwy.startswith("RW"):
            return None
        rwy_letter = _RUNWAY_LETTER_CODE[rwy[4]]
        ri = fields["route_indicator"]
        route = 0 if ri == " " else ord(ri) - ord("A") + 1
        lat = _parse_hp_seconds(fields["ltp_lat"], 2)
        lon = _parse_hp_seconds(fields["ltp_lon"], 3)
        flat = _parse_hp_seconds(fields["fpap_lat"], 2)
        flon = _parse_hp_seconds(fields["fpap_lon"], 3)
        if None in (lat, lon, flat, flon):
            return None
        lat, lon, flat, flon = (_fas_step(v) for v in (lat, lon, flat, flon))
        height = fields["ltp_ellipsoid_height"]
        h = int(height[1:]) * (-1 if height[0] == "-" else 1)
        tch = int(fields["tch"])
        units = fields["tch_units"]
        if units not in ("F", "M"):
            return None
        offset = fields["length_offset"].strip()
        return [
            (int(fields["operation_type"]), 4),
            (int(fields["sbas_provider"]), 4),
            *chars(fields["airport"]),
            (int(rwy[2:4]), 6), (rwy_letter, 2),
            (int(fields["apd"]), 3), (route, 5),
            (int(fields["ref_path_data_selector"]), 8),
            *chars(fields["ref_path_ident"]),
            (lat & 0xFFFFFFFF, 32), (lon & 0xFFFFFFFF, 32),
            (h + 5120, 16),
            ((flat - lat) & 0xFFFFFF, 24), ((flon - lon) & 0xFFFFFF, 24),
            (tch if units == "F" else tch // 5, 15), (0 if units == "F" else 1, 1),
            (int(fields["glidepath_angle"]), 16),
            ((int(fields["course_width"]) - 8000) // 25, 8),
            (int(offset) // 8 if offset else 255, 8),
            (int(fields["hal"]) // 2, 8), (int(fields["val"]) // 2, 8),
        ]
    except (KeyError, ValueError, IndexError):
        return None


def fas_crc(fields: dict[str, str]) -> str | None:
    """The CRC-32Q of a PP record's FAS data block, as the 8 hex digits the
    record carries in cols 116-123, or ``None`` if the block does not
    encode. The remainder is taken MSB-first over the transmitted bit
    stream (Sec. 6.10.3: x^31 is the first remainder bit), then written the
    way the 40-byte block is sent: four bytes, each least significant bit
    first -- so each byte's bits read reversed against the register."""
    packed = _fas_block_bits(fields)
    if packed is None:
        return None
    reg = 0
    for value, width in packed:
        for i in range(width):
            top = (reg >> 31) & 1
            reg = ((reg << 1) & 0xFFFFFFFF) | ((value >> i) & 1)
            if top:
                reg ^= CRC32Q_POLY
    for _ in range(32):
        top = (reg >> 31) & 1
        reg = (reg << 1) & 0xFFFFFFFF
        if top:
            reg ^= CRC32Q_POLY
    out = bytes(int(f"{b:08b}"[::-1], 2) for b in reg.to_bytes(4, "big"))
    return out.hex().upper()


@dataclass
class FasBlock:
    area: str | None
    airport: str
    airport_icao: str | None
    approach_ident: str
    runway: str
    operation_type: int | None
    route_indicator: str | None
    sbas_provider: int | None
    ref_path_data_selector: int | None
    ref_path_ident: str | None
    apd: int | None
    ltp_lat: float | None
    ltp_lon: float | None
    ltp_ellipsoid_height_m: float | None
    glidepath_angle_deg: float | None
    fpap_lat: float | None
    fpap_lon: float | None
    course_width_m: float | None
    length_offset_m: int | None        # None where the source leaves it blank
    tch: float | None                  # in tch_units: feet or metres
    tch_units: str | None
    hal_m: float | None
    val_m: float | None
    crc: str                           # as published, cols 116-123
    crc_computed: str | None           # None when the block would not encode
    fpap_ellipsoid_height_m: float | None
    fpap_orthometric_height_m: float | None
    ltp_orthometric_height_m: float | None
    approach_type_ident: str | None
    gnss_channel: int | None
    helicopter_course: int | None
    fields: dict[str, str]             # every primary field as read, unstripped
    continuations: list[str]           # continuation records, verbatim
    key: str                           # cols 7-26, shared by the primary and its continuations

    @property
    def crc_ok(self) -> bool:
        return self.crc_computed is not None and self.crc_computed == self.crc.upper()

    @property
    def label(self) -> str:
        """How the build report names a block: airport, approach, runway."""
        return f"{self.airport} {self.approach_ident.strip()} {self.runway.strip()}"


def _fas_block(line: str) -> FasBlock:
    f = {k: line[v] for k, v in _PP.items()}
    tch = _to_int(f["tch"])
    units = _strip(f["tch_units"])
    if tch is not None and units == "F":
        tch_v = tch / 10.0
    elif tch is not None and units == "M":
        tch_v = tch / 100.0
    else:
        tch_v = None
    cw, hal, val = _to_int(f["course_width"]), _to_int(f["hal"]), _to_int(f["val"])
    gpa = _to_int(f["glidepath_angle"])
    return FasBlock(
        area=_strip(f["area"]), airport=f["airport"].strip(),
        airport_icao=_strip(f["airport_icao"]),
        approach_ident=f["approach_ident"].strip(), runway=f["runway"].strip(),
        operation_type=_to_int(f["operation_type"]),
        route_indicator=_strip(f["route_indicator"]),
        sbas_provider=_to_int(f["sbas_provider"]),
        ref_path_data_selector=_to_int(f["ref_path_data_selector"]),
        ref_path_ident=_strip(f["ref_path_ident"]), apd=_to_int(f["apd"]),
        ltp_lat=_hp_degrees(_parse_hp_seconds(f["ltp_lat"], 2)),
        ltp_lon=_hp_degrees(_parse_hp_seconds(f["ltp_lon"], 3)),
        ltp_ellipsoid_height_m=_signed_tenths(f["ltp_ellipsoid_height"]),
        glidepath_angle_deg=gpa / 100.0 if gpa is not None else None,
        fpap_lat=_hp_degrees(_parse_hp_seconds(f["fpap_lat"], 2)),
        fpap_lon=_hp_degrees(_parse_hp_seconds(f["fpap_lon"], 3)),
        course_width_m=cw / 100.0 if cw is not None else None,
        length_offset_m=_to_int(f["length_offset"]) if f["length_offset"].strip() else None,
        tch=tch_v, tch_units=units,
        hal_m=hal / 10.0 if hal is not None else None,
        val_m=val / 10.0 if val is not None else None,
        crc=f["crc"].strip().upper(), crc_computed=fas_crc(f),
        fpap_ellipsoid_height_m=None, fpap_orthometric_height_m=None,
        ltp_orthometric_height_m=None, approach_type_ident=None, gnss_channel=None,
        helicopter_course=None, fields=f, continuations=[], key=line[6:26],
    )


def _apply_pp_continuation(block: FasBlock, line: str) -> None:
    block.continuations.append(line)
    c = {k: line[v] for k, v in _PP_CONT.items()}
    if line[26:27] != "2" or c["application_type"] != "E":
        return
    block.fpap_ellipsoid_height_m = _signed_tenths(c["fpap_ellipsoid_height"])
    block.fpap_orthometric_height_m = _signed_tenths(c["fpap_orthometric_height"])
    block.ltp_orthometric_height_m = _signed_tenths(c["ltp_orthometric_height"])
    block.approach_type_ident = _strip(c["approach_type_ident"])
    block.gnss_channel = _to_int(c["gnss_channel"]) if c["gnss_channel"].strip() else None
    block.helicopter_course = (_to_int(c["helicopter_course"])
                               if c["helicopter_course"].strip() else None)


def iter_path_points(path: str | Path) -> Iterator[FasBlock]:
    """Every Path Point (``PP``) record, in source order, from every
    Customer/Area Code, with its continuations attached. Each block carries
    the CRC the record publishes and the one re-computed from its fields;
    whether a mismatch is kept is the builder's call, not this reader's.
    A continuation with no primary before it (none in 2611) is skipped."""
    current: FasBlock | None = None
    for line in read_records(path):
        if not (line[4:5] == "P" and line[12:13] == "P"):
            continue
        if line[_PP["continuation"]] not in ("0", "1"):
            if current is not None and line[6:26] == current.key:
                _apply_pp_continuation(current, line)
            continue
        if current is not None:
            yield current
        current = _fas_block(line)
    if current is not None:
        yield current
