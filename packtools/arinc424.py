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
    min_alt2=slice(88, 93),            # reciprocal-direction MEA; unused here
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
    if wdc[3] in ("F", "I"):
        flags |= FLAG_FAF
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
