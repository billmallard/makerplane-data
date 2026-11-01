# SPDX-License-Identifier: Apache-2.0
"""Pack test record: ``packtool report`` and ``packtool report --check``.

Writes the ``pack-report/1`` record (docs/pack_report.md) for one pack file:
the v1 check catalogue for the pack's kind (testing-visibility plan section
4.3), what each check measured, and what each one **cannot** catch. Then
``--check`` re-runs the open checks on a downloaded pack and diffs the
result against the published record (plan section 5.5).

The record is evidence, not a verdict on the pack. A check can pass; a pack
is never "passed".

Every number a check reports is a pure function of the pack bytes (plus,
where stated, the previous edition, the upstream inputs, or the pyEfis
checkout), so anyone with the same inputs gets the same record. Nothing
here prints local paths into a record.

QA reviews each kind's check definitions and thresholds before they first
gate a publish (plan section 4.4, AER-4102). The thresholds below are the
author's proposals until that review lands; QA decides what to accept.
"""

from __future__ import annotations

import array
import datetime as _dt
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import arinc424, device_probe, packmeta
from .manifest import NOTICE, ORIGINS
from .packmeta import KINDS
from .regions import load_regions, tile_sw_corner
from .signing import sha256_file

SCHEMA = "pack-report/1"
RECORDS = ("build", "retro", "rejected")
STATUSES = ("pass", "warn", "fail", "not-run")
REPO = "billmallard/makerplane-data"

# --- Thresholds --------------------------------------------------------------
# ROW_DELTA_WARN, the GEO-002 allowance and OSM_REGION_DROP_WARN are accepted
# by QA only as provisional warn levels (AER-4102 C7); the record says so.
PROVISIONAL = ("no edition-to-edition baseline yet (one published pair per kind when "
               "set); a warn level for a person to look at, to be re-set once enough "
               "records exist (AER-4102)")
#: ROW-002: a table whose row count moves more than this against the previous
#: edition is a warn that needs a build note.
ROW_DELTA_WARN = 0.10
#: GEO-002: rows outside the US coverage envelope may grow by this many rows
#: or this fraction (whichever is larger) before the check warns.
GEO_OUTSIDE_GROWTH_ROWS = 25
GEO_OUTSIDE_GROWTH_FRACTION = 0.10
#: PROC-FIX-001: legs naming a fix the build could not place. 2610 measured
#: 950 of 201,030 and 2611 measured 941 of 200,853 (both 0.47%).
PROC_UNRESOLVED_MAX_FRACTION = 0.005
#: PROC-MSA-003: plausible MSA sector values. An MSA is 1,000 ft above the
#: highest obstacle in the sector, so nothing lower is possible; 2611 runs
#: 1,300 to 16,000 ft. Radii are 25 nm unless extended to take in the
#: airport; 2611 runs 25 to 37 nm (KASX, centred on IWD).
MSA_ALT_MIN_FT = 1000
MSA_ALT_MAX_FT = 20000
MSA_RADIUS_MIN_NM = 10
MSA_RADIUS_MAX_NM = 50
#: PROC-MSA-004: MSA records whose centre fix the build could not place.
#: 2611 measured 1 of 6,058 (KHRJ's HQT, an NDB the file does not define).
MSA_UNRESOLVED_MAX_FRACTION = 0.001
#: PROC-MORA-001: Grid MORA band floor, 50% of the 241 bands in 2611
#: (rounded down), by the SG-004 convention.
PROC_MORA_BAND_FLOOR = 120
#: PROC-MORA-003: plausible Grid MORA range in feet. Floor: the 1,000 ft
#: non-mountainous buffer over sea level. Ceiling: Denali (20,310 ft) plus
#: the 2,000 ft mountainous buffer, with room to spare. 2611 spans 1,400 to
#: 22,700.
PROC_MORA_MIN_FT = 1_000
PROC_MORA_MAX_FT = 25_000
#: OSM-001: a region's feature count dropping by more than this warns; a
#: region that had at least OSM_REGION_MIN features and now has none fails.
OSM_REGION_DROP_WARN = 0.10
OSM_REGION_MIN = 100
#: TER-004: how many native tiles to sample, and the plausible range.
TER_SAMPLE_TILES = 16
TER_ELEV_MIN_M = -500
TER_ELEV_MAX_M = 9000
TER_VOID_MAX_FRACTION = 0.5

# Default pedigree label per kind (docs/do200b_applicability.md section 5).
# Terrain (Copernicus) is neither federal nor community: pass --origin.
ORIGIN_BY_KIND = {
    "navdata": "federal", "navaids": "federal", "obstacles": "federal",
    "procedures": "federal", "cifp": "federal", "plates": "federal",
    "airports": "community", "water": "community", "highways": "community",
    "rivers": "community", "airspace": "community",
}

#: Kinds whose catalogue QA has not accepted as a gate (AER-4102). A record
#: is still written for them, but it says it is ungated and a build record
#: never clears the publish gate on its own.
UNGATED = {
    "rivers": "OSM checks only and no device probe; it needs IDX-001 and an ACC-001 "
              "probe before it can gate",
    "cifp": "no content check beyond the common and SQLite ones, and no device probe "
            "(the CIFP build itself is deferred)",
    "airspace": "ASP-001..003 are new (AER-4445) and QA has not accepted them as a gate; "
                "no device probe (pyEfis has no airspace reader yet)",
    "plates": "a zip pack: only PACK-001..004 and a not-run ACC-001; no content check and "
              "no device probe",
}

_FEDERAL_SQLITE = ("navdata", "navaids", "obstacles", "procedures")
_OSM = ("water", "highways", "rivers")

# Which schema_guard contract speaks for which pack kind. Obstacles (FAA
# DOF) has no contract, so its SG checks could never run; OBS-001 is its
# floor instead.
_GUARD_BUILDER = {"navdata": "airports", "navaids": "navaids"}

#: OBS-001: row floors for kinds with no schema_guard contract, by the
#: SG-004 convention (50% of an observed build, rounded down to the
#: hundred). obstacles: 50% of the 657,181 rows in obstacles-conus 261001
#: (260806 had 652,407).
_ROW_FLOORS = {"obstacles": {"obstacles": 328_500}}

# Coordinate columns per kind, for GEO-001/GEO-002. Missing tables or
# columns are skipped, not failed: the schema is ACC-001's and SG-004's job.
_COORDS = {
    "navdata": [("airports", "lat", "lon"), ("runway_ends", "lat", "lon")],
    "airports": [("airports", "lat", "lon"), ("runway_ends", "lat", "lon")],
    "navaids": [("navaids", "lat", "lon"), ("fixes", "lat", "lon"),
                ("awy_segments", "lat1", "lon1"), ("awy_segments", "lat2", "lon2")],
    "obstacles": [("obstacles", "lat", "lon")],
    "procedures": [("legs", "fix_lat", "fix_lon"), ("legs", "centre_lat", "centre_lon"),
                   ("airway_legs", "fix_lat", "fix_lon"), ("msa", "centre_lat", "centre_lon")],
}

# Tables whose NULL coordinates GEO-001 tolerates: a procedure or airway
# leg may name no fix, and PROC-FIX-001 counts the legs whose fix the build
# could not place; PROC-MSA-004 does the same for MSA centres. On every
# other (point) table a NULL position is bad.
_NULL_COORDS_OK = ("legs", "airway_legs", "msa")

# Where FAA data is expected: CONUS, Alaska (both sides of the antimeridian),
# Hawaii, Puerto Rico / USVI, Guam / CNMI, American Samoa, Wake, Midway.
# [lat_min, lon_min, lat_max, lon_max]. FAA files also carry real oceanic
# and foreign points, so GEO-002 tracks the count, it does not forbid it.
US_ENVELOPE = [(23, -126, 51, -65), (50, -180, 72.5, -128), (50, 170, 56, 180),
               (18, -161, 23, -154), (17, -68, 19, -64), (13, 144, 21, 146.5),
               (-15, -171, -11, -168), (19, 166, 20, 167), (28, -178, 29, -177)]

# Keys that must be unique (UNQ-001, blocking) and keys the FAA reuses
# legitimately, tracked against the previous edition (UNQ-002).
_UNIQUE = {
    "navdata": [("airports", ("site_no",)), ("airports", ("icao",)),
                ("runways", ("site_no", "rwy_id")),
                ("runway_ends", ("site_no", "rwy_id", "end_id"))],
    "airports": [("airports", ("icao",))],
    "navaids": [("fixes", ("id",))],
    "obstacles": [("obstacles", ("oas_no",))],
    "procedures": [("procedures", ("airport", "kind", "ident")),
                   ("msa", ("airport", "area", "centre_ident", "centre_icao", "centre_section",
                            "centre_subsection", "multiple_code", "sector_seq"))],
}
_REUSED = {
    "navaids": [("navaids", ("id", "type")), ("awy_segments", ("awy_id", "seq"))],
}

# ROW-001: tables a kind's builder creates empty by design, declared here so
# the record says so. water: pyEfis tools/build_water_db.py creates
# waterway_lines (and its R-tree) in every build and fills it only with
# --waterway, which the Geofabrik orchestrator never passes; river lines are
# the rivers kind (AER-4137).
_DECLARED_EMPTY = {"water": ("waterway_lines",)}

# The R-trees each kind's pyEfis builder writes, and the table each one
# indexes (pyEfis tools/build_highway_db.py, tools/build_water_db.py).
_RTREES = {
    "highways": (("highway_rtree", "highway_lines"),),
    "water": (("water_rtree", "water_polygons"), ("waterway_rtree", "waterway_lines")),
}


class ReportError(RuntimeError):
    """The pack cannot be reported on at all (not a pack, unknown kind)."""


# --- the check model --------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One catalogue entry. ``run(ctx)`` returns ``(status, measured, note)``."""
    id: str
    title: str
    blocking: bool
    expected: str
    cannot_catch: str
    run: Callable[["Ctx"], tuple]
    since: str = ""
    #: The only reasons this check may be ``not-run`` in a build record
    #: without blocking the publish: ``(reason, why)`` pairs, declared here
    #: and never at run time (AER-4102 C1). A blocking check with any other
    #: not-run reason blocks.
    exempt: tuple = ()
    #: Set when the threshold has no measured baseline yet; carried into the
    #: record so a reader knows the number is a placeholder (AER-4102 C7).
    provisional: str = ""


@dataclass
class Ctx:
    path: Path
    meta: packmeta.PackMeta
    kind: str
    is_zip: bool
    origin: str
    previous: dict | None
    inputs: Path | None
    pyefis_src: Path | None
    probe: bool
    sources: list
    first_edition: bool = False
    origin_given: bool = False
    metrics: dict = field(default_factory=dict)
    _con: sqlite3.Connection | None = None
    _tables: dict | None = None

    @property
    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        return self._con

    @property
    def tables(self) -> dict[str, int]:
        if self._tables is None:
            self._tables = _table_counts(self.con)
        return self._tables

    def prev_metric(self, check_id: str):
        return ((self.previous or {}).get("metrics") or {}).get(check_id)

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None


def _not_run(note: str, reason: str = "") -> tuple:
    return ("not-run", None, note, reason)


#: Declared exemption for a delta check: the caller said this is the id's
#: first edition (``--first-edition``), so there is nothing to compare.
FIRST_EDITION = ("first-edition", "first edition, no previous record")


def _no_previous(ctx, extra: str = "") -> tuple:
    """not-run for a delta check with no previous record. Only a first
    edition the caller declared is exemptable; a missing ``--previous`` is not."""
    if ctx.first_edition:
        return _not_run("First edition (--first-edition): there is no previous record to "
                        "compare against." + extra, FIRST_EDITION[0])
    return _not_run("No previous edition was supplied to compare against (--previous), and "
                    "--first-edition was not declared." + extra, "no-previous")


# --- sqlite helpers ---------------------------------------------------------

def _table_counts(con: sqlite3.Connection) -> dict[str, int]:
    """Row count per data table. Leaves out pack_meta, sqlite internals and
    virtual tables with their shadow tables (an rtree mirrors its table)."""
    rows = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    virtual = {n for n, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL TABLE")}
    shadow = {f"{v}_{s}" for v in virtual for s in ("node", "rowid", "parent")}
    out = {}
    for name, _sql in sorted(rows):
        if name.startswith("sqlite_") or name == "pack_meta" or name in virtual or name in shadow:
            continue
        out[name] = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    return out


def _columns(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _pct(new: int, old: int) -> str:
    return f"{(new - old) / old * 100:+.1f}%" if old else "new"


# --- checks: every kind -----------------------------------------------------

def _c_meta(ctx):
    m = ctx.meta
    return ("pass", f"id={m.id} kind={m.kind} cycle={m.cycle} schema_version={m.schema_version}", "")


def _c_meta_fields(ctx):
    missing = [f for f in ("attribution", "license") if not getattr(ctx.meta, f)]
    if missing:
        return ("fail", "missing: " + ", ".join(missing), "")
    return ("pass", "attribution and license present", "")


#: PACK-003's measured text when the label was passed rather than defaulted.
ORIGIN_PASSED = "passed with --origin"


def _c_origin(ctx):
    if ctx.origin_given:
        how = ORIGIN_PASSED
    elif ctx.kind in ORIGIN_BY_KIND:
        how = f"defaulted by kind {ctx.kind}, not read from the pack"
    else:
        how = f"kind {ctx.kind} has no default; pass --origin"
    measured = f"{ctx.origin or '(unset)'} ({how})"
    return ("pass" if ctx.origin in ORIGINS else "fail", measured, "")


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _c_sources(ctx):
    if not ctx.sources:
        return _not_run("No upstream sources were supplied to the report (--sources).")
    bad = []
    for src in ctx.sources:
        gaps = [g for g, ok in (("no edition", (src.get("edition") or "").strip()),
                                ("no 64-hex sha256", _HEX64.match(src.get("sha256") or "")))
                if not ok]
        if gaps:
            bad.append(f"{src.get('name') or '(unnamed)'} ({', '.join(gaps)})")
    n = len(ctx.sources)
    if bad:
        return ("fail", f"{n} source(s); incomplete: " + "; ".join(bad), "")
    return ("pass", f"{n} source(s), each with edition and sha256", "")


def _c_device(ctx):
    if not ctx.probe:
        return _not_run("Device acceptance was switched off for this run (--no-device).")
    if ctx.kind not in _PROBE_KINDS:
        return _not_run(f"No pyEfis reader probe is defined for kind {ctx.kind!r} in catalogue v1.")
    if ctx.pyefis_src is None:
        return _not_run("No pyEfis checkout was available (set PYEFIS_SRC_DIR or pass --pyefis-src).")
    res = run_device_probe(ctx.kind, ctx.path, ctx.pyefis_src)
    if res.get("unavailable"):
        return _not_run(f"The pyEfis reader could not be loaded here: {res.get('error')}")
    if not res.get("opened"):
        return ("fail", f"reader did not open the pack: {res.get('error')}", "")
    qs = res.get("queries") or []
    ctx.metrics["ACC-001"] = qs
    ok = sum(1 for q in qs if q["ok"])
    if qs and ok == len(qs):
        return ("pass", f"opened; {ok}/{len(qs)} queries returned data", "")
    empty = ", ".join(q["query"] for q in qs if not q["ok"]) or "no queries ran"
    return ("fail", f"opened; {ok}/{len(qs)} queries returned data; empty: {empty}", "")


_PROBE_KINDS = ("navdata", "airports", "navaids", "obstacles", "procedures",
                "highways", "water", "terrain")


# --- checks: sqlite ---------------------------------------------------------

def _c_quick_check(ctx):
    res = [r[0] for r in ctx.con.execute("PRAGMA quick_check")]
    if res == ["ok"]:
        return ("pass", "ok", "")
    return ("fail", "; ".join(res[:5]), "")


def _c_empty(ctx):
    if not ctx.tables:
        return ("fail", "no data tables", "")
    declared = _DECLARED_EMPTY.get(ctx.kind, ())
    empty = sorted(t for t, n in ctx.tables.items() if n == 0)
    undeclared = [t for t in empty if t not in declared]
    if undeclared:
        return ("fail", "empty: " + ", ".join(undeclared), "")
    if empty:
        return ("pass", "0 empty tables; empty by declaration: " + ", ".join(empty), "")
    return ("pass", 0, "")


def _c_row_delta(ctx):
    prev = (ctx.previous or {}).get("tables")
    if not prev:
        return _no_previous(ctx)
    worst = None
    for t in sorted(set(prev) | set(ctx.tables)):
        old = (prev.get(t) or {}).get("rows")
        new = ctx.tables.get(t, 0)
        if not old:
            continue
        frac = (new - old) / old
        if worst is None or abs(frac) > abs(worst[0]):
            worst = (frac, t, old, new)
    if worst is None:
        return _not_run("The previous edition shares no non-empty table with this one.")
    frac, t, old, new = worst
    measured = f"largest change: {t} {_pct(new, old)} ({old:,} -> {new:,})"
    return ("warn" if abs(frac) > ROW_DELTA_WARN else "pass", measured, "")


def _c_rtree(ctx):
    names = {r[0] for r in ctx.con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND upper(sql) LIKE 'CREATE VIRTUAL TABLE%USING RTREE%'")}
    declared = _RTREES.get(ctx.kind, ())
    parts, bad = [], False
    for rt, base in declared:
        if rt not in names:
            parts.append(f"{rt} missing")
            bad = True
            continue
        n_rt = ctx.con.execute(f'SELECT COUNT(*) FROM "{rt}"').fetchone()[0]
        n_base = ctx.tables.get(base)
        if n_base is None:
            parts.append(f"{rt} {n_rt:,}, base table {base} missing")
            bad = True
            continue
        chk = ctx.con.execute("SELECT rtreecheck(?)", (rt,)).fetchone()[0]
        ok = n_rt == n_base and chk == "ok"
        bad = bad or not ok
        rel = "=" if n_rt == n_base else "!="
        part = f"{rt} {n_rt:,} {rel} {base} {n_base:,}, rtreecheck "
        part += "ok" if chk == "ok" else chk.replace("\n", "; ")[:200]
        parts.append(part)
    for rt in sorted(names - {r for r, _ in declared}):
        parts.append(f"{rt} is not declared for kind {ctx.kind}")
        bad = True
    return ("fail" if bad else "pass", "; ".join(parts), "")


def _c_tables_kept(ctx):
    if ctx.previous is None:
        return _no_previous(ctx)
    prev = ctx.previous.get("tables") or {}
    gone = sorted(t for t in prev if t not in ctx.tables)
    if not gone:
        return ("pass", f"{len(gone)} of {len(prev)} tables gone", "")
    old = str((ctx.previous.get("pack") or {}).get("schema_version", ""))
    new = str(ctx.meta.schema_version)
    if old != new:
        return ("pass", f"gone: {', '.join(gone)}; schema_version {old} -> {new}", "")
    return ("fail", f"gone: {', '.join(gone)} (schema_version unchanged at {new})", "")


# --- checks: federal --------------------------------------------------------

def _guard_source(ctx):
    class _S:
        builder = _GUARD_BUILDER.get(ctx.kind, "")
    return _S()


def _guard_layer(layer: int):
    def run(ctx):
        from .schema_guard import DEFAULT
        src = _guard_source(ctx)
        if DEFAULT._contract(src) is None:
            return _not_run(f"No schema_guard contract exists for the {ctx.kind} source yet "
                            "(testing-visibility plan section 2).")
        if layer == 4:
            res = DEFAULT.evaluate_output(src, ctx.path)
            floors = DEFAULT.contracts[src.builder].floors
            measured = "; ".join(f"{t} {ctx.tables.get(t, 0):,} (floor {f:,})"
                                 for t, f in sorted(floors.items()))
            return ("pass" if res.ok else "fail", measured, "")
        if ctx.inputs is None:
            return _not_run("Needs the upstream CSVs (--inputs). The guard runs on the live "
                            "download at build time.")
        res = DEFAULT.evaluate_inputs(src, ctx.inputs)[layer - 1]
        if res.ok:
            return ("pass", "held", "")
        return ("fail", res.message.replace(str(ctx.inputs), "<inputs>"), "")
    return run


def _c_row_floor(ctx):
    floors = _ROW_FLOORS.get(ctx.kind, {})
    low = [t for t, f in floors.items() if ctx.tables.get(t, 0) < f]
    measured = "; ".join(f"{t} {ctx.tables.get(t, 0):,} (floor {f:,})"
                         for t, f in sorted(floors.items()))
    return ("fail" if low else "pass", measured, "")


def _coord_columns(ctx):
    out = []
    for t, la, lo in _COORDS.get(ctx.kind, []):
        if t in ctx.tables and {la, lo} <= _columns(ctx.con, t):
            out.append((t, la, lo))
    return out


def _c_coords_valid(ctx):
    cols = _coord_columns(ctx)
    if not cols:
        return _not_run("This pack has none of the coordinate columns the check knows.")
    bad = 0
    for t, la, lo in cols:
        scope = f"{la} IS NOT NULL AND " if t in _NULL_COORDS_OK else ""
        bad += ctx.con.execute(
            f'SELECT COUNT(*) FROM "{t}" WHERE {scope}('
            f'{la} IS NULL OR {lo} IS NULL OR {la} < -90 OR {la} > 90 OR {lo} < -180 '
            f'OR {lo} > 180 OR ({la} = 0 AND {lo} = 0))').fetchone()[0]
    return ("fail" if bad else "pass", bad, "")


def _envelope_sql(la: str, lo: str) -> str:
    return " OR ".join(f"({la} BETWEEN {a} AND {c} AND {lo} BETWEEN {b} AND {d})"
                       for a, b, c, d in US_ENVELOPE)


def _c_envelope(ctx):
    cols = _coord_columns(ctx)
    if not cols:
        return _not_run("This pack has none of the coordinate columns the check knows.")
    counts = {}
    for t, la, lo in cols:
        counts[f"{t}.{la}"] = ctx.con.execute(
            f'SELECT COUNT(*) FROM "{t}" WHERE {la} IS NOT NULL AND {lo} IS NOT NULL '
            f'AND NOT ({_envelope_sql(la, lo)})').fetchone()[0]
    ctx.metrics["GEO-002"] = counts
    total = sum(counts.values())
    prev = ctx.prev_metric("GEO-002")
    if prev is None:
        return _no_previous(ctx, f" This edition has {total:,} such points; "
                            "the next edition compares against that.")
    old = sum(prev.values())
    allowed = old + max(GEO_OUTSIDE_GROWTH_ROWS, int(old * GEO_OUTSIDE_GROWTH_FRACTION))
    return ("warn" if total > allowed else "pass", f"{total:,} (previous {old:,})", "")


def _dup_count(con, table: str, cols: tuple) -> int:
    c = ", ".join(cols)
    return con.execute(f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table}" GROUP BY {c} '
                       f'HAVING COUNT(*) > 1)').fetchone()[0]


def _present_keys(ctx, keys):
    return [(t, cols) for t, cols in keys
            if t in ctx.tables and set(cols) <= _columns(ctx.con, t)]


def _c_unique(ctx):
    keys = _present_keys(ctx, _UNIQUE.get(ctx.kind, []))
    if not keys:
        return _not_run("This pack has none of the keys the check knows.")
    parts = [(f"{t}({', '.join(c)})", _dup_count(ctx.con, t, c)) for t, c in keys]
    dups = sum(n for _, n in parts)
    measured = "; ".join(f"{k} {n}" for k, n in parts)
    return ("fail" if dups else "pass", measured, "")


def _c_reused(ctx):
    keys = _present_keys(ctx, _REUSED.get(ctx.kind, []))
    if not keys:
        return _not_run("This pack has none of the keys the check knows.")
    counts = {f"{t}({', '.join(c)})": _dup_count(ctx.con, t, c) for t, c in keys}
    ctx.metrics["UNQ-002"] = counts
    prev = ctx.prev_metric("UNQ-002")
    measured = "; ".join(f"{k} {n}" for k, n in counts.items())
    if prev is None:
        return _no_previous(ctx, f" This edition: {measured}.")
    grew = [k for k, n in counts.items() if n > prev.get(k, 0)]
    prev_s = "; ".join(f"{k} {prev.get(k, 0)}" for k in counts)
    return ("warn" if grew else "pass", f"{measured} (previous {prev_s})", "")


# --- checks: procedures -----------------------------------------------------

def _c_unresolved(ctx):
    if "legs" not in ctx.tables:
        return ("fail", "no legs table", "")
    total = ctx.tables["legs"]
    rows = ctx.con.execute(
        "SELECT COALESCE(fix_type, ''), COUNT(*) FROM legs WHERE fix_id IS NOT NULL "
        "AND TRIM(fix_id) <> '' AND (fix_lat IS NULL OR fix_lon IS NULL) "
        "GROUP BY 1 ORDER BY 1").fetchall()
    n = sum(c for _, c in rows)
    ctx.metrics["PROC-FIX-001"] = {"unresolved": n, "legs": total,
                                   "by_fix_type": {k or "(none)": c for k, c in rows}}
    frac = n / total if total else 0.0
    measured = f"{n:,} of {total:,} legs ({frac * 100:.2f}%)"
    return ("warn" if frac > PROC_UNRESOLVED_MAX_FRACTION else "pass", measured, "")


def _c_awy_unique(ctx):
    if "airways" not in ctx.tables:
        return ("fail", "no airways table", "")
    dups = _dup_count(ctx.con, "airways", ("ident",))
    idx = ctx.con.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                          "AND name='idx_awy_ident'").fetchone()
    unique = bool(idx and "UNIQUE" in (idx[0] or "").upper())
    measured = f"{dups} duplicate idents; unique index {'present' if unique else 'absent'}"
    return ("fail" if dups else "pass", measured, "")


def _c_awy_collision(ctx):
    if "airway_legs" not in ctx.tables:
        return ("fail", "no airway_legs table", "")
    n = ctx.con.execute(
        "SELECT COUNT(DISTINCT airway_id) FROM (SELECT airway_id FROM airway_legs "
        "GROUP BY airway_id, seq HAVING COUNT(*) > 1)").fetchone()[0]
    return ("fail" if n else "pass", n, "")


# --- checks: airspace -------------------------------------------------------

def _segments_cross(a, b, c, d) -> bool:
    """Whether segment ab and segment cd share a point (collinear overlap
    included)."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 0) - (v < 0)

    def on(p, q, r):
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
                and min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return ((o1 == 0 and on(a, c, b)) or (o2 == 0 and on(a, d, b))
            or (o3 == 0 and on(c, a, d)) or (o4 == 0 and on(c, b, d)))


def _ring_problem(ring) -> str | None:
    """Why a GeoJSON linear ring is not closed and simple, or None."""
    if not isinstance(ring, list) or len(ring) < 4:
        return "fewer than 4 positions"
    pts = []
    for p in ring:
        if (not isinstance(p, list) or len(p) < 2
                or not all(isinstance(v, (int, float)) for v in p[:2])):
            return "a position is not [lon, lat]"
        if not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            return "a position is out of range"
        pts.append((float(p[0]), float(p[1])))
    if pts[0] != pts[-1]:
        return "not closed"
    # Drop repeated consecutive positions: legal, but they are zero-length
    # edges the crossing test would report against their neighbours.
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(pts) < 4:
        return "fewer than 3 distinct vertices"
    segs = list(zip(pts, pts[1:]))
    n = len(segs)
    order = sorted(range(n), key=lambda i: min(segs[i][0][0], segs[i][1][0]))
    active: list[int] = []
    for i in order:
        (a, b) = segs[i]
        lo_x = min(a[0], b[0])
        active = [j for j in active if max(segs[j][0][0], segs[j][1][0]) >= lo_x]
        for j in active:
            if abs(i - j) in (1, n - 1):    # neighbours share a vertex by design
                continue
            c, d = segs[j]
            if (max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
                continue
            if _segments_cross(a, b, c, d):
                return "self-intersects"
        active.append(i)
    return None


def _c_asp_polygons(ctx):
    if "airspaces" not in ctx.tables:
        return ("fail", "no airspaces table", "")
    bad: dict[str, list[str]] = {}
    total = 0
    for rid, geom in ctx.con.execute("SELECT id, geometry FROM airspaces ORDER BY id"):
        total += 1
        try:
            g = json.loads(geom)
        except (TypeError, ValueError):
            bad.setdefault("not JSON", []).append(rid)
            continue
        if not isinstance(g, dict) or g.get("type") != "Polygon" or not g.get("coordinates"):
            bad.setdefault("not a Polygon", []).append(rid)
            continue
        for ring in g["coordinates"]:
            why = _ring_problem(ring)
            if why:
                bad.setdefault(why, []).append(rid)
                break
    n = sum(len(v) for v in bad.values())
    if not n:
        return ("pass", f"0 of {total:,} polygons", "")
    detail = "; ".join(f"{why}: {len(ids)} ({', '.join(ids[:3])}{', ...' if len(ids) > 3 else ''})"
                       for why, ids in sorted(bad.items()))
    return ("fail", f"{n:,} of {total:,} polygons: {detail}", "")


def _asp_feet(value, unit, ref):
    """A limit as (feet, comparable class), or None if it cannot be placed.
    Classes: "gnd" (the surface), "unl" (unlimited), "agl", "alt" (MSL feet
    or a flight level, compared as feet)."""
    if value == "UNLTD":
        return (math.inf, "unl")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    unit, ref = str(unit), str(ref)
    if unit == "6":
        return (v * 100, "alt")
    if unit != "1":
        return None
    if ref == "0":
        return (v, "gnd" if v == 0 else "agl")
    if ref in ("1", "2"):
        return (v, "alt")
    return None


def _c_asp_limits(ctx):
    if "airspaces" not in ctx.tables:
        return ("fail", "no airspaces table", "")
    inverted, equal, unplaced, apart, compared = [], 0, 0, 0, 0
    for row in ctx.con.execute(
            "SELECT id, lower_limit, lower_limit_unit, lower_limit_reference, upper_limit, "
            "upper_limit_unit, upper_limit_reference FROM airspaces ORDER BY id"):
        lo, hi = _asp_feet(*row[1:4]), _asp_feet(*row[4:7])
        if lo is None or hi is None:
            unplaced += 1
            continue
        same = lo[1] == hi[1] or "gnd" in (lo[1], hi[1]) or "unl" in (lo[1], hi[1])
        if not same:         # AGL against MSL: depends on the terrain beneath
            apart += 1
            continue
        compared += 1
        if lo[0] > hi[0]:
            inverted.append(row[0])
        elif lo[0] == hi[0]:
            equal += 1
    measured = (f"{len(inverted)} floor above ceiling of {compared:,} compared; {equal} floor "
                f"equal to ceiling; {apart:,} not comparable (AGL against MSL); {unplaced} "
                "with a limit the check cannot read")
    if inverted:
        measured += " -- " + ", ".join(inverted[:5])
    return ("fail" if inverted or unplaced else "pass", measured, "")


#: ASP-003's declared exemption: an openAIP pack has no CIFP rows to reconcile.
ASP_NOT_CIFP = ("not-cifp", "the pack holds no CIFP-built rows (an openAIP pack)")


def _c_asp_reconcile(ctx):
    if "airspaces" not in ctx.tables:
        return ("fail", "no airspaces table", "")
    built = dict(ctx.con.execute(
        "SELECT substr(id, 7, 1), COUNT(*) FROM airspaces WHERE id LIKE 'cifp-U%' "
        "GROUP BY 1").fetchall())
    if not built:
        return _not_run("No CIFP-built rows in this pack.", ASP_NOT_CIFP[0])
    src = None
    if ctx.inputs is not None:
        src = ctx.inputs / "FAACIFP18"
        if not src.is_file():
            src = next(iter(sorted(ctx.inputs.rglob("FAACIFP18"))), None)
    if src is None:
        return _not_run("Needs the extracted CIFP (--inputs) to count the source file's "
                        "sectors.", "no-inputs")
    from .build.airspace_cifp import iter_sectors
    counts: dict[str, int] = {}
    records: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for recs in iter_sectors(src):
        r = recs[0]
        counts[r.section[1]] = counts.get(r.section[1], 0) + 1
        records[r.section] = records.get(r.section, 0) + len(recs)
        k = f"{r.section}-{r.airspace_type}"
        by_type[k] = by_type.get(k, 0) + 1
    ctx.metrics["ASP-003"] = {"source_sectors": counts, "source_primary_records": records,
                              "rows": built, "by_type": by_type}
    parts = [f"U{s} {built.get(s, 0):,} rows / {counts.get(s, 0):,} source sectors"
             for s in ("C", "R")]
    ok = all(built.get(s, 0) == counts.get(s, 0) for s in set(built) | set(counts))
    return ("pass" if ok else "fail", "; ".join(parts), "")


def _msa_records(ctx):
    """The pack's MSA records, one per source PS record: ``record`` is the
    verbatim JSON every sector row of one record shares."""
    out = {}
    for rec, seq, start, end, alt, radius in ctx.con.execute(
            "SELECT record, sector_seq, bearing_start, bearing_end, altitude_ft, radius_nm "
            "FROM msa ORDER BY rowid"):
        out.setdefault(rec, []).append((seq, start, end, alt, radius))
    return out


def _msa_label(record: str) -> str:
    f = json.loads(record)["fields"]
    code = f["multiple_code"].strip()
    return f"{f['airport'].strip()} {f['centre_ident'].strip()}{'/' + code if code else ''}"


def _c_msa_census(ctx):
    if "msa" not in ctx.tables:
        return ("fail", "no msa table", "")
    if ctx.inputs is None:
        return _not_run("Needs the source FAACIFP18 (--inputs) to count its PS records. The "
                        "nightly gate passes the extracted CIFP.")
    from . import arinc424
    from .build.procedures import ProceduresBuildError, _find_cifp_file
    try:
        cifp = _find_cifp_file(Path(ctx.inputs))
    except ProceduresBuildError as e:
        return ("fail", str(e).replace(str(ctx.inputs), "<inputs>"), "")
    primaries = continuations = 0
    for line in arinc424.read_records(cifp):
        if line[4:5] == "P" and line[12:13] == "S":
            if line[38:39] in ("0", "1"):
                primaries += 1
            else:
                continuations += 1
    records = _msa_records(ctx)
    stored_cont = sum(len(json.loads(r)["continuations"]) for r in records)
    ctx.metrics["PROC-MSA-001"] = {"source_primaries": primaries,
                                   "source_continuations": continuations,
                                   "records": len(records), "continuations": stored_cont}
    measured = (f"{len(records):,} of {primaries:,} PS records; {stored_cont:,} of "
                f"{continuations:,} continuations")
    ok = len(records) == primaries and stored_cont == continuations
    return ("pass" if ok else "fail", measured, "")


def _msa_coverage_fault(sectors) -> str | None:
    seqs = [s[0] for s in sectors]
    if seqs != list(range(1, len(sectors) + 1)):
        return f"sector numbers {seqs}"
    bounds = [(s[1], s[2]) for s in sectors]
    if any(b is None or not 0 <= b <= 360 for pair in bounds for b in pair):
        return f"bearing missing or outside 0-360 {bounds}"
    if len(bounds) == 1:
        return None if bounds[0][0] % 360 == bounds[0][1] % 360 else f"one sector {bounds[0]}"
    if any(start % 360 == end % 360 for start, end in bounds):
        return f"zero-width sector {bounds}"
    for i, (_, end) in enumerate(bounds):
        if end % 360 != bounds[(i + 1) % len(bounds)][0] % 360:
            return f"gap or overlap {bounds}"
    span = sum((end - start) % 360 for start, end in bounds)
    return None if span == 360 else f"sectors span {span} degrees {bounds}"


def _c_msa_coverage(ctx):
    if "msa" not in ctx.tables:
        return ("fail", "no msa table", "")
    bad = []
    for record, sectors in _msa_records(ctx).items():
        fault = _msa_coverage_fault(sectors)
        if fault:
            bad.append(f"{_msa_label(record)}: {fault}")
    measured = f"{len(bad)} records" + (f" ({'; '.join(bad[:5])})" if bad else "")
    return ("fail" if bad else "pass", measured, "")


def _c_msa_values(ctx):
    if "msa" not in ctx.tables:
        return ("fail", "no msa table", "")

    def count(where):
        return ctx.con.execute(f"SELECT COUNT(*) FROM msa WHERE {where}").fetchone()[0]

    alt_bad = count(f"altitude_ft IS NULL OR altitude_ft NOT BETWEEN {MSA_ALT_MIN_FT} "
                    f"AND {MSA_ALT_MAX_FT}")
    rad_bad = count(f"radius_nm IS NULL OR radius_nm NOT BETWEEN {MSA_RADIUS_MIN_NM} "
                    f"AND {MSA_RADIUS_MAX_NM}")
    ref_bad = count("mag_true IS NULL OR mag_true NOT IN ('M', 'T')")
    lo, hi, rlo, rhi = ctx.con.execute(
        "SELECT MIN(altitude_ft), MAX(altitude_ft), MIN(radius_nm), MAX(radius_nm) "
        "FROM msa").fetchone()
    measured = (f"{alt_bad} altitudes, {rad_bad} radii, {ref_bad} bearing references out of "
                f"bounds (altitudes {lo}-{hi} ft, radii {rlo}-{rhi} nm)")
    return ("fail" if alt_bad or rad_bad or ref_bad else "pass", measured, "")


def _c_msa_unresolved(ctx):
    if "msa" not in ctx.tables:
        return ("fail", "no msa table", "")
    records = _msa_records(ctx)
    unresolved = sorted({_msa_label(r) for r, in ctx.con.execute(
        "SELECT DISTINCT record FROM msa WHERE centre_lat IS NULL OR centre_lon IS NULL")})
    ctx.metrics["PROC-MSA-004"] = {"unresolved": len(unresolved), "records": len(records)}
    frac = len(unresolved) / len(records) if records else 0.0
    measured = (f"{len(unresolved):,} of {len(records):,} records"
                + (f" ({', '.join(unresolved[:10])})" if unresolved else ""))
    return ("warn" if frac > MSA_UNRESOLVED_MAX_FRACTION else "pass", measured, "")


def _c_mora_count(ctx):
    if "grid_mora" not in ctx.tables or "grid_mora_bands" not in ctx.tables:
        return ("fail", "no grid_mora / grid_mora_bands table", "")
    bands, cells = ctx.tables["grid_mora_bands"], ctx.tables["grid_mora"]
    expect = bands * arinc424.MORA_CELLS_PER_BAND
    bad = cells != expect or bands < PROC_MORA_BAND_FLOOR
    measured = (f"{cells:,} cells for {bands} bands (expected {expect:,}); "
                f"band floor {PROC_MORA_BAND_FLOOR}")
    return ("fail" if bad else "pass", measured, "")


def _c_mora_unique(ctx):
    if "grid_mora" not in ctx.tables:
        return ("fail", "no grid_mora table", "")
    n = _dup_count(ctx.con, "grid_mora", ("lat_sw", "lon_sw"))
    return ("fail" if n else "pass", f"{n} duplicate cells", "")


def _c_mora_bounds(ctx):
    if "grid_mora" not in ctx.tables:
        return ("fail", "no grid_mora table", "")
    lo, hi, n_out, n_garbled = ctx.con.execute(
        "SELECT MIN(mora_ft), MAX(mora_ft), "
        "SUM(mora_ft IS NOT NULL AND (mora_ft < ? OR mora_ft > ?)), "
        "SUM(mora_ft IS NULL AND raw <> ?) FROM grid_mora",
        (PROC_MORA_MIN_FT, PROC_MORA_MAX_FT, arinc424.MORA_UNKNOWN)).fetchone()
    n_out, n_garbled = n_out or 0, n_garbled or 0
    measured = (f"{n_out} cells outside {PROC_MORA_MIN_FT:,}-{PROC_MORA_MAX_FT:,} ft "
                f"(range {lo}-{hi}); {n_garbled} fields neither digits nor UNK")
    return ("fail" if n_out or n_garbled else "pass", measured, "")


def _c_mora_unknown(ctx):
    if "grid_mora" not in ctx.tables:
        return ("fail", "no grid_mora table", "")
    total = ctx.tables["grid_mora"]
    unk = ctx.con.execute("SELECT COUNT(*) FROM grid_mora WHERE mora_ft IS NULL").fetchone()[0]
    ctx.metrics["PROC-MORA-004"] = {"unknown": unk, "cells": total}
    measured = f"{unk:,} of {total:,} cells UNK (stored NULL, not filled)"
    prev = ctx.prev_metric("PROC-MORA-004")
    if prev is None:
        return ("pass", measured, "")
    grew = unk > prev.get("unknown", 0)
    return ("warn" if grew else "pass", f"{measured} (previous {prev.get('unknown', 0):,})", "")


# --- checks: terrain --------------------------------------------------------

_TILE = r"[NS]\d{2}[EW]\d{3}"
_TER_MEMBER = re.compile(
    rf"^(?:[NS]\d{{2}}/{_TILE}\.(?:hgt|wmask)"
    rf"|\.mip/\d+/[NS]\d{{2}}/{_TILE}\.(?:hgt|wmask)"
    rf"|\.mip/mosaic/L\d+\.(?:hgt|json|wmask)"
    rf"|pack_meta\.json)$")


def _square_side(size: int) -> int | None:
    n = int(round((size / 2) ** 0.5))
    return n if n * n * 2 == size else None


def _terrain_members(ctx):
    with zipfile.ZipFile(ctx.path) as z:
        return {i.filename: i.file_size for i in z.infolist() if not i.is_dir()}


def _mosaic_shape(z: zipfile.ZipFile, hgt: str):
    try:
        meta = json.loads(z.read(hgt[:-4] + ".json"))
        return int(meta["rows"]), int(meta["cols"])
    except Exception:
        return None


def _c_ter_layout(ctx):
    bad = []
    with zipfile.ZipFile(ctx.path) as z:
        members = {i.filename: i.file_size for i in z.infolist() if not i.is_dir()}
        for name, size in sorted(members.items()):
            if not _TER_MEMBER.match(name):
                bad.append(f"{name} (unexpected name)")
                continue
            if name.startswith(".mip/mosaic/"):
                if name.endswith(".hgt"):
                    shape = _mosaic_shape(z, name)
                    if shape is None or shape[0] * shape[1] * 2 != size:
                        bad.append(f"{name} (size does not match its .json)")
                elif name.endswith(".wmask"):
                    shape = _mosaic_shape(z, name[:-6] + ".hgt")
                    if shape is None or -(-shape[1] // 8) * shape[0] != size:
                        bad.append(f"{name} (size does not match its .hgt)")
                continue
            if name.endswith(".hgt") and _square_side(size) is None:
                bad.append(f"{name} (not a square int16 grid)")
            elif name.endswith(".wmask"):
                side = _square_side(members.get(name[:-6] + ".hgt", 0))
                if side is None or -(-side // 8) * side != size:
                    bad.append(f"{name} (size does not match its .hgt)")
    if bad:
        return ("fail", f"{len(bad)} of {len(members)} members malformed, e.g. "
                + "; ".join(bad[:3]), "")
    return ("pass", f"{len(members):,} members, 0 malformed", "")


def _native_tiles(members) -> list[str]:
    return sorted(n for n in members if n.endswith(".hgt") and not n.startswith(".mip/"))


def _c_ter_bbox(ctx):
    members = _terrain_members(ctx)
    native = _native_tiles(members)
    mip = sum(1 for n in members if n.startswith(".mip/") and n.endswith(".hgt")
              and not n.startswith(".mip/mosaic/"))
    ctx.metrics["TER-002"] = {"native_tiles": len(native), "mip_tiles": mip}
    if not native:
        # The exemption follows the pack's identity, never its contents: a
        # regional pack that lost every tile must not pass as a mosaic (R2).
        from .make_terrain import MOSAIC_PACK_ID
        if ctx.meta.id == MOSAIC_PACK_ID:
            return _not_run("This is the mosaic pack; it carries no native tiles.",
                            "mosaic-pack")
        return ("fail", f"0 native tiles in {ctx.meta.id}, which is not the mosaic pack", "")
    region = load_regions().get(ctx.meta.id.removeprefix("terrain-"))
    if region is None:
        return _not_run(f"Pack id {ctx.meta.id!r} names no region in regions.yaml.")
    outside = [n for n in native if not region.contains(*tile_sw_corner(Path(n).stem))]
    prev = ctx.prev_metric("TER-002")
    vs = f" (previous {prev['native_tiles']:,})" if prev else ""
    measured = f"{len(native):,} native tiles{vs}, {len(outside)} outside {region.key}"
    return ("fail" if outside else "pass", measured, "")


def _c_ter_tileset(ctx):
    tiles = sorted(Path(n).stem for n in _native_tiles(_terrain_members(ctx)))
    ctx.metrics["TER-006"] = tiles
    if ctx.previous is None:
        return _no_previous(ctx, f" This edition has {len(tiles):,} native tiles; the next "
                            "edition compares against them.")
    prev = ctx.prev_metric("TER-006")
    if prev is None:
        return _not_run("The previous record lists no native tile set (metrics TER-006); "
                        "pass the previous pack instead of its report.", "no-previous-tiles")
    lost = sorted(set(prev) - set(tiles))
    gained = len(set(tiles) - set(prev))
    cycle = ((ctx.previous.get("pack") or {}).get("cycle")) or "?"
    measured = (f"{len(tiles):,} native tiles (previous edition {cycle}: {len(prev):,}); "
                f"lost {len(lost)}{': ' + ', '.join(lost[:10]) if lost else ''}"
                f"{' ...' if len(lost) > 10 else ''}; gained {gained}")
    return ("fail" if lost else "pass", measured, "")


def _c_ter_wmask(ctx):
    from .make_terrain import missing_wmask_members
    missing = missing_wmask_members(ctx.path)
    return ("fail" if missing else "pass", len(missing), "")


# No-data markers in a terrain .hgt: the SRTM/GLO-30 void, and the water
# sentinel pyEfis's mosaic builder writes where it has no tile.
_HGT_VOID = (-32768, -9999)


def _hgt_stats(raw: bytes):
    """(min, max, void samples, samples) of a big-endian int16 grid, with
    the no-data markers left out of min and max."""
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "little":
        a.byteswap()
    void = sum(a.count(v) for v in _HGT_VOID)
    vals = sorted(set(a).difference(_HGT_VOID))
    if not vals:
        return 0, 0, void, len(a)
    return vals[0], vals[-1], void, len(a)


def _c_ter_elev(ctx):
    with zipfile.ZipFile(ctx.path) as z:
        names = z.namelist()
        native = _native_tiles(names)
        if native:
            step = max(1, len(native) // TER_SAMPLE_TILES)
            sample = native[::step][:TER_SAMPLE_TILES]
            label = f"{len(sample)} of {len(native):,} native tiles"
        else:
            mosaic = sorted((n for n in names if n.startswith(".mip/mosaic/L") and n.endswith(".hgt")),
                            key=lambda n: int(Path(n).stem[1:]))
            if not mosaic:
                return _not_run("No .hgt member to sample.")
            sample = mosaic[-1:]
            label = f"mosaic level {Path(sample[0]).stem}"
        lo, hi, void, total = None, None, 0, 0
        for name in sample:
            mn, mx, v, n = _hgt_stats(z.read(name))
            lo = mn if lo is None else min(lo, mn)
            hi = mx if hi is None else max(hi, mx)
            void += v
            total += n
    frac = void / total if total else 0.0
    measured = f"{label}: min {lo:,} m, max {hi:,} m, void {frac * 100:.1f}%"
    # A mosaic spans sea as well as land, so its no-data share says nothing;
    # only native tiles are held to the void limit.
    ok = (TER_ELEV_MIN_M <= lo and hi <= TER_ELEV_MAX_M
          and (not native or frac <= TER_VOID_MAX_FRACTION))
    return ("pass" if ok else "warn", measured, "")


# --- checks: OSM ------------------------------------------------------------

_BBOX = ("min_lat", "max_lat", "min_lon", "max_lon")


def _bbox_tables(ctx):
    return [t for t in ctx.tables if set(_BBOX) <= _columns(ctx.con, t)]


def _c_osm_regions(ctx):
    tables = _bbox_tables(ctx)
    if not tables:
        return _not_run("No table with a bounding box to bin by region.")
    regions = load_regions()
    counts = {}
    for t in tables:
        sums = ", ".join(
            f"SUM(CASE WHEN (min_lat + max_lat) / 2 >= {r.bbox[0]} AND (min_lat + max_lat) / 2 < {r.bbox[2]} "
            f"AND (min_lon + max_lon) / 2 >= {r.bbox[1]} AND (min_lon + max_lon) / 2 < {r.bbox[3]} "
            f"THEN 1 ELSE 0 END)" for r in regions.values())
        row = ctx.con.execute(f'SELECT {sums} FROM "{t}"').fetchone()
        counts[t] = {k: int(v or 0) for k, v in zip(regions, row)}
    ctx.metrics["OSM-001"] = counts
    prev = ctx.prev_metric("OSM-001")
    if prev is None:
        return _no_previous(ctx, " This edition's per-region counts are recorded "
                            "for the next one.")
    worst, emptied = None, False
    for t, by_region in counts.items():
        for k, new in by_region.items():
            old = (prev.get(t) or {}).get(k, 0)
            if old < OSM_REGION_MIN:
                continue
            frac = (new - old) / old
            emptied = emptied or new == 0
            if worst is None or frac < worst[0]:
                worst = (frac, t, k, old, new)
    if worst is None:
        return _not_run(f"No region had {OSM_REGION_MIN} or more features in the previous edition.")
    frac, t, k, old, new = worst
    lead = "largest drop" if frac < 0 else "no region dropped; smallest growth"
    measured = f"{lead}: {t} {k} {_pct(new, old)} ({old:,} -> {new:,})"
    if emptied:
        return ("fail", measured, "")
    return ("warn" if -frac > OSM_REGION_DROP_WARN else "pass", measured, "")


def _c_hwy_schema(ctx):
    try:
        detected = packmeta.detect_highways_schema_version(ctx.path)
    except ValueError as e:
        return ("fail", str(e).replace(str(ctx.path), "<pack>"), "")
    declared = ctx.meta.schema_version
    measured = f"declared {declared}, columns say {detected}"
    return ("pass" if detected == declared else "fail", measured, "")


def _c_osm_bbox(ctx):
    tables = _bbox_tables(ctx)
    if not tables:
        return _not_run("No table with a bounding box.")
    bad = 0
    for t in tables:
        bad += ctx.con.execute(
            f'SELECT COUNT(*) FROM "{t}" WHERE min_lat IS NULL OR max_lat IS NULL '
            f"OR min_lon IS NULL OR max_lon IS NULL OR min_lat > max_lat OR min_lon > max_lon "
            f"OR min_lat < -90 OR max_lat > 90 OR min_lon < -180 OR max_lon > 180").fetchone()[0]
    return ("fail" if bad else "pass", bad, "")


# --- the catalogue ----------------------------------------------------------

def _fixed_not_run(note: str):
    return lambda ctx: _not_run(note)


def _names(points) -> str:
    names = [p[0] for p in points]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _probe_coverage() -> str:
    d = device_probe
    return (f"US kinds, {len(d._US_POINTS)} CONUS metros ({_names(d._US_POINTS)}); "
            f"airports (Canada), {_names(d._CA_POINTS)}; water, {_names(d._WATER_POINTS)}; "
            f"navaids, idents {', '.join(d._NAVAID_IDENTS)}; procedures, airways "
            f"{', '.join(d._AIRWAY_IDENTS)}; terrain, one native tile or the coarsest "
            "mosaic level")


_COMMON = [
    Check("PACK-001", "pack_meta is readable and names a known kind", True,
          "pack_meta present; kind is one the device knows",
          "A pack_meta that is well formed but names the wrong id or cycle for the "
          "bytes inside.", _c_meta),
    Check("PACK-002", "Attribution and license are recorded in pack_meta", True,
          "both non-empty",
          "A license tag or attribution that is present but wrong for the data.",
          _c_meta_fields),
    Check("PACK-003", "Origin label is federal, community or user-supplied", True,
          "one of federal / community / user-supplied",
          "Whether the label matches where the data really came from. For a kind with "
          "a default label (ORIGIN_BY_KIND in packtools/report.py) the label comes from "
          "that table, not from the builder or the pack, so the check is tautological "
          "and always passes; measured says which. With --origin it shows only that the "
          "label given is one of the three.", _c_origin, "AER-699"),
    Check("PACK-004", "Upstream sources are recorded", False,
          "at least one source, each with a non-empty edition and a 64-hex sha256",
          "A recorded source that is not the one the build actually used, and an "
          "edition or sha256 that is well formed but wrong.",
          _c_sources),
    Check("ACC-001", "Device acceptance: pyEfis's own reader opens the pack and "
          "answers a fixed query set", True,
          "the reader opens the pack and every query returns data",
          "Wrong values the reader accepts. It shows the device can open and query "
          "these bytes, not that what it returns is right. The queries cover fixed "
          "points only: " + _probe_coverage() + ". So it cannot catch a missing Alaska, "
          "Hawaii or Puerto Rico, or any state or province that holds none of those "
          "points.", _c_device),
]

_SQLITE = [
    Check("SQL-001", "SQLite structure is sound (PRAGMA quick_check)", True, "ok",
          "Wrong data in a structurally sound file, and an index whose contents do not "
          "match its table: quick_check does not verify index contents, which is "
          "SQLite's documented difference from integrity_check. IDX-001 covers the "
          "R-trees.", _c_quick_check),
    Check("ROW-001", "No data table is empty", True,
          "0 empty tables, apart from those declared empty by design for the kind "
          "(water: waterway_lines)",
          "A table that has rows but is missing most of them.", _c_empty),
    Check("ROW-002", "Row counts against the previous edition", False,
          f"Provisional threshold: no table moves more than {ROW_DELTA_WARN:.0%}; a larger "
          "move needs a build note",
          "A change that keeps counts steady (rows replaced by wrong rows), any defect "
          "already present in the previous edition, and a loss under the threshold (for "
          "obstacles at 10%, about 65,000 rows).", _c_row_delta,
          provisional=PROVISIONAL),
    Check("ROW-003", "No table of the previous edition has disappeared", True,
          "every table in the previous edition is still present, unless schema_version "
          "changed",
          "A table dropped together with a schema_version bump that should not have "
          "been; the bump is taken as the declaration that the drop is intended.",
          _c_tables_kept, "AER-4102", exempt=(FIRST_EDITION,)),
]

_FEDERAL = [
    Check("SG-001", "schema_guard layer 1: input CSV structure matches the committed snapshot",
          True, "no added, removed, retyped or widened column",
          "A change in the values inside an unchanged column layout.",
          _guard_layer(1), "NASR 26-01"),
    Check("SG-002", "schema_guard layer 2: every column the builder reads is present",
          True, "all required headers present",
          "A column that is present but filled with wrong or blank values.",
          _guard_layer(2), "NASR 26-01"),
    Check("SG-003", "schema_guard layer 3: policed columns hold only known values",
          True, "no unknown value in a policed column",
          "A known value used with a new meaning, and every column the contract "
          "does not police.", _guard_layer(3), "NASR 26-01"),
    Check("SG-004", "schema_guard layer 4: output tables clear their row floors",
          True, "every floored table at or above its floor (half an observed build)",
          "A loss smaller than half an observed build, and a table with enough rows "
          "that are wrong.", _guard_layer(4), "NASR 26-01"),
]

_COORD_CHECKS = [
    Check("GEO-001", "Coordinates are present, valid and not at 0,0", True,
          "0 bad coordinates. On point tables a NULL latitude or longitude is bad; on "
          "procedure and airway legs a leg with no fix position is left to PROC-FIX-001",
          "A plausible coordinate in the wrong place, such as a transposed digit that "
          "still lands on land. On airway_legs, a leg with a NULL fix position (no "
          "check counts those).", _c_coords_valid),
    Check("UNQ-001", "Identifiers that must be unique are unique", True, "0 duplicates",
          "Two different things given the same correct-looking identifier in "
          "different tables, and a wrong identifier that happens to be unique.",
          _c_unique),
]

_FEDERAL_GEO = [
    Check("GEO-002", "Points outside the US coverage envelope, against the previous edition",
          False,
          f"Provisional threshold: no growth beyond {GEO_OUTSIDE_GROWTH_ROWS} points or "
          f"{GEO_OUTSIDE_GROWTH_FRACTION:.0%}, whichever is larger. FAA files carry real "
          "oceanic and foreign points, so the count is tracked, not forbidden",
          "A point moved to the wrong place inside the envelope; the envelope is a "
          "handful of boxes, not a coastline.", _c_envelope, provisional=PROVISIONAL),
]

_KIND_CHECKS: dict[str, list[Check]] = {
    "obstacles": [
        Check("OBS-001", "The obstacles table clears its row floor", True,
              "at or above the floor: half an observed build (half of the 657,181 "
              "obstacles in 261001)",
              "A loss smaller than half an observed build (ROW-002 tracks that against "
              "the previous edition, as a warn), and rows that are present but wrong.",
              _c_row_floor, "AER-4102"),
    ],
    "navaids": [
        Check("UNQ-002", "Identifiers the FAA reuses, against the previous edition", False,
              "no more reused navaid idents or airway sequence numbers than the previous "
              "edition", "Whether any one reuse is legitimate; the check only counts them.",
              _c_reused),
    ],
    "procedures": [
        Check("PROC-FIX-001", "Procedure legs that name a fix the build could not place",
              False,
              f"at most {PROC_UNRESOLVED_MAX_FRACTION:.1%} of legs: a regression bound that "
              "carries forward the 0.47% observed in 2610 (950 of 201,030) and 2611 (941 of "
              "200,853). It does not accept those legs as correct; it stops the share "
              "growing",
              "A fix placed at the wrong coordinates. Legs with no fix by design (for "
              "example heading legs) are not counted.", _c_unresolved),
        Check("PROC-AWY-001", "Airway idents are unique", True, "0 duplicate idents",
              "Two areas' airways merged under one ident row; PROC-AWY-002 looks for "
              "that.", _c_awy_unique, "AER-1979"),
        Check("PROC-AWY-002", "No airway mixes legs from two areas (repeated sequence "
              "numbers)", True, "0 airways",
              "Two areas' legs merged with sequence numbers that do not collide, and an "
              "airway whose legs are all present but in the wrong order.",
              _c_awy_collision, "AER-1979"),
        Check("PROC-MSA-001", "Every PS record in the source file is in the msa table", True,
              "as many MSA records (and continuation records) in the pack as PS records in "
              "the source FAACIFP18",
              "A record that is present but decoded wrong; PROC-MSA-002 and -003 look for "
              "that.", _c_msa_census, "AER-4446"),
        Check("PROC-MSA-002", "Each MSA's sectors cover 360 degrees with no gap or overlap",
              True, "0 records: every sector ends where the next begins, the last closes "
              "on the first, and together they span 360 degrees (one sector: start equals "
              "end)",
              "A consistent set of sectors with the wrong altitude in one of them, and a "
              "column shift that keeps the bearings contiguous.", _c_msa_coverage, "AER-4446"),
        Check("PROC-MSA-003", "MSA altitudes, radii and bearing references are plausible",
              True, f"altitudes {MSA_ALT_MIN_FT:,}-{MSA_ALT_MAX_FT:,} ft, radii "
              f"{MSA_RADIUS_MIN_NM}-{MSA_RADIUS_MAX_NM} nm, bearings referenced M or T",
              "A wrong value inside the bounds, such as 5200 coded where the plate says "
              "5900.", _c_msa_values, "AER-4446"),
        Check("PROC-MSA-004", "MSA centre fixes the build could not place", False,
              f"at most {MSA_UNRESOLVED_MAX_FRACTION:.1%} of MSA records, every one listed: "
              "a regression bound that carries forward the 1 of 6,058 observed in 2611 "
              "(KHRJ HQT, an NDB the file does not define). It does not accept those "
              "records as correct; they are stored with no position, never a guessed one",
              "A centre placed at the wrong coordinates.", _c_msa_unresolved, "AER-4446"),
        Check("PROC-MORA-001", "Grid MORA has 30 cells per band", True,
              f"cells = 30 x bands, and at least {PROC_MORA_BAND_FLOOR} bands (half the "
              "241 in 2611)",
              "A band dropped whole because its corner did not decode, if fewer than "
              "half go; a band placed at the wrong corner.", _c_mora_count, "AER-4447"),
        Check("PROC-MORA-002", "No Grid MORA cell appears twice", True, "0 duplicate cells",
              "A band placed at a wrong but unclaimed corner.", _c_mora_unique, "AER-4447"),
        Check("PROC-MORA-003", "Grid MORA values are plausible", True,
              f"every published value within {PROC_MORA_MIN_FT:,}-{PROC_MORA_MAX_FT:,} ft, "
              "and every field either three digits or UNK",
              "A plausible but wrong value, and a right value in the wrong cell.",
              _c_mora_bounds, "AER-4447"),
        Check("PROC-MORA-004", "Grid MORA cells the FAA marks UNK", False,
              "reported; warns if more cells are UNK than in the previous edition",
              "Whether any one UNK is expected. UNK cells are stored as NULL and never "
              "filled.", _c_mora_unknown, "AER-4447"),
        Check("PROC-GLD-001", "FAACIFP18 golden fixture", False,
              "the fixture's procedures decode to their published legs",
              "Everything outside the fixture's handful of procedures.",
              _fixed_not_run("The golden fixture runs in the unit tests against the "
                             "parser (tests/test_build_procedures.py), not against a "
                             "built pack. Catalogue v2 (TV7) moves it here.")),
        Check("PROC-DIFF-001", "Independent differential against a commercial database",
              False, "no unexplained differences",
              "Errors that both databases share.",
              _fixed_not_run("Not yet run (AER-1702). Until it is, nothing independent "
                             "of the FAA source has been compared against this pack's "
                             "procedures.")),
    ],
    "airspace": [
        Check("ASP-001", "Every airspace geometry is a closed, simple polygon", True,
              "0 bad polygons: GeoJSON Polygon, every ring at least 4 positions, first equal "
              "to last, positions in range, no edge crossing another",
              "A valid polygon in the wrong place or of the wrong shape (an arc drawn the "
              "wrong way round still closes). Edges are tested as straight lines in "
              "longitude/latitude, the way the ring is stored.", _c_asp_polygons, "AER-4445"),
        Check("ASP-002", "No sector's floor is above its ceiling", True,
              "0 inverted sectors, and every limit readable. Feet MSL and flight levels are "
              "compared as feet; a surface floor or an unlimited ceiling compares with "
              "anything",
              "A floor below its ceiling that is still wrong, and any sector with an AGL "
              "limit against an MSL one (counted, not compared: which is higher depends on "
              "the terrain beneath).", _c_asp_limits, "AER-4445"),
        Check("ASP-003", "CIFP sectors reconcile with the source file", True,
              "one row per UC and per UR sector in the FAACIFP18 the pack was built from",
              "A sector that is present but wrong, and a record the parser never "
              "recognised as section U at all (the count and the rows come from the same "
              "reader).", _c_asp_reconcile, "AER-4445", exempt=(ASP_NOT_CIFP,)),
    ],
    "terrain": [
        Check("TER-001", "Every member is a known terrain file of a valid size", True,
              "0 malformed members",
              "A tile of the right size holding wrong elevations.", _c_ter_layout),
        Check("TER-002", "Native tiles fall inside the pack's region", True,
              "0 tiles outside the region bbox; a regional pack with no native tiles fails "
              "(only the terrain-mosaic pack, by id, carries none)",
              "A tile missing from inside the region (ocean tiles are absent by design, "
              "so the check cannot tell a gap from sea).", _c_ter_bbox,
          exempt=(("mosaic-pack", "the terrain-mosaic pack (by pack_meta.id) carries no "
                                  "native tiles to place"),)),
        Check("TER-006", "No native tile of the previous edition is missing", True,
              "every native tile in the previous edition is still present. Applies across "
              "Copernicus editions too: a new edition that drops a tile fails",
              "A tile that is present but holds wrong or older elevations, and a tile that "
              "was already missing from the previous edition.",
              _c_ter_tileset, "AER-4102", exempt=(FIRST_EDITION,)),
        Check("TER-003", "Every masked .hgt carries its .wmask water mask", True,
              "0 missing masks",
              "A mask that is present but wrong.", _c_ter_wmask, "AER-2287"),
        Check("TER-004", "Elevation range and void fraction on a sample of tiles", False,
              f"{TER_ELEV_MIN_M} m to {TER_ELEV_MAX_M:,} m, void at most "
              f"{TER_VOID_MAX_FRACTION:.0%}",
              "Anything in a tile that was not sampled, and wrong elevations that stay "
              "inside the plausible range.", _c_ter_elev),
        Check("TER-005", "Spot heights against published airport field elevations", False,
              "within tolerance of the FAA field elevation",
              "Errors away from airports.",
              _fixed_not_run("Planned for catalogue v2 (TV7).")),
    ],
}

_OSM_CHECKS = [
    Check("OSM-001", "Feature counts per region against the previous edition", True,
          f"Provisional threshold: no region with {OSM_REGION_MIN}+ features goes to zero "
          f"(fail); no region drops more than {OSM_REGION_DROP_WARN:.0%} (warn)",
          "A loss smaller than a region (one state inside a multi-state region), and "
          "features that are present but wrong.", _c_osm_regions, "makerplane-data#17",
          exempt=(FIRST_EDITION,), provisional=PROVISIONAL),
    Check("OSM-002", "Feature counts per state or province against the previous edition",
          False, "no state goes to zero",
          "Losses inside a state.",
          _fixed_not_run("Needs state and province boundaries; planned for catalogue v2 "
                         "(TV7). OSM-001 bins by region meanwhile.")),
    Check("OSM-004", "Every feature's bounding box is well formed", True, "0 bad boxes",
          "A well-formed box around wrong geometry.", _c_osm_bbox),
    Check("OSM-005", "Coverage map image", False, "an image a person can look at",
          "Anything; it is for a person to read, not a test.",
          _fixed_not_run("Rendered by the report site (TV5).")),
]

_RTREE_CHECK = Check(
    "IDX-001", "Every spatial index (R-tree) indexes every row of its table", True,
    "each declared R-tree present, its row count equal to its base table's, and "
    "rtreecheck() ok; no undeclared R-tree",
    "An index that holds the right number of rows with wrong boxes or ids. rtreecheck "
    "tests the tree's internal consistency, not that each box encloses its feature.",
    _c_rtree, "AER-4102")

_HWY_SCHEMA = Check(
    "OSM-003", "highway_lines columns agree with the declared schema_version", True,
    "declared schema_version equals the one the column set implies",
    "A column whose meaning changed without its name changing.", _c_hwy_schema, "AER-1715")


def catalogue(kind: str, *, is_zip: bool) -> list[Check]:
    """The v1 checks for one pack kind, in report order."""
    checks = list(_COMMON)
    if not is_zip:
        checks += _SQLITE
    if kind in _GUARD_BUILDER:
        checks += _FEDERAL
    if kind in _COORDS:
        checks += _COORD_CHECKS
    if kind in _FEDERAL_SQLITE:
        checks += _FEDERAL_GEO
    checks += _KIND_CHECKS.get(kind, [])
    if kind in _RTREES and not is_zip:
        checks.append(_RTREE_CHECK)
    if kind in _OSM:
        checks += _OSM_CHECKS
        if kind == "highways":
            checks.append(_HWY_SCHEMA)
    return checks


#: Kinds whose packs are zip archives rather than SQLite files.
_ZIP_KINDS = ("terrain", "plates")


def all_checks() -> dict[str, list[Check]]:
    """Every kind's catalogue, for ``--list-checks``, QA review and the gate."""
    return {k: catalogue(k, is_zip=k in _ZIP_KINDS) for k in KINDS}


# --- device acceptance probe ------------------------------------------------

def resolve_pyefis_src(explicit: str | Path | None = None) -> Path | None:
    """The pyEfis ``src`` dir to probe with: ``explicit``, else
    ``PYEFIS_SRC_DIR``, else ``PYEFIS_TOOLS_DIR``'s sibling ``src``."""
    cands = [explicit, os.environ.get("PYEFIS_SRC_DIR")]
    tools = os.environ.get("PYEFIS_TOOLS_DIR")
    if tools:
        cands.append(Path(tools).parent / "src")
    for c in cands:
        if c and (Path(c) / "pyefis").is_dir():
            return Path(c)
    return None


def run_device_probe(kind: str, path: Path, pyefis_src: Path, *, timeout: int = 900) -> dict:
    """Run ``packtools.device_probe`` in its own process with pyEfis on
    ``PYTHONPATH``. pyEfis is never imported into this process."""
    here = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(pyefis_src), str(here), env.get("PYTHONPATH", "")) if p)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        proc = subprocess.run([sys.executable, "-m", "packtools.device_probe", kind, str(path)],
                              capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"opened": False, "error": f"probe timed out after {timeout} s"}
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        err = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return {"opened": False, "unavailable": True,
                "error": f"probe exited {proc.returncode}: {err}"}


def _git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


# --- building a record ------------------------------------------------------

def _built_block(*, commit: str | None, builder_refs: dict, at: str | None) -> dict:
    gh = os.environ.get("GITHUB_ACTIONS") == "true"
    run_url = None
    if gh and os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ.get('GITHUB_REPOSITORY', REPO)}/actions/runs/"
                   f"{os.environ['GITHUB_RUN_ID']}")
    runner = "local"
    if gh:
        runner = ("self-hosted" if os.environ.get("RUNNER_ENVIRONMENT") == "self-hosted"
                  else "github-hosted")
    return {
        "at": at or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": REPO,
        "commit": commit or _git_head(Path(__file__).resolve().parent.parent),
        "builder_refs": builder_refs,
        "run_url": run_url,
        "runner": runner,
    }


def build_report(pack_path: str | Path, *, record: str = "build", origin: str | None = None,
                 previous: dict | None = None, inputs: str | Path | None = None,
                 pyefis_src: str | Path | None = None, probe: bool = True,
                 sources: list | None = None, notes: dict | None = None,
                 commit: str | None = None, builder_refs: dict | None = None,
                 built_at: str | None = None, first_edition: bool = False) -> dict:
    """Run the kind's v1 catalogue on one pack and return the record.

    ``previous`` is the previous edition's record (see :func:`previous_from`);
    ``notes`` maps a check id to its build note (a ``warn`` without one
    blocks the publish, :func:`publish_blockers`). ``first_edition`` is the
    caller's declaration that the id has no earlier edition; it is the only
    thing that lets a blocking delta check be ``not-run`` in a build record."""
    if record not in RECORDS:
        raise ReportError(f"record must be one of {RECORDS}")
    if first_edition and previous is not None:
        raise ReportError("first_edition and previous are contradictory")
    path = Path(pack_path)
    try:
        meta = packmeta.read(path)
    except Exception as e:
        raise ReportError(f"cannot read pack_meta from {path.name}: {e}") from e
    is_zip = zipfile.is_zipfile(path)
    src = resolve_pyefis_src(pyefis_src) if probe else None
    refs = dict(builder_refs or {})
    if src is not None and "pyEfis" not in refs:
        sha = _git_head(src)
        if sha:
            refs["pyEfis"] = sha
    ctx = Ctx(path=path, meta=meta, kind=meta.kind, is_zip=is_zip,
              origin=origin if origin is not None else ORIGIN_BY_KIND.get(meta.kind, ""),
              previous=previous, inputs=Path(inputs) if inputs else None,
              pyefis_src=src, probe=probe, sources=list(sources or []),
              first_edition=first_edition, origin_given=origin is not None)
    notes = notes or {}
    checks = []
    try:
        for c in catalogue(meta.kind, is_zip=is_zip):
            raised, reason = False, ""
            try:
                status, measured, note, *rest = c.run(ctx)
                reason = rest[0] if rest else ""
            except Exception as e:     # a check that crashes is a failed check, never a silent one
                status, measured, note = "fail", f"check raised {type(e).__name__}: {e}", ""
                raised = True
            entry = {"id": c.id, "title": c.title, "status": status, "blocking": c.blocking,
                     "measured": measured, "expected": c.expected,
                     "cannot_catch": c.cannot_catch}
            if raised:
                entry["raised"] = True
            if status == "not-run" and reason:
                entry["not_run_reason"] = reason
            if c.since:
                entry["since"] = c.since
            if c.provisional:
                entry["provisional"] = c.provisional
            note = notes.get(c.id) or note
            if note:
                entry["note"] = note
            checks.append(entry)
        tables = {} if is_zip else ctx.tables
    finally:
        ctx.close()
    prev_tables = (previous or {}).get("tables") or {}
    summary = {"pass": 0, "warn": 0, "fail": 0, "not_run": 0}
    for c in checks:
        summary[c["status"].replace("-", "_")] += 1
    return {
        "schema": SCHEMA,
        "record": record,
        "pack": {"id": meta.id, "kind": meta.kind, "cycle": meta.cycle,
                 "sha256": sha256_file(path), "bytes": path.stat().st_size,
                 "schema_version": str(meta.schema_version)},
        "origin": ctx.origin,
        "built": _built_block(commit=commit, builder_refs=refs, at=built_at),
        "sources": ctx.sources,
        "previous": ({"cycle": previous["pack"]["cycle"], "sha256": previous["pack"]["sha256"]}
                     if previous else None),
        "checks": checks,
        "tables": {t: {"rows": n, "prev": (prev_tables.get(t) or {}).get("rows")}
                   for t, n in tables.items()},
        "metrics": ctx.metrics,
        "summary": summary,
        "gated": meta.kind not in UNGATED,
        **({"ungated": UNGATED[meta.kind]} if meta.kind in UNGATED else {}),
        "notice": NOTICE,
    }


def previous_from(path: str | Path, **kw) -> dict:
    """The previous edition's record: a ``report.json`` is read as is; a pack
    file is reported on now (without the device probe, which deltas do not
    need)."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        head = path.read_bytes()[:16].lstrip()
        if head.startswith(b"{"):
            return json.loads(path.read_text(encoding="utf-8"))
    return build_report(path, probe=False, **kw)


def to_bytes(report: dict) -> bytes:
    """Canonical bytes, as the manifest serializes (docs/pack_report.md).
    Hash these bytes; upload these bytes."""
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


# --- validating a record ----------------------------------------------------

def validate_report(obj: dict) -> list[str]:
    """Structural problems with a ``pack-report/1`` record, empty if none."""
    errs = []
    if obj.get("schema") != SCHEMA:
        errs.append(f"schema is not {SCHEMA}")
    if obj.get("record") not in RECORDS:
        errs.append("record is not build / retro / rejected")
    if obj.get("origin") not in ORIGINS:
        errs.append("origin is not federal / community / user-supplied")
    if obj.get("notice") != NOTICE:
        errs.append("notice is not the standing text")
    checks = obj.get("checks")
    if not isinstance(checks, list) or not checks:
        errs.append("checks is empty")
        checks = []
    tally = {"pass": 0, "warn": 0, "fail": 0, "not_run": 0}
    seen = set()
    for c in checks:
        cid = c.get("id", "?")
        if cid in seen:
            errs.append(f"{cid}: listed twice")
        seen.add(cid)
        st = c.get("status")
        if st not in STATUSES:
            errs.append(f"{cid}: status {st!r}")
            continue
        tally[st.replace("-", "_")] += 1
        if not (isinstance(c.get("cannot_catch"), str) and c["cannot_catch"].strip()):
            errs.append(f"{cid}: no cannot_catch")
        if (c.get("measured") is None) != (st == "not-run"):
            errs.append(f"{cid}: measured must be null exactly when not-run")
        if st == "not-run" and not c.get("note"):
            errs.append(f"{cid}: not-run without a note saying why")
    if obj.get("summary") != tally:
        errs.append("summary does not equal the tally of checks")
    return errs


def publish_blockers(report: dict) -> list[str]:
    """Why this record would stop a publish. Empty if none.

    The rules (AER-4102 C1, C2 and R1): only a ``build`` record can clear the
    gate; a check that raised blocks whatever its flag; a blocking check that
    failed, or did not run for a reason its catalogue entry does not declare,
    blocks; a warn with no build note blocks (plan section 4.3); an ungated
    kind blocks.

    Which checks exist, and which of them block, come from this code's
    catalogue for the pack's kind, never from the record being judged: a
    record cannot clear itself by flipping ``blocking``, dropping a check or
    changing its ``record`` type (R1). A catalogue check missing from the
    record blocks."""
    out = []
    rtype = report.get("record")
    if rtype != "build":
        out.append(f"record is {rtype}, not build: only a build record can clear the gate")
    kind = (report.get("pack") or {}).get("kind")
    cat = all_checks().get(kind)
    if cat is None:
        return out + [f"kind {kind!r} is not a known pack kind"]
    if kind in UNGATED:
        out.append(f"ungated: kind {kind} has no accepted check catalogue ({UNGATED[kind]})")
    by_id = {c.get("id"): c for c in report.get("checks") or []}
    for spec in cat:
        c = by_id.get(spec.id)
        if c is None:
            out.append(f"{spec.id} is in the {kind} catalogue but missing from the record")
        elif c.get("raised"):
            out.append(f"{c['id']} raised, so it measured nothing: {c['measured']}")
        elif c["status"] == "not-run" and spec.blocking:
            if c.get("not_run_reason") not in dict(spec.exempt):
                out.append(f"{c['id']} is blocking and did not run: {c.get('note', '')}")
        elif c["status"] == "fail" and spec.blocking:
            out.append(f"{c['id']} failed: {c['measured']}")
        elif c["status"] == "warn" and not c.get("note"):
            out.append(f"{c['id']} warns with no build note: {c['measured']}")
    return out


# --- --check: reproduce a published record ----------------------------------

@dataclass
class Comparison:
    id: str
    outcome: str      # reproduced | MISMATCH | not reproducible here | not run in record
    published: str
    here: str


def _fmt(c: dict | None) -> str:
    if c is None:
        return "(absent)"
    return f"{c['status']}: {c['measured']}" if c["status"] != "not-run" else "not-run"


def compare(published: dict, here: dict) -> tuple[list[Comparison], list[str]]:
    """Diff a re-run against a published record. Returns per-check rows and
    record-level mismatches (pack identity, table counts)."""
    problems = []
    for k in ("sha256", "bytes", "id", "cycle"):
        if published["pack"].get(k) != here["pack"].get(k):
            problems.append(f"pack.{k}: published {published['pack'].get(k)!r}, "
                            f"here {here['pack'].get(k)!r}")
    for t, v in (published.get("tables") or {}).items():
        got = (here.get("tables") or {}).get(t, {}).get("rows")
        if got != v.get("rows"):
            problems.append(f"tables.{t}: published {v.get('rows')}, here {got}")
    mine = {c["id"]: c for c in here["checks"]}
    rows = []
    for c in published["checks"]:
        h = mine.get(c["id"])
        if c["status"] == "not-run":
            outcome = "not run in record"
        elif h is None:
            outcome = "MISMATCH"
        elif h["status"] == "not-run":
            outcome = "not reproducible here"
        elif (h["status"], h["measured"]) == (c["status"], c["measured"]):
            outcome = "reproduced"
        else:
            outcome = "MISMATCH"
        here_s = _fmt(h)
        if outcome == "not reproducible here":
            here_s = f"not-run: {h.get('note', '')}"
        rows.append(Comparison(c["id"], outcome, _fmt(c), here_s))
    return rows, problems


def auspex_lines(published: dict) -> list[str]:
    """The black-box section, printed for the reader, never re-run."""
    a = published.get("auspex")
    if not a:
        return []
    out = [f"Auspex {a.get('check', '')} ({a.get('when', '')}): verdict "
           f"{a.get('verdict', '')} -- not reproducible here (Auspex)"]
    for f in a.get("findings") or []:
        loc = ", ".join(f"{k}={v}" for k, v in (f.get("locator") or {}).items())
        out.append(f"  finding at {loc}: {f.get('statement', '')}")
    return out
