# SPDX-License-Identifier: Apache-2.0
"""Golden facts: published values a pack is compared against (GLD-001).

The facts live in ``packtools/data/golden_facts.yaml``, a file anyone can
extend by pull request (docs/golden_facts.md). Each fact names one value
in one kind of pack, the published source it came from, and how close the
pack must be. :func:`load` validates the file; :func:`check` looks each
fact up in an open pack.

The set of fact types is closed: each maps to one fixed query below, so a
contributor adds data, never SQL.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent / "data" / "golden_facts.yaml"

#: fact type -> (pack kind, key fields, SQL taking the key fields in order).
#: Every query returns the candidate values; more than one row is an
#: ambiguous key, which the fact reports instead of guessing.
FACT_TYPES = {
    "airport_elevation_ft": (
        "navdata", ("airport",),
        "SELECT elev_ft FROM airports WHERE icao = ?"),
    "runway_length_ft": (
        "navdata", ("airport", "runway"),
        "SELECT r.length_ft FROM runways r JOIN airports a USING (site_no) "
        "WHERE a.icao = ? AND r.rwy_id = ?"),
    "navaid_frequency_mhz": (
        "navaids", ("navaid", "type"),
        "SELECT CAST(freq AS REAL) FROM navaids WHERE id = ? AND type = ?"),
    "navaid_elevation_ft": (
        "navaids", ("navaid", "type"),
        "SELECT elev_ft FROM navaids WHERE id = ? AND type = ?"),
    "obstacle_amsl_ft": (
        "obstacles", ("oas_no",),
        "SELECT amsl_ft FROM obstacles WHERE oas_no = ?"),
}

REQUIRED = ("id", "kind", "fact", "key", "value", "tolerance", "source", "source_url", "added")


class FactsError(ValueError):
    """The golden facts file is malformed."""


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    fact: str
    key: tuple
    value: float
    tolerance: float
    source: str
    source_url: str
    added: str


def load(path: str | Path | None = None) -> list[Fact]:
    """Every fact in the file, validated. Raises :class:`FactsError`
    naming each problem, so a bad pull request fails its tests rather
    than a build."""
    raw = yaml.safe_load(Path(path or DATA).read_text(encoding="utf-8")) or {}
    items = raw.get("facts")
    if not isinstance(items, list):
        raise FactsError("the file needs a top-level 'facts:' list")
    out, errs, seen = [], [], set()
    for i, f in enumerate(items):
        where = f"fact {i + 1} ({(f or {}).get('id', '?')})"
        if not isinstance(f, dict):
            errs.append(f"{where}: not a mapping")
            continue
        missing = [k for k in REQUIRED if f.get(k) in (None, "")]
        if missing:
            errs.append(f"{where}: missing {', '.join(missing)}")
            continue
        extra = sorted(set(f) - set(REQUIRED))
        if extra:
            errs.append(f"{where}: unknown field {', '.join(extra)}")
        if f["id"] in seen:
            errs.append(f"{where}: id used twice")
        seen.add(f["id"])
        spec = FACT_TYPES.get(f["fact"])
        if spec is None:
            errs.append(f"{where}: fact type {f['fact']!r} is not one of {sorted(FACT_TYPES)}")
            continue
        kind, fields, _sql = spec
        if f["kind"] != kind:
            errs.append(f"{where}: {f['fact']} belongs to kind {kind}, not {f['kind']}")
        key = f["key"] if isinstance(f["key"], dict) else {}
        if sorted(key) != sorted(fields):
            errs.append(f"{where}: key needs exactly {', '.join(fields)}")
            continue
        try:
            value, tol = float(f["value"]), float(f["tolerance"])
        except (TypeError, ValueError):
            errs.append(f"{where}: value and tolerance must be numbers")
            continue
        if tol < 0:
            errs.append(f"{where}: tolerance is negative")
        if not str(f["source_url"]).startswith("https://"):
            errs.append(f"{where}: source_url must be an https:// link to the published source")
        added = f["added"]
        if isinstance(added, _dt.date):
            added = added.isoformat()
        out.append(Fact(str(f["id"]), kind, f["fact"], tuple(str(key[k]) for k in fields),
                        value, tol, str(f["source"]), str(f["source_url"]), str(added)))
    if errs:
        raise FactsError("; ".join(errs))
    return out


def for_kind(kind: str, facts: list[Fact]) -> list[Fact]:
    return [f for f in facts if f.kind == kind]


def check(con: sqlite3.Connection, facts: list[Fact]) -> list[dict]:
    """One result per fact: ``outcome`` is ``match``, ``differs``,
    ``not found`` or ``ambiguous`` (the key names more than one row)."""
    out = []
    for f in facts:
        _kind, _fields, sql = FACT_TYPES[f.fact]
        try:
            vals = [r[0] for r in con.execute(sql, f.key).fetchall()]
        except sqlite3.Error as e:
            vals, err = [], str(e)
        else:
            err = ""
        row = {"id": f.id, "expected": f.value, "tolerance": f.tolerance}
        if err:
            row.update(outcome="not found", found=None, error=err)
        elif not vals or vals[0] is None:
            row.update(outcome="not found", found=None)
        elif len(vals) > 1:
            row.update(outcome="ambiguous", found=vals[:5])
        else:
            got = float(vals[0])
            row.update(found=got,
                       outcome="match" if abs(got - f.value) <= f.tolerance + 1e-9 else "differs")
        out.append(row)
    return out
