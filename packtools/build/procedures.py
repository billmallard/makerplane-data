# SPDX-License-Identifier: Apache-2.0
"""ARINC 424 CIFP -> sqlite ``procedures`` pack (AER-1600 / PA1).

Builds five tables from one FAACIFP18-format file: ``airways`` +
``airway_legs`` (Enroute Airways, ``ER``) and ``procedures`` +
``transitions`` + ``legs`` (SID/STAR/Approach, ``PD``/``PE``/``PF``).
Schema is brief section 3.3/3.4's, unchanged.

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

import math
import sqlite3
from pathlib import Path

from .. import arinc424

_EARTH_RADIUS_NM = 3440.065

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
    direction   TEXT
);
CREATE INDEX idx_awy_ident ON airways(ident);
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
    centre_fix      TEXT,       -- RF (radius-to-fix) arc centre; NULL for every other leg type
    centre_lat      REAL,
    centre_lon      REAL,
    arc_radius_nm   REAL        -- derived: great_circle(centre, fix); convenience for the renderer
);
CREATE INDEX idx_proc_airport ON procedures(airport, kind);
CREATE INDEX idx_trans_proc ON transitions(proc_id);
CREATE INDEX idx_legs_trans ON legs(transition_id, seq);
"""


class ProceduresBuildError(RuntimeError):
    pass


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


def _great_circle_nm(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (*p1, *p2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return _EARTH_RADIUS_NM * 2 * math.asin(min(1.0, math.sqrt(a)))


def _build_airways(con: sqlite3.Connection, path: Path,
                    fix_index, cycle: str) -> int:
    airway_ids: dict[str, int] = {}
    n = 0
    for leg in arinc424.iter_airway_legs(path, fix_index):
        airway_id = airway_ids.get(leg.route_ident)
        if airway_id is None:
            cur = con.execute(
                "INSERT INTO airways (ident, cycle) VALUES (?, ?)",
                (leg.route_ident, cycle))
            airway_id = cur.lastrowid
            airway_ids[leg.route_ident] = airway_id
        con.execute(
            "INSERT INTO airway_legs (airway_id, seq, fix_id, fix_lat, fix_lon, "
            "fix_type, min_alt_ft, max_alt_ft, direction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (airway_id, leg.seq, leg.fix_id, leg.fix_lat, leg.fix_lon,
             leg.fix_type, leg.min_alt_ft, leg.max_alt_ft, leg.direction))
        n += 1
    con.commit()
    return n


def _build_procedures(con: sqlite3.Connection, path: Path,
                       fix_index, cycle: str) -> int:
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
            role = arinc424.transition_role(leg.kind, leg.route_type, leg.transition)
            cur = con.execute(
                "INSERT INTO transitions (proc_id, role, ident) VALUES (?, ?, ?)",
                (proc_id, role, leg.transition))
            trans_id = cur.lastrowid
            trans_ids[trans_key] = trans_id

        arc_radius_nm = None
        if leg.centre_lat is not None and leg.fix_lat is not None:
            arc_radius_nm = _great_circle_nm(
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
        n_airway_legs = _build_airways(con, cifp_path, fix_index, cycle)
        n_proc_legs = _build_procedures(con, cifp_path, fix_index, cycle)
    finally:
        con.close()

    if n_airway_legs == 0:
        raise ProceduresBuildError(
            f"no airway legs parsed from {cifp_path} -- refusing to ship an "
            f"empty airways table (PA2 depends on it).")
    if n_proc_legs == 0:
        raise ProceduresBuildError(
            f"no procedure legs parsed from {cifp_path} -- refusing to ship "
            f"an empty procedures pack.")
    return out_path
