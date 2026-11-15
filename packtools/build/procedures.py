# SPDX-License-Identifier: Apache-2.0
"""ARINC 424 CIFP -> sqlite ``procedures`` pack (AER-1600 / PA1).

Builds nine tables from one FAACIFP18-format file: ``airways`` +
``airway_legs`` (Enroute Airways, ``ER``), ``procedures`` +
``transitions`` + ``legs`` (SID/STAR/Approach, ``PD``/``PE``/``PF``),
``msa`` (Airport MSA, ``PS``; AER-4446, additive -- see ``_build_msa``) and
``grid_mora_bands`` + ``grid_mora`` (Grid MORA, ``AS``; AER-4447, additive) and
``fas_blocks`` (Final Approach Segment data blocks, ``PP``; AER-5623, additive,
CRC-verified -- see ``_build_fas_blocks`` and docs/fas_blocks.md).
Each table addition moves schema_version, see ``packmeta.PROCEDURES_TABLE_SCHEMAS``.
A new column does not (readers select by name; the published format rule):
``airway_legs.min_alt2_ft`` (AER-5460) is the ER record's second Minimum
Altitude, the MEA for travel against ``seq`` order, NULL where the record
leaves it blank -- and absent from packs built before it.
Schema is brief section 3.3/3.4's, with one addition: ``idx_awy_ident`` is a
UNIQUE index (AER-1979) so a route ident colliding across areas fails the
build loudly instead of silently merging two airways into one row -- see
``_AREA`` below for the area-filtering policy that is the primary fix.

Airways are built and committed first, deliberately -- brief section 3.4:
"PA1's definition of done is staged so the airway table ships before
procedure parsing is finished," because PA2 (airway lookup) depends only
on the airway slice and should not wait on procedure parsing to land.
Both happen in this one pass because both are ready together, but the
ordering (and the fact that airway parsing does not import anything from
the procedure-parsing path) is preserved so the two could still ship as
separate commits if that were ever needed again.

Distinct from the registered-but-deferred ``cifp`` pack kind
(``sources.py`` "cifp-conus"): that kind names the pyAvTools spatial index
shape (Airport/Runway/Navaid/RouteNode) VirtualVfr consumes, which this
does not replicate. ``procedures`` is a new, unrelated kind -- see brief
decision 2.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from .. import arinc424
from ..geo import great_circle_nm
from .errors import BuildError

_SCHEMA = """
CREATE TABLE airways (
    id      INTEGER PRIMARY KEY,
    ident   TEXT NOT NULL,
    cycle   TEXT NOT NULL
);
CREATE TABLE airway_legs (
    airway_id   INTEGER NOT NULL REFERENCES airways(id),
    seq         INTEGER NOT NULL,
    fix_id      TEXT NOT NULL,
    fix_lat     REAL,
    fix_lon     REAL,
    fix_type    TEXT,
    min_alt_ft  INTEGER,
    max_alt_ft  INTEGER,
    direction   TEXT,
    min_alt2_ft INTEGER   -- MEA against seq order; NULL = same as min_alt_ft
);
CREATE UNIQUE INDEX idx_awy_ident ON airways(ident);
CREATE INDEX idx_awyleg_awy ON airway_legs(airway_id, seq);
CREATE INDEX idx_awyleg_fix ON airway_legs(fix_id);

CREATE TABLE procedures (
    id              INTEGER PRIMARY KEY,
    airport         TEXT NOT NULL,
    kind            TEXT NOT NULL,   -- sid | star | approach
    ident           TEXT NOT NULL,
    runway          TEXT,
    approach_type   TEXT,
    rnp             REAL,
    cycle           TEXT NOT NULL
);
CREATE TABLE transitions (
    id          INTEGER PRIMARY KEY,
    proc_id     INTEGER NOT NULL REFERENCES procedures(id),
    role        TEXT NOT NULL,        -- enroute | runway | common
    ident       TEXT
);
CREATE TABLE legs (
    transition_id   INTEGER NOT NULL REFERENCES transitions(id),
    seq             INTEGER NOT NULL,
    path_term       TEXT NOT NULL,
    fix_id          TEXT,
    fix_lat         REAL,
    fix_lon         REAL,
    fix_type        TEXT,
    recd_navaid     TEXT,
    theta           REAL,
    rho             REAL,
    course          REAL,
    dist_nm         REAL,
    time_min        REAL,
    alt_desc        TEXT,
    alt1_ft         INTEGER,
    alt2_ft         INTEGER,
    speed_kt        INTEGER,
    turn_dir        TEXT,
    rnp             REAL,
    flags           INTEGER NOT NULL,
    centre_fix      TEXT,       -- RF legs: the arc centre. Other legs: the procedure's MSA centre
                                -- (one leg per procedure carries it; joins msa on airport + centre_fix)
    centre_lat      REAL,
    centre_lon      REAL,
    arc_radius_nm   REAL        -- RF legs only: great_circle(centre, fix); convenience for the renderer
);
CREATE INDEX idx_proc_airport ON procedures(airport, kind);
CREATE INDEX idx_trans_proc ON transitions(proc_id);
CREATE INDEX idx_legs_trans ON legs(transition_id, seq);

CREATE TABLE grid_mora_bands (
    id      INTEGER PRIMARY KEY,
    lat_sw  INTEGER NOT NULL,
    lon_sw  INTEGER NOT NULL,
    cycle   TEXT,            -- the record's own last-revised cycle stamp
    raw     TEXT NOT NULL    -- the 132-char AS record, verbatim
);
CREATE TABLE grid_mora (
    band_id INTEGER NOT NULL REFERENCES grid_mora_bands(id),
    lat_sw  INTEGER NOT NULL,
    lon_sw  INTEGER NOT NULL,
    mora_ft INTEGER,         -- NULL where the source says UNK; never filled
    raw     TEXT NOT NULL    -- the 3-char field as read ("130", "UNK")
);
CREATE UNIQUE INDEX idx_mora_cell ON grid_mora(lat_sw, lon_sw);
CREATE TABLE msa (
    airport             TEXT NOT NULL,
    area                TEXT,          -- Customer/Area Code; Alaska is coded CAN
    centre_ident        TEXT NOT NULL,
    centre_icao         TEXT,
    centre_section      TEXT,
    centre_subsection   TEXT,
    centre_type         TEXT,          -- runway | vor | ndb | waypoint | airport
    centre_lat          REAL,          -- NULL when the centre does not resolve
    centre_lon          REAL,
    multiple_code       TEXT,          -- tells apart two MSAs on one centre
    mag_true            TEXT,          -- M = bearings magnetic, T = true
    sector_seq          INTEGER NOT NULL,
    bearing_start       INTEGER,       -- bearing TO the centre, degrees; the sector runs
    bearing_end         INTEGER,       -- clockwise start -> end; start == end is the full circle
    altitude_ft         INTEGER,
    radius_nm           INTEGER,
    cycle               TEXT NOT NULL,
    record              TEXT NOT NULL  -- JSON: every field as read, plus continuation records
);
CREATE INDEX idx_msa_airport ON msa(airport, centre_ident);
CREATE TABLE fas_blocks (
    id                          INTEGER PRIMARY KEY,
    proc_id                     INTEGER REFERENCES procedures(id),  -- the approach; NULL if the airport has no approach of this ident
    airport                     TEXT NOT NULL,
    area                        TEXT,          -- Customer/Area Code; Alaska is coded CAN
    approach_ident              TEXT NOT NULL, -- procedures.ident, e.g. "R28", "R08-Y"
    runway                      TEXT NOT NULL, -- "RW28"; "RW00" is a helipad or point in space
    route_indicator             TEXT,          -- tells apart two FAS to one runway; NULL = the only one
    approach_type               TEXT,          -- service level the block is for: LPV or LP (GLS in principle)
    operation_type              INTEGER,       -- 0 = straight-in
    sbas_provider               INTEGER,       -- 0 = WAAS
    ref_path_data_selector      INTEGER,
    ref_path_ident              TEXT,          -- e.g. "W28A"; the pilot confirms the approach by it
    apd                         INTEGER,       -- Approach Performance Designator
    ltp_lat                     REAL,          -- Landing Threshold Point, WGS-84 degrees
    ltp_lon                     REAL,
    ltp_ellipsoid_height_m      REAL,
    ltp_orthometric_height_m    REAL,
    fpap_lat                    REAL,          -- Flight Path Alignment Point, WGS-84 degrees
    fpap_lon                    REAL,
    fpap_ellipsoid_height_m     REAL,
    fpap_orthometric_height_m   REAL,
    glidepath_angle_deg         REAL,
    tch                         REAL,          -- threshold crossing height, in tch_units
    tch_units                   TEXT,          -- F = feet, M = metres
    course_width_m              REAL,          -- full-scale lateral width at the LTP
    length_offset_m             INTEGER,       -- stop end of runway to FPAP; NULL where the source leaves it blank
    hal_m                       REAL,          -- Horizontal Alert Limit
    val_m                       REAL,          -- Vertical Alert Limit; 0 on an LP block (no vertical)
    gnss_channel                INTEGER,
    helicopter_course           INTEGER,
    crc                         TEXT NOT NULL, -- FAS block CRC-32Q as published; re-computed and matched at build
    cycle                       TEXT NOT NULL,
    record                      TEXT NOT NULL  -- JSON: every primary field as read, plus continuation records
);
CREATE INDEX idx_fas_airport ON fas_blocks(airport, approach_ident);
CREATE INDEX idx_fas_proc ON fas_blocks(proc_id);
"""


class ProceduresBuildError(BuildError):
    """A refusal (bad or empty input). A BuildError, so run_cyclical
    fails the run rather than logging it as a benign WARN."""


#: Fix references the source file names but never defines, each checked by
#: hand against the whole file. Keyed ``(ident, icao, section+subsection)``
#: as the leg codes it. A leg naming one keeps a NULL position; a leg whose
#: fix does not resolve for any other reason stops the build (AER-5612).
#: An entry that starts resolving also stops the build, so the list cannot
#: go stale silently.
KNOWN_SOURCE_GAPS: dict[tuple[str, str, str], str] = {
    ("CMY", "K5", "DB"): "NDB named by the KDAF R36 IF leg; CIFP 2611 and 2612 "
                         "carry no D-section record for it",
}

#: How many unresolved references a refusal names before summarising.
_UNRESOLVED_SHOWN = 20


def _check_unresolved(unresolved: dict[tuple[str, str, str], list[str]],
                      fix_index, cifp_path: Path) -> None:
    """Refuse the build if any leg names a fix it could not place, other
    than a declared source gap. Before AER-5612 such legs shipped with NULL
    coordinates; pyEfis refuses a procedure with one, so 556 procedures in
    the 2611 pack could not be loaded."""
    stale = sorted(
        "/".join(k) for k in KNOWN_SOURCE_GAPS
        if (k[0], k[1], k[2][:1], k[2][1:]) in fix_index)
    if stale:
        raise ProceduresBuildError(
            f"{cifp_path} now defines {', '.join(stale)}, listed in "
            f"KNOWN_SOURCE_GAPS as missing -- remove the entry.")
    bad = {k: v for k, v in unresolved.items() if k not in KNOWN_SOURCE_GAPS}
    if not bad:
        return
    lines = [f"  {'/'.join(k)}: {len(v)} leg(s), e.g. {v[0]}"
             for k, v in sorted(bad.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    more = len(lines) - _UNRESOLVED_SHOWN
    raise ProceduresBuildError(
        f"{len(bad)} fix reference(s) in {cifp_path} did not resolve to a "
        f"position (ident/icao-or-airport/section):\n"
        + "\n".join(lines[:_UNRESOLVED_SHOWN])
        + (f"\n  ... and {more} more" if more > 0 else ""))


def _find_cifp_file(input_dir: Path) -> Path:
    # The FAA has shipped this file under this exact name for decades
    # (verified live, cycle 2609); a glob would also happily match one of
    # the zip's PDF/xlsx siblings by accident, which a fixed name cannot.
    candidate = input_dir / "FAACIFP18"
    if candidate.exists():
        return candidate
    hits = sorted(input_dir.rglob("FAACIFP18"))
    if hits:
        return hits[0]
    raise ProceduresBuildError(
        f"no FAACIFP18 file found under {input_dir} -- expected the CIFP "
        f"zip to have been extracted there.")


def _approach_type_and_runway(proc_ident: str) -> tuple[str | None, str | None]:
    """Best-effort split of a 6-char approach identifier ("I07   " ->
    ("I", "07"), "H21-Y " -> ("H", "21"), "VOR-A " -> ("VOR", None) -- the
    trailing "-A"/"-B" Multiple Indicator marks a circling approach with no
    runway of its own). Deliberately not a full decode of every ARINC
    approach-type letter code (5.10) -- that table is long, approach-type
    dependent, and no golden fixture here needs it resolved further than
    "the raw prefix", so raw is what is stored."""
    ident = proc_ident.strip()
    if not ident:
        return (None, None)
    base = ident.split("-", 1)[0]
    i = 0
    while i < len(base) and not base[i].isdigit():
        i += 1
    kind = base[:i] or None
    digits = base[i:]
    runway = None
    if len(digits) >= 2 and digits[:2].isdigit():
        runway = digits[:2]
        if len(digits) > 2 and digits[2] in ("L", "R", "C"):
            runway += digits[2]
    return (kind, runway)


#: AER-1979: the ER stream in one CIFP file interleaves airway legs from
#: every Customer/Area Code the file covers (USA, CAN, PAC, LAM), and a
#: route ident is only unique *within* an area -- 73 of 1,504 idents in a
#: live cycle recur under a second area with an unrelated set of legs
#: (e.g. J501 is 11 US legs and, separately, 13 Canadian ones). This pack
#: is named/published "-conus" like every other FAA-sourced pack in this
#: repo (airports-conus, navaids-conus, obstacles-conus); it carries no
#: foreign data today by design, so the build-time policy is to filter to
#: USA rather than to key ``airways`` on ``(ident, area)`` and ship all
#: four -- a "-conus" pack advertising foreign-only airways is its own
#: defect. Going past the US border is a real, but separate, coverage
#: decision (see sources.py's OPENAIP_COUNTRIES note on the same tradeoff
#: for airspace).
_AREA = "USA"


def _build_airways(con: sqlite3.Connection, path: Path,
                    fix_index, cycle: str, unresolved: dict) -> int:
    airway_ids: dict[str, int] = {}
    n = 0
    for leg in arinc424.iter_airway_legs(path, fix_index):
        if leg.area != _AREA:
            continue
        airway_id = airway_ids.get(leg.route_ident)
        if airway_id is None:
            cur = con.execute(
                "INSERT INTO airways (ident, cycle) VALUES (?, ?)",
                (leg.route_ident, cycle))
            airway_id = cur.lastrowid
            airway_ids[leg.route_ident] = airway_id
        if leg.fix_lat is None:
            unresolved.setdefault(
                (leg.fix_id, leg.fix_icao or "", leg.fix_section or ""), []).append(
                f"airway {leg.route_ident} seq {leg.seq}")
        con.execute(
            "INSERT INTO airway_legs (airway_id, seq, fix_id, fix_lat, fix_lon, "
            "fix_type, min_alt_ft, max_alt_ft, direction, min_alt2_ft) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (airway_id, leg.seq, leg.fix_id, leg.fix_lat, leg.fix_lon,
             leg.fix_type, leg.min_alt_ft, leg.max_alt_ft, leg.direction,
             leg.min_alt2_ft))
        n += 1
    con.commit()
    return n


def _build_procedures(con: sqlite3.Connection, path: Path,
                       fix_index, cycle: str, unresolved: dict) -> int:
    # Keyed by (airport, kind, ident) so legs from every transition of the
    # same named procedure land under one procedures row; transitions are
    # keyed one level further by (route_type, transition ident) since a
    # blank transition ident is valid (the runway/common route) and still
    # distinct per route_type.
    proc_ids: dict[tuple[str, str, str], int] = {}
    proc_rnp: dict[tuple[str, str, str], float] = {}
    trans_ids: dict[tuple[int, str, str], int] = {}
    n = 0
    for leg in arinc424.iter_procedure_legs(path, fix_index):
        proc_key = (leg.airport, leg.kind, leg.proc_ident)
        proc_id = proc_ids.get(proc_key)
        if proc_id is None:
            approach_type, runway = (
                _approach_type_and_runway(leg.proc_ident)
                if leg.kind == "approach" else (None, None))
            cur = con.execute(
                "INSERT INTO procedures (airport, kind, ident, runway, "
                "approach_type, rnp, cycle) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (leg.airport, leg.kind, leg.proc_ident, runway,
                 approach_type, leg.rnp, cycle))
            proc_id = cur.lastrowid
            proc_ids[proc_key] = proc_id
        elif leg.rnp is not None and proc_key not in proc_rnp:
            # The procedure's coded RNP is a per-leg field in the source
            # (5.211); take the first non-blank value seen as representative
            # of the procedure as a whole -- see docstring caveat below.
            con.execute("UPDATE procedures SET rnp = ? WHERE id = ?",
                        (leg.rnp, proc_id))
            proc_rnp[proc_key] = leg.rnp

        trans_key = (proc_id, leg.route_type, leg.transition or "")
        trans_id = trans_ids.get(trans_key)
        if trans_id is None:
            try:
                role = arinc424.transition_role(leg.kind, leg.route_type, leg.transition)
            except ValueError as e:
                raise ProceduresBuildError(
                    f"{leg.airport} {leg.proc_ident}: {e} -- refusing to guess "
                    f"which part of the procedure it is.") from e
            cur = con.execute(
                "INSERT INTO transitions (proc_id, role, ident) VALUES (?, ?, ?)",
                (proc_id, role, leg.transition))
            trans_id = cur.lastrowid
            trans_ids[trans_key] = trans_id

        where = (f"{leg.airport} {leg.kind} {leg.proc_ident} "
                 f"{leg.transition or '(common)'} seq {leg.seq} {leg.path_term}")
        if leg.fix_id and leg.fix_lat is None:
            unresolved.setdefault(
                (leg.fix_id, leg.fix_icao or "", leg.fix_section or ""), []).append(where)
        if leg.path_term == "RF" and leg.centre_lat is None:
            unresolved.setdefault(
                (leg.centre_fix or "(none)", "", "RF centre"), []).append(where)

        # Only an RF leg's centre is an arc centre; on any other leg the
        # same columns name the procedure's MSA centre (AER-4446), and a
        # "radius" to it means nothing.
        arc_radius_nm = None
        if (leg.path_term == "RF" and leg.centre_lat is not None
                and leg.fix_lat is not None):
            arc_radius_nm = great_circle_nm(
                (leg.centre_lat, leg.centre_lon), (leg.fix_lat, leg.fix_lon))
        con.execute(
            "INSERT INTO legs (transition_id, seq, path_term, fix_id, fix_lat, "
            "fix_lon, fix_type, recd_navaid, theta, rho, course, dist_nm, "
            "time_min, alt_desc, alt1_ft, alt2_ft, speed_kt, turn_dir, rnp, "
            "flags, centre_fix, centre_lat, centre_lon, arc_radius_nm) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trans_id, leg.seq, leg.path_term, leg.fix_id, leg.fix_lat, leg.fix_lon,
             leg.fix_type, leg.recd_navaid, leg.theta, leg.rho, leg.course,
             leg.dist_nm, leg.time_min, leg.alt_desc, leg.alt1_ft, leg.alt2_ft,
             leg.speed_kt, leg.turn_dir, leg.rnp, leg.flags,
             leg.centre_fix, leg.centre_lat, leg.centre_lon, arc_radius_nm))
        n += 1
    con.commit()
    return n


def _build_grid_mora(con: sqlite3.Connection, path: Path) -> int:
    """Grid MORA (``AS``) bands and their one-degree cells (AER-4447).
    Every band in the file is kept, not just USA: AS records carry no
    Customer/Area Code at all, and an off-route altitude is wanted exactly
    where the airways stop. ``idx_mora_cell`` is UNIQUE so two bands
    claiming one cell fail the build rather than one silently winning."""
    n = 0
    try:
        for band in arinc424.iter_grid_mora(path):
            cur = con.execute(
                "INSERT INTO grid_mora_bands (lat_sw, lon_sw, cycle, raw) "
                "VALUES (?, ?, ?, ?)", (band.lat_sw, band.lon_sw, band.cycle, band.raw))
            band_id = cur.lastrowid
            con.executemany(
                "INSERT INTO grid_mora (band_id, lat_sw, lon_sw, mora_ft, raw) "
                "VALUES (?, ?, ?, ?, ?)",
                [(band_id, lat, lon, ft, raw) for lat, lon, ft, raw in band.cells()])
            n += 1
    except sqlite3.IntegrityError as e:
        raise ProceduresBuildError(
            f"two Grid MORA bands in {path} claim the same cell ({e}) -- "
            f"refusing to pick one.") from e
    con.commit()
    return n


def _build_msa(con: sqlite3.Connection, path: Path, fix_index, cycle: str) -> int:
    """One row per MSA sector. Not filtered by area like the airways are:
    the procedures this pack carries are not either, and Alaska's MSAs are
    coded under the CAN area. Returns the number of PS records stored."""
    n = 0
    for rec in arinc424.iter_msa(path, fix_index):
        record = json.dumps({"fields": rec.fields, "continuations": rec.continuations},
                            sort_keys=True)
        for s in rec.sectors:
            con.execute(
                "INSERT INTO msa (airport, area, centre_ident, centre_icao, centre_section, "
                "centre_subsection, centre_type, centre_lat, centre_lon, multiple_code, "
                "mag_true, sector_seq, bearing_start, bearing_end, altitude_ft, radius_nm, "
                "cycle, record) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rec.airport, rec.area, rec.centre_ident, rec.centre_icao, rec.centre_section,
                 rec.centre_subsection, rec.centre_type, rec.centre_lat, rec.centre_lon,
                 rec.multiple_code, rec.mag_true, s.seq, s.bearing_start, s.bearing_end,
                 s.altitude_ft, s.radius_nm, cycle, record))
        n += 1
    con.commit()
    return n


def _build_fas_blocks(con: sqlite3.Connection, path: Path, cycle: str
                      ) -> tuple[int, list[str]]:
    """Final Approach Segment data blocks (``PP``; AER-5623, additive). Each
    block's CRC is re-computed from its own fields; a block whose CRC does
    not match, or that will not encode, is dropped -- never stored -- and
    named in the returned list, which the build prints and PROC-FAS-001
    re-derives into the pack report. Not filtered by area, like ``msa``.
    Each block attaches to its approach by (airport, ident). Returns
    (blocks stored, labels of blocks dropped)."""
    approaches = {(a, i): pid for pid, a, i in con.execute(
        "SELECT id, airport, ident FROM procedures WHERE kind = 'approach'")}
    n = 0
    dropped: list[str] = []
    for b in arinc424.iter_path_points(path):
        if not b.crc_ok:
            dropped.append(f"{b.label}: CRC {b.crc}, computed "
                           f"{b.crc_computed or '(does not encode)'}")
            continue
        record = json.dumps({"fields": b.fields, "continuations": b.continuations},
                            sort_keys=True)
        con.execute(
            "INSERT INTO fas_blocks (proc_id, airport, area, approach_ident, runway, "
            "route_indicator, approach_type, operation_type, sbas_provider, "
            "ref_path_data_selector, ref_path_ident, apd, ltp_lat, ltp_lon, "
            "ltp_ellipsoid_height_m, ltp_orthometric_height_m, fpap_lat, fpap_lon, "
            "fpap_ellipsoid_height_m, fpap_orthometric_height_m, glidepath_angle_deg, tch, "
            "tch_units, course_width_m, length_offset_m, hal_m, val_m, gnss_channel, "
            "helicopter_course, crc, cycle, record) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?)",
            (approaches.get((b.airport, b.approach_ident)), b.airport, b.area,
             b.approach_ident, b.runway, b.route_indicator, b.approach_type_ident,
             b.operation_type, b.sbas_provider, b.ref_path_data_selector, b.ref_path_ident,
             b.apd, b.ltp_lat, b.ltp_lon, b.ltp_ellipsoid_height_m,
             b.ltp_orthometric_height_m, b.fpap_lat, b.fpap_lon, b.fpap_ellipsoid_height_m,
             b.fpap_orthometric_height_m, b.glidepath_angle_deg, b.tch, b.tch_units,
             b.course_width_m, b.length_offset_m, b.hal_m, b.val_m, b.gnss_channel,
             b.helicopter_course, b.crc, cycle, record))
        n += 1
    con.commit()
    return n, dropped


def build_procedures(input_dir: str | Path, out_path: str | Path) -> Path:
    """Build one sqlite ``procedures`` pack from an extracted CIFP zip.

    ``input_dir`` holds the extracted CIFP archive (containing
    ``FAACIFP18``); matches the ``Builder = Callable[[Path, Path], Path]``
    shape every entry in :data:`packtools.build.BUILDERS` follows.
    """
    input_dir = Path(input_dir)
    out_path = Path(out_path)
    cifp_path = _find_cifp_file(input_dir)
    cycle = arinc424.file_cycle(cifp_path)
    if not cycle:
        raise ProceduresBuildError(
            f"could not read a cycle ('VOLUME nnnn') from {cifp_path}'s header")

    fix_index = arinc424.build_fix_index(cifp_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    try:
        con.executescript(_SCHEMA)
        unresolved: dict[tuple[str, str, str], list[str]] = {}
        n_airway_legs = _build_airways(con, cifp_path, fix_index, cycle, unresolved)
        n_proc_legs = _build_procedures(con, cifp_path, fix_index, cycle, unresolved)
        _build_grid_mora(con, cifp_path)
        n_msa = _build_msa(con, cifp_path, fix_index, cycle)
        n_fas, fas_dropped = _build_fas_blocks(con, cifp_path, cycle)
    finally:
        con.close()
    for line in fas_dropped:
        print(f"WARN fas_blocks: dropped {line}", file=sys.stderr)

    if n_airway_legs == 0:
        raise ProceduresBuildError(
            f"no airway legs parsed from {cifp_path} -- refusing to ship an "
            f"empty airways table (PA2 depends on it).")
    if n_proc_legs == 0:
        raise ProceduresBuildError(
            f"no procedure legs parsed from {cifp_path} -- refusing to ship "
            f"an empty procedures pack.")
    if n_msa == 0:
        raise ProceduresBuildError(
            f"no MSA records parsed from {cifp_path} -- every cycle carries "
            f"thousands; refusing to ship an empty msa table.")
    if n_fas == 0:
        raise ProceduresBuildError(
            f"no FAS data blocks stored from {cifp_path} ({len(fas_dropped)} dropped) -- "
            f"every cycle carries thousands; refusing to ship an empty fas_blocks table.")
    _check_unresolved(unresolved, fix_index, cifp_path)
    return out_path
