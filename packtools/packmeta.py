# SPDX-License-Identifier: Apache-2.0
"""pack_meta — the self-describing header carried *inside* every pack.

A pack must identify itself even when it is separated from the manifest
(e.g. a lone file copied onto a USB stick). Two physical pack containers,
one logical metadata schema:

  * **sqlite packs** (navdata, water): a ``pack_meta(key, value)`` table.
    pyEfis's DB loaders already tolerate-and-ignore unknown tables, so this
    is invisible to the renderer.
  * **zip packs** (terrain tiles, later charts, CIFP file-sets): a
    ``pack_meta.json`` member alongside the payload.

The manifest entry for a pack is a superset of pack_meta (it adds url,
bytes, sha256, regions). pack_meta is the subset that is intrinsic to the
file itself.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

#: Bump when the *meaning* of pack_meta fields changes in a breaking way.
SCHEMA_VERSION = 1

# Recognised pack kinds. "kind" drives which build tool produced the pack and
# how the Pi installs it; it is open for extension (charts, etc.).
#
# "plates" (AER-1610/PA11): FAA d-TPP approach-plate PDFs, one pack per
# packtools/regions.yaml region (docs/dtpp_plates_spike.md §3) -- NOT yet
# merged to main and NOT wired into packtools.sources.SOURCES, so it cannot
# reach the nightly cyclical cron yet. Per the repo rule (CLAUDE.md "Deploy /
# publish notes"), a new kind must merge to `dev` -- the branch devices
# actually track -- before its first R2 publish; promoting this kind to
# `main` and enabling publish is a separate, later step.
KINDS = ("navdata", "obstacles", "cifp", "water", "terrain", "highways",
         "rivers", "airports", "navaids", "airspace", "procedures", "plates")


@dataclass
class PackMeta:
    id: str                       # stable pack id, e.g. "obstacles-conus"
    kind: str                     # one of KINDS
    cycle: str                    # AIRAC id ("2606") or YYMMDD or edition tag
    effective: str | None = None  # ISO date; None => non-cyclical (terrain)
    expires: str | None = None    # ISO date (exclusive); None => never expires
    attribution: str = ""
    # license/license_url are additive and independent of attribution:
    # attribution is presentation text (what to show a pilot); license is a
    # short machine-checkable tag (SPDX id, "LicenseRef-*", or e.g. "ODbL-1.0")
    # and license_url the canonical link to its full text. "" means
    # unspecified -- today's implicit default for every existing source.
    license: str = ""
    license_url: str = ""
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown pack kind {self.kind!r}; expected one of {KINDS}")

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "PackMeta":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# --- sqlite packs ---------------------------------------------------------

def embed_sqlite(path: str | Path, meta: PackMeta) -> None:
    """(Re)write the pack_meta table inside an sqlite pack in place."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS pack_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("DELETE FROM pack_meta")
        con.executemany(
            "INSERT INTO pack_meta (key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in meta.as_dict().items()],
        )
        con.commit()
    finally:
        con.close()


def read_sqlite(path: str | Path) -> PackMeta:
    con = sqlite3.connect(str(path))
    try:
        rows = con.execute("SELECT key, value FROM pack_meta").fetchall()
    finally:
        con.close()
    d: dict = {k: v for k, v in rows}
    if "schema_version" in d:
        d["schema_version"] = int(d["schema_version"])
    return PackMeta.from_dict(d)


# --- zip packs ------------------------------------------------------------

_ZIP_META_NAME = "pack_meta.json"


def embed_zip(path: str | Path, meta: PackMeta) -> None:
    """Add/replace pack_meta.json inside an existing zip pack."""
    path = Path(path)
    # zipfile cannot rewrite a member in place; copy through a temp file.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as zin, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == _ZIP_META_NAME:
                continue
            zout.writestr(item, zin.read(item.filename))
        zout.writestr(_ZIP_META_NAME, json.dumps(meta.as_dict(), indent=2))
    tmp.replace(path)


def read_zip(path: str | Path) -> PackMeta:
    with zipfile.ZipFile(path, "r") as z:
        return PackMeta.from_dict(json.loads(z.read(_ZIP_META_NAME)))


# --- dispatch -------------------------------------------------------------

def read(path: str | Path) -> PackMeta:
    """Read pack_meta from either container type, detected by content."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        return read_zip(path)
    return read_sqlite(path)


# --- per-kind on-disk schema detection -------------------------------------
#
# ``PackMeta.schema_version`` above tracks the *meta header's* own fields.
# For the "highways" kind that field also has to speak for the on-disk
# highway_lines table -- the pack's actual payload schema -- because
# pyEfis's HighwayDB reads that table directly. That table's columns changed
# once already (AER-623/RD3a added flags+ref) without the embedded
# schema_version ever moving, since build-pack always defaulted to the
# package-wide SCHEMA_VERSION constant regardless of kind (AER-1715): two
# published highways packs with different highway_lines columns both
# declared schema_version 1. HighwayDB copes today by probing
# PRAGMA table_info at open time rather than trusting the field, so nothing
# is broken by the drift, but the field can't be gated on until it actually
# moves with the schema. HIGHWAYS_TABLE_SCHEMAS maps the highway_lines
# *column set* that tools/build_highway_db.py (pyEfis) is known to have
# produced to the schema_version that shape should carry.
#
# Deliberately column-based, not row-content-based: which fclass values a
# given build happens to contain (e.g. a state-limited build with no
# primary/secondary roads in it) is a data-completeness question, not a
# schema one, and gating on it would make an ordinary partial build fail to
# pack. Column presence is also exactly what HighwayDB._has_flags_ref
# already probes, so this mirrors the one signal a reader can actually gate
# on today. Keyed on the full column set (not just flags/ref) so a future
# column addition this map doesn't yet know about fails loud instead of
# silently reusing the wrong version.
HIGHWAYS_TABLE_SCHEMAS: dict[frozenset[str], int] = {
    frozenset({"id", "fclass", "min_lat", "max_lat", "min_lon", "max_lon",
               "verts"}): 1,   # AER-623-era shape: no flags/ref columns
    frozenset({"id", "fclass", "min_lat", "max_lat", "min_lon", "max_lon",
               "verts", "flags", "ref"}): 2,  # RD3a, pyEfis PR #165
}


def detect_highways_schema_version(path: str | Path) -> int:
    """Introspect a built highways sqlite's ``highway_lines`` columns and
    return the schema_version that shape corresponds to (AER-1715).

    Raises on a column set we don't recognise -- silently labeling an
    unknown shape would just move the drift this exists to close, rather
    than close it; add the new shape to HIGHWAYS_TABLE_SCHEMAS (with the
    next version number) before packing."""
    con = sqlite3.connect(str(path))
    try:
        cols = frozenset(row[1] for row in con.execute("PRAGMA table_info(highway_lines)"))
    finally:
        con.close()
    try:
        return HIGHWAYS_TABLE_SCHEMAS[cols]
    except KeyError:
        raise ValueError(
            f"unrecognised highway_lines schema in {path} (columns: "
            f"{sorted(cols)}) -- add this shape to HIGHWAYS_TABLE_SCHEMAS "
            "(packtools/packmeta.py) with the next schema_version before "
            "packing (AER-1715)")
