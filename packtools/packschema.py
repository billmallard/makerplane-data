# SPDX-License-Identifier: Apache-2.0
"""Capture a pack's format from the pack itself: ``schema/<kind>.json``.

makerplane-data#137 (SCH1) publishes the pack formats on
navdata.aerocommons.org, generated from built packs rather than written by
hand, because the definitions live in two repos (``packtools/build/`` and the
pyEfis ``tools/`` scripts) and a hand-written page would drift from both.
This module is the capture half: it reads one pack and returns what a reader
of that kind sees. The renderer is a separate step.

For a SQLite pack: every table's ``sqlite_master`` statement verbatim (SQLite
keeps the original ``CREATE`` text, so inline DDL comments survive), ``PRAGMA
table_info`` per table, and every index with its statement. Virtual tables
(R-trees) are marked, and their shadow tables are listed under the virtual
table rather than as tables of their own. For a zip pack (terrain): the
member layout, grouped by name pattern, with counts. Both carry the
``pack_meta`` keys and ``schema_version``, and the pack id and cycle they
came from.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path

from . import packmeta

SCHEMA = "pack-schema/1"


def _sqlite(path: Path) -> tuple[dict, list[str]]:
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        objs = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                           "ORDER BY type, name").fetchall()
        virtual = {n for t, n, _, sql in objs
                   if t == "table" and (sql or "").upper().startswith("CREATE VIRTUAL TABLE")}
        tables = []
        for t, name, _, sql in objs:
            if t != "table" or name.startswith("sqlite_"):
                continue
            shadow_of = next((v for v in virtual if name.startswith(v + "_")), None)
            if shadow_of:
                continue
            cols = [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "default": r[4],
                     "pk": r[5]}
                    for r in con.execute(f'PRAGMA table_info("{name}")')]
            idx = [{"name": n, "sql": s, "unique": bool(re.match(r"\s*CREATE\s+UNIQUE",
                                                                    s or "", re.I))}
                   for tt, n, tbl, s in objs if tt == "index" and tbl == name]
            tables.append({
                "name": name, "sql": sql, "columns": cols, "indexes": idx,
                "virtual": name in virtual,
                **({"shadow_tables": sorted(n for tt, n, _, _ in objs if tt == "table"
                                            and n.startswith(name + "_"))}
                   if name in virtual else {}),
            })
        keys = []
        if any(t == "table" and n == "pack_meta" for t, n, _, _ in objs):
            keys = sorted(r[0] for r in con.execute("SELECT key FROM pack_meta"))
        return {"format": "sqlite", "tables": tables}, keys
    finally:
        con.close()


def _pattern(name: str) -> str:
    """A member name with each run of digits as ``#``, and N/S/E/W hemisphere
    letters kept: ``N39W105.hgt`` -> ``N#W#.hgt``."""
    return re.sub(r"\d+", "#", name)


def _zip(path: Path) -> tuple[dict, list[str]]:
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        keys = []
        if packmeta._ZIP_META_NAME in names:
            keys = sorted(json.loads(z.read(packmeta._ZIP_META_NAME)))
    pats = Counter(_pattern(n) for n in names)
    return {"format": "zip", "members": len(names),
            "layout": [{"pattern": p, "count": n} for p, n in sorted(pats.items())]}, keys


def capture(path: str | Path) -> dict:
    """The format of one pack, as a ``pack-schema/1`` object."""
    path = Path(path)
    meta = packmeta.read(path)
    body, keys = _zip(path) if zipfile.is_zipfile(path) else _sqlite(path)
    return {"schema": SCHEMA, "kind": meta.kind,
            "from": {"id": meta.id, "cycle": meta.cycle},
            "schema_version": str(meta.schema_version),
            "pack_meta_keys": keys, **body}


def to_bytes(obj: dict) -> bytes:
    """Canonical bytes (as ``report.to_bytes``): unchanged formats give
    unchanged bytes, so nothing re-uploads."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def key_for(kind: str) -> str:
    return f"schema/{kind}.json"
