# SPDX-License-Identifier: Apache-2.0
"""FAA CIFP section U -> sqlite ``airspace`` pack (AER-4445 / AIRSP1).

Reads the Controlled (``UC``: Class B/C/D) and Restrictive (``UR``:
restricted, prohibited, MOA, warning, alert, ...) airspace records from the
same FAACIFP18 file ``procedures-conus`` is built from, and writes the
``airspaces`` table the openAIP builder (``airspace.py``) already defines, so
one pyEfis reader serves both sources.

One row per **sector**. Class B and C airspace is a set of sectors (ARINC
"multiple code" A, B, C...), each with its own floor and ceiling; each
becomes its own row, and the rows of one airspace share
``properties_json.designation``. The row id is
``cifp-<section>-<icao>-<type>-<designation>-<multiple code>``, e.g.
``cifp-UC-K2-A-KSBA-B`` is the KSBA Class C outer shelf.

Encodings follow openAIP's own airspace schema (fetched live from
api.core.openaip.net/api/schemas/response/airspace/airspace-schema.json,
2026-10-31), stored as text the way the openAIP builder stores them:

* ``icao_class``: ``1``/``2``/``3`` for Class B/C/D; ``8`` (unclassified /
  SUA) for every ``UR`` row.
* ``type``: only the exact equivalents are mapped -- restricted ``1``,
  prohibited ``3``, alert ``17``, warning ``18``, danger ``2``. Everything
  else (Class B/C/D, MOA, ARINC's "unspecified" restrictive type) is ``0``
  (Other); the ARINC type letter and its label are in ``properties_json``
  (``airspace_type``, ``airspace_type_label``), never guessed into a
  near-miss openAIP code.
* limits: unit ``1`` feet or ``6`` flight level; reference ``0`` GND (which
  is what openAIP calls AGL), ``1`` MSL, ``2`` STD. ``GND`` is value ``0``,
  unit ``1``, reference ``0``. ``UNLTD`` has no openAIP encoding: it is
  stored as the value ``UNLTD`` with unit and reference NULL.

``activity`` is NULL: openAIP's activity enum is ENR 5.5 sporting activity,
and nothing in CIFP maps to it. A ``UR`` controlling agency goes into
``remarks``. Every ARINC field, decoded, and every source record verbatim,
are in ``properties_json``.

Boundaries: ARINC 424 gives each vertex a "boundary via" -- ``G`` great
circle, ``H`` rhumb line, ``R``/``L`` clockwise/counter-clockwise arc to the
next vertex about the arc origin, ``C`` a full circle, with ``E`` closing back
to the first vertex. Arcs and circles are densified so no chord strays more
than :data:`ARC_TOLERANCE_NM` from the true arc, and great-circle edges
longer than :data:`GC_STEP_NM` get intermediate points; the arc origin,
radius and bearing stay in ``properties_json.boundary`` so a renderer can
draw true arcs. An arc's radius is interpolated from the origin->start to
the origin->end distance, so the ring passes exactly through every published
vertex: the published arc distance agrees with those to within 0.94 NM on
all 2,246 arcs in 2611 (the worst are 125-240 NM Hawaii warning areas;
spherical vs. ellipsoidal distance).

Area policy: every record's ICAO region must be one :data:`US_ICAO_REGIONS`
names. An unmapped region fails the build -- a new region is a policy
decision for a person, not something to ship or drop silently. In 2611 every
section-U record is in one of them (Alaska, Hawaii, Guam/CNMI, Puerto Rico
and the USVI included), so no record is filtered.
"""

from __future__ import annotations

import json
import math
import sqlite3
from itertools import groupby
from pathlib import Path

from .. import arinc424
from ..geo import EARTH_RADIUS_NM, great_circle_nm
from .airspace import _COLUMNS, _SCHEMA
from .errors import BuildError

#: Largest distance a densified arc chord may stray from the true arc.
ARC_TOLERANCE_NM = 0.01
#: Longest great-circle edge left as a single straight segment.
GC_STEP_NM = 10.0
#: Coarsest angular step along an arc, whatever its radius.
_MAX_ARC_STEP_DEG = 10.0

#: ICAO regions (cols 7-8) that are US airspace. K1-K7 CONUS, PA Alaska, PH
#: Hawaii, PG Guam/CNMI, TJ Puerto Rico, TI US Virgin Islands.
US_ICAO_REGIONS = frozenset({"K1", "K2", "K3", "K4", "K5", "K6", "K7",
                             "PA", "PH", "PG", "TJ", "TI"})

_UC_CLASS = {"B": "1", "C": "2", "D": "3"}
_UC_TYPE_LABEL = {"T": "Class B", "A": "Class C", "Z": "Class D"}
_UR_TYPE = {"R": "1", "P": "3", "A": "17", "W": "18", "D": "2"}
_UR_TYPE_LABEL = {"A": "Alert", "C": "Caution", "D": "Danger", "M": "MOA",
                  "P": "Prohibited", "R": "Restricted", "T": "Training",
                  "W": "Warning", "U": "Unspecified"}
_SUA_CLASS = "8"
_OTHER_TYPE = "0"
_FT, _FL = "1", "6"
_GND, _MSL, _STD = "0", "1", "2"


class AirspaceCifpBuildError(BuildError):
    """A refusal (bad or empty input). A BuildError, so run_cyclical
    fails the run rather than logging it as a benign WARN."""


# --- geodesy (spherical, matching geo.great_circle_nm) ---------------------

def _bearing(p, q) -> float:
    la1, lo1, la2, lo2 = (math.radians(v) for v in (*p, *q))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360.0


def _destination(p, bearing_deg: float, dist_nm: float) -> tuple[float, float]:
    la1, lo1 = math.radians(p[0]), math.radians(p[1])
    b, d = math.radians(bearing_deg), dist_nm / EARTH_RADIUS_NM
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(la1),
                           math.cos(d) - math.sin(la1) * math.sin(la2))
    return (math.degrees(la2), (math.degrees(lo2) + 540.0) % 360.0 - 180.0)


def _arc_step_deg(radius_nm: float) -> float:
    if radius_nm <= ARC_TOLERANCE_NM:
        return _MAX_ARC_STEP_DEG
    return min(_MAX_ARC_STEP_DEG,
               math.degrees(2 * math.acos(1 - ARC_TOLERANCE_NM / radius_nm)))


def _arc_points(origin, start, end, clockwise: bool) -> list[tuple[float, float]]:
    """Interior points of the arc from ``start`` to ``end`` about ``origin``
    (both endpoints excluded)."""
    b0, b1 = _bearing(origin, start), _bearing(origin, end)
    r0, r1 = great_circle_nm(origin, start), great_circle_nm(origin, end)
    sweep = (b1 - b0) % 360.0 if clockwise else -((b0 - b1) % 360.0)
    n = max(1, math.ceil(abs(sweep) / _arc_step_deg(max(r0, r1))))
    return [_destination(origin, b0 + sweep * i / n, r0 + (r1 - r0) * i / n)
            for i in range(1, n)]


def _circle(origin, radius_nm: float) -> list[tuple[float, float]]:
    n = max(8, math.ceil(360.0 / _arc_step_deg(radius_nm)))
    return [_destination(origin, 360.0 * i / n, radius_nm) for i in range(n)]


def _gc_points(p, q) -> list[tuple[float, float]]:
    """Interior points of the great circle p -> q, at most GC_STEP_NM apart
    (spherical linear interpolation)."""
    d = great_circle_nm(p, q)
    n = math.ceil(d / GC_STEP_NM)
    if n <= 1:
        return []
    la1, lo1, la2, lo2 = (math.radians(v) for v in (*p, *q))
    a = (math.cos(la1) * math.cos(lo1), math.cos(la1) * math.sin(lo1), math.sin(la1))
    b = (math.cos(la2) * math.cos(lo2), math.cos(la2) * math.sin(lo2), math.sin(la2))
    delta = d / EARTH_RADIUS_NM
    out = []
    for i in range(1, n):
        f = i / n
        ka, kb = math.sin((1 - f) * delta) / math.sin(delta), math.sin(f * delta) / math.sin(delta)
        x, y, z = (ka * a[j] + kb * b[j] for j in range(3))
        out.append((math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))))
    return out


# --- one sector -> one row --------------------------------------------------

def _limit(raw: str | None, unit: str | None, where: str):
    """ARINC lower/upper limit -> (value, unit, reference) in openAIP codes."""
    if raw is None:
        raise AirspaceCifpBuildError(f"{where}: no vertical limit on the sector's first record")
    if raw == "GND":
        return "0", _FT, _GND
    if raw == "UNLTD":
        return "UNLTD", None, None
    if raw.startswith("FL") and raw[2:].isdigit():
        return str(int(raw[2:])), _FL, _STD
    if raw.isdigit() and unit in ("A", "M"):
        return str(int(raw)), _FT, _GND if unit == "A" else _MSL
    raise AirspaceCifpBuildError(f"{where}: unrecognised vertical limit {raw!r} unit {unit!r}")


def _drop_spikes(coords: list[list[float]]) -> tuple[list[list[float]], int]:
    """Drop repeated vertices and exact out-and-back spikes (A, B, A -> A).

    The FAA source has a few: KOKC Class C retraces 35.37719/35.37716/
    35.37719 N on one meridian, a few metres out and back, and four other
    2611 sectors do the same. A ring that doubles back on itself is not a
    simple polygon (ASP-001); the published vertices stay verbatim in
    ``properties_json``. Returns the open ring and how many spikes went."""
    out: list[list[float]] = []
    spikes = 0
    for q in coords:
        if out and q == out[-1]:
            continue
        if len(out) >= 2 and q == out[-2]:
            out.pop()
            spikes += 1
            continue
        out.append(q)
    # The same across the closing vertex.
    while len(out) >= 3 and (out[-1] == out[0] or out[-2] == out[0] or out[1] == out[-1]):
        if out[-1] == out[0]:
            out.pop()
        elif out[-2] == out[0]:
            out.pop()
            out.pop()
            spikes += 1
        else:
            out.pop(0)
            out.pop(0)
            spikes += 1
    return out, spikes


def _ring(recs: list[arinc424.AirspaceRecord], where: str):
    ring: list[tuple[float, float]] = []
    for i, r in enumerate(recs):
        via = r.boundary_via[:1]
        if via == "C":
            if len(recs) != 1 or r.arc_origin_lat is None or not r.arc_distance_nm:
                raise AirspaceCifpBuildError(f"{where}: a circle must be the sector's only "
                                             "record and carry an origin and radius")
            ring = _circle((r.arc_origin_lat, r.arc_origin_lon), r.arc_distance_nm)
            break
        if r.lat is None:
            raise AirspaceCifpBuildError(f"{where} seq {r.seq}: vertex has no position")
        p = (r.lat, r.lon)
        nxt = recs[0] if r.boundary_via.endswith("E") else recs[i + 1]
        q = (nxt.lat, nxt.lon)
        ring.append(p)
        if via == "G":
            ring += _gc_points(p, q)
        elif via == "H":
            pass
        elif via in ("L", "R"):
            if r.arc_origin_lat is None:
                raise AirspaceCifpBuildError(f"{where} seq {r.seq}: arc with no origin")
            ring += _arc_points((r.arc_origin_lat, r.arc_origin_lon), p, q,
                                clockwise=via == "R")
        else:
            raise AirspaceCifpBuildError(f"{where} seq {r.seq}: unknown boundary via "
                                         f"{r.boundary_via!r}")
    coords, spikes = _drop_spikes([[round(lon, 7), round(lat, 7)] for lat, lon in ring])
    coords.append(list(coords[0]))
    for a, b in zip(coords, coords[1:]):
        if abs(a[0] - b[0]) > 180:
            raise AirspaceCifpBuildError(f"{where}: ring crosses the antimeridian")
    # RFC 7946 s3.1.6: exterior rings are counter-clockwise.
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(coords, coords[1:]))
    if area2 < 0:
        coords.reverse()
    return coords, spikes


#: One ``properties_json.boundary`` row per vertex, in this column order.
_BOUNDARY_FIELDS = ("seq", "boundary_via", "lat", "lon", "arc_origin_lat", "arc_origin_lon",
                    "arc_distance_nm", "arc_bearing", "rnp", "file_record", "cycle")


def _sector_row(recs: list[arinc424.AirspaceRecord]) -> tuple:
    first, last = recs[0], recs[-1]
    where = "{} {} {} {} {}".format(*first.sector_key)
    if first.icao not in US_ICAO_REGIONS:
        raise AirspaceCifpBuildError(
            f"{where}: ICAO region {first.icao!r} is not in US_ICAO_REGIONS; decide whether "
            "this pack should carry it before building")
    if not last.boundary_via.endswith("E") or any(r.boundary_via.endswith("E")
                                                  for r in recs[:-1]):
        raise AirspaceCifpBuildError(f"{where}: the sector does not end on exactly one "
                                     "closing (E) record")
    seqs = [r.seq for r in recs]
    if seqs != sorted(set(seqs)):
        raise AirspaceCifpBuildError(f"{where}: sequence numbers out of order")
    controlled = first.section == "UC"
    if controlled:
        icao_class = _UC_CLASS.get(first.classification or "")
        if icao_class is None:
            raise AirspaceCifpBuildError(f"{where}: unknown airspace classification "
                                         f"{first.classification!r}")
        type_code, label = _OTHER_TYPE, _UC_TYPE_LABEL.get(first.airspace_type)
    else:
        icao_class = _SUA_CLASS
        type_code = _UR_TYPE.get(first.airspace_type, _OTHER_TYPE)
        label = _UR_TYPE_LABEL.get(first.airspace_type)
    if label is None:
        raise AirspaceCifpBuildError(f"{where}: unknown airspace type {first.airspace_type!r}")
    lower = _limit(first.lower_limit, first.lower_unit, where)
    upper = _limit(first.upper_limit, first.upper_unit, where)
    ring, spikes = _ring(recs, where)
    geometry = {"type": "Polygon", "coordinates": [ring]}
    agency = next((r.controlling_agency for r in recs if r.controlling_agency), None)
    props = {
        "source": "FAA CIFP",
        "section": first.section,
        "area": first.area,
        "icao": first.icao,
        "airspace_type": first.airspace_type,
        "airspace_type_label": label,
        "designation": first.designation,
        "centre_section": first.centre_section,
        "centre_subsection": first.centre_subsection,
        "classification": first.classification,
        "multiple_code": first.multiple_code,
        "level": first.level,
        "time_code": first.time_code,
        "notam": first.notam,
        "controlling_agency": agency,
        "lower_limit": first.lower_limit, "lower_unit": first.lower_unit,
        "upper_limit": first.upper_limit, "upper_unit": first.upper_unit,
        "densify": {"arc_tolerance_nm": ARC_TOLERANCE_NM, "gc_step_nm": GC_STEP_NM},
        "spikes_removed": spikes,
        # GND to 0 AGL: in 2611 these 16 sectors mark an area cut out of a
        # sibling sector (R-7201A's B is the 3 NM hole ADDS draws as an
        # interior ring), not airspace in their own right.
        "zero_height": lower == upper,
        "boundary_fields": list(_BOUNDARY_FIELDS),
        "boundary": [[getattr(r, f) for f in _BOUNDARY_FIELDS] for r in recs],
        "records": [r.raw for r in recs],
    }
    row_id = "cifp-{}-{}-{}-{}-{}".format(*first.sector_key)
    return (
        row_id,
        first.name,
        type_code,
        icao_class,
        "US",
        *lower,
        *upper,
        None,
        f"Controlling agency: {agency}" if agency else None,
        json.dumps(geometry, separators=(",", ":")),
        max(r.cycle or "" for r in recs) or None,
        json.dumps(props, separators=(",", ":"), sort_keys=True),
    )


def iter_sectors(path: str | Path):
    """Group the file's section-U records into sectors, in source order."""
    yield from (list(g) for _k, g in groupby(arinc424.iter_airspace_records(path),
                                             key=lambda r: r.sector_key))


def _find_cifp_file(input_dir: Path) -> Path:
    candidate = input_dir / "FAACIFP18"
    if candidate.exists():
        return candidate
    hits = sorted(input_dir.rglob("FAACIFP18"))
    if hits:
        return hits[0]
    raise AirspaceCifpBuildError(
        f"no FAACIFP18 file found under {input_dir} -- expected the CIFP zip to have "
        "been extracted there.")


def build_airspace_cifp(input_dir: str | Path, out_path: str | Path) -> Path:
    """Build the ``airspaces`` table from an extracted CIFP zip. Matches the
    ``Builder = Callable[[Path, Path], Path]`` shape of
    :data:`packtools.build.BUILDERS`."""
    path = _find_cifp_file(Path(input_dir))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    seen: set[tuple] = set()
    con = sqlite3.connect(str(out_path))
    try:
        con.execute(_SCHEMA)
        placeholders = ",".join("?" * _COLUMNS)
        n = 0
        try:
            for recs in iter_sectors(path):
                key = recs[0].sector_key
                if key in seen:
                    raise AirspaceCifpBuildError("sector {} {} {} {} {} is split across the "
                                                 "file".format(*key))
                seen.add(key)
                con.execute(f"INSERT INTO airspaces VALUES ({placeholders})", _sector_row(recs))
                n += 1
        except ValueError as e:   # arinc424's unhandled-continuation refusal
            raise AirspaceCifpBuildError(str(e)) from e
        con.commit()
    finally:
        con.close()
    if n == 0:
        raise AirspaceCifpBuildError(f"no UC/UR airspace records in {path}")
    return out_path
